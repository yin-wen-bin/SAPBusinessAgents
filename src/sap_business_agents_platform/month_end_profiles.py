from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import uuid
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .month_end import ALLOWED_EVIDENCE_SOURCES


JsonObject = dict[str, Any]
ENABLE_CONFIRMATION = "month-end-profile-enable"
PACKAGE_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class MonthEndProfileError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "month_end_profile_error",
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def profile_digest(profile: JsonObject) -> str:
    normalized = deepcopy(profile)
    normalized.setdefault("enabled", True)
    return canonical_digest(normalized)


def verification_digest(profile: JsonObject) -> str:
    """Identity of SAP-relevant content, excluding local lifecycle fields."""

    normalized = deepcopy(profile)
    normalized.pop("version", None)
    normalized.pop("enabled", None)
    return canonical_digest(normalized)


def default_profile_path(data_root: Path) -> Path:
    return data_root.resolve() / "config" / "month-end-closing" / "profiles.json"


def configured_profile_path(repository_root: Path, data_root: Path) -> tuple[Path, bool]:
    configured = os.getenv("SAPBA_MONTH_END_PROFILE_PATH", "").strip()
    if not configured:
        return default_profile_path(data_root), True
    path = Path(configured)
    resolved = (path if path.is_absolute() else repository_root / path).resolve()
    return resolved, False


class MonthEndProfileService:
    def __init__(
        self,
        *,
        repository_root: Path,
        data_root: Path,
        database_path: Path,
        sap_client: str,
        sap_read: Any,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.data_root = data_root.resolve()
        self.path, self.editable = configured_profile_path(
            self.repository_root, self.data_root
        )
        configured_schema = (
            self.repository_root / "config" / "month-end-closing-profiles.schema.json"
        )
        self.schema_path = (
            configured_schema
            if configured_schema.is_file()
            else PACKAGE_REPOSITORY_ROOT
            / "config"
            / "month-end-closing-profiles.schema.json"
        )
        self.sap_client = str(sap_client or "").strip()
        self.system_alias = os.getenv("SAPBA_SAP_SYSTEM_ALIAS", "default").strip() or "default"
        self.sap_read = sap_read
        self.database_path = database_path.resolve()
        self._lock = threading.RLock()
        self._schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        self._validator = Draft202012Validator(
            self._schema, format_checker=FormatChecker()
        )
        self._initialize_database()

    def registry(self) -> JsonObject:
        with self._lock:
            registry, diagnostics, exists = self._read_registry()
            response_profiles: list[JsonObject] = []
            if not diagnostics:
                for stored in registry["profiles"]:
                    profile = deepcopy(stored)
                    profile.setdefault("enabled", True)
                    digest = profile_digest(profile)
                    verified = self._verification_for(
                        str(profile.get("profile_id") or ""),
                        verification_digest(profile),
                    )
                    response_profiles.append(
                        {
                            "profile": profile,
                            "profile_id": str(profile.get("profile_id") or ""),
                            "version": str(profile.get("version") or ""),
                            "enabled": bool(profile.get("enabled", True)),
                            "system_alias": str(profile.get("system_alias") or "default"),
                            "sap_client": str(profile.get("sap_client") or ""),
                            "company_code": str(profile.get("company_code") or ""),
                            "ledger": str(profile.get("ledger") or ""),
                            "effective_from": str(profile.get("effective_from") or ""),
                            "effective_to": str(profile.get("effective_to") or ""),
                            "profile_digest": digest,
                            "verification_digest": verification_digest(profile),
                            "validation": verified
                            or {
                                "status": "not_validated",
                                "verified_at": None,
                                "provider": None,
                            },
                        }
                    )
            return {
                "schema_version": 1,
                "registry_digest": canonical_digest(registry) if not diagnostics else None,
                "registry_exists": exists,
                "registry_valid": not diagnostics,
                "editable": self.editable and not diagnostics,
                "read_only_code": (
                    None
                    if self.editable
                    else "month_end_profile_registry_external_read_only"
                ),
                "restart_required": False,
                "runtime_scope": {
                    "system_alias": self.system_alias,
                    "sap_client": self.sap_client,
                },
                "profiles": response_profiles,
                "errors": diagnostics,
            }

    async def validate(
        self, profile: JsonObject, *, online: bool, actor: str = "local-user"
    ) -> JsonObject:
        candidate = self._normalize_profile(profile)
        errors = self._static_errors(candidate)
        registry, registry_errors, _ = self._read_registry()
        if not registry_errors:
            errors.extend(self._overlap_errors(candidate, registry["profiles"], replacing_id=str(candidate.get("profile_id") or "")))
        result: JsonObject = {
            "valid": not errors,
            "errors": errors,
            "profile": candidate,
            "profile_digest": profile_digest(candidate),
            "verification_digest": verification_digest(candidate),
            "online_validation": {"status": "not_requested"},
            "can_enable": False,
            "restart_required": False,
        }
        if errors or not online:
            return result

        online_result = await self._online_validate(candidate)
        result["online_validation"] = online_result
        if online_result.get("status") == "validated":
            self._record_verification(candidate, online_result, actor)
            digest = profile_digest(candidate)
            self._audit(
                str(candidate.get("profile_id") or ""),
                "online_validate",
                digest,
                digest,
                actor,
            )
            result["can_enable"] = True
        elif online_result.get("status") == "invalid":
            result["valid"] = False
            result["errors"] = self._deduplicate_errors(
                [*result["errors"], *(online_result.get("errors") or [])]
            )
        return result

    def create(
        self,
        profile: JsonObject,
        *,
        expected_registry_digest: str,
        actor: str,
    ) -> JsonObject:
        with self._lock:
            self._require_editable()
            registry, errors, _ = self._read_registry()
            self._require_valid_registry(errors)
            self._check_registry_digest(registry, expected_registry_digest)
            candidate = self._normalize_profile(profile)
            candidate["version"] = "1.0.0"
            candidate["enabled"] = False
            profile_id = str(candidate.get("profile_id") or "")
            if any(str(item.get("profile_id") or "") == profile_id for item in registry["profiles"]):
                raise MonthEndProfileError(
                    "A month-end profile with this id already exists.",
                    code="month_end_profile_conflict",
                    detail={"errors": [self._field_error("profile_id", "duplicate", "Profile ID already exists.")]},
                )
            validation_errors = self._static_errors(candidate)
            if validation_errors:
                self._raise_invalid(validation_errors)
            registry["profiles"].append(candidate)
            self._write_registry(registry)
            digest = profile_digest(candidate)
            self._audit(profile_id, "create", None, digest, actor)
            return self._mutation_response(registry, candidate)

    def update(
        self,
        profile_id: str,
        profile: JsonObject,
        *,
        expected_registry_digest: str,
        expected_profile_digest: str,
        actor: str,
    ) -> JsonObject:
        with self._lock:
            self._require_editable()
            registry, errors, _ = self._read_registry()
            self._require_valid_registry(errors)
            self._check_registry_digest(registry, expected_registry_digest)
            index, current = self._find_profile(registry, profile_id)
            old_digest = profile_digest(current)
            if old_digest != expected_profile_digest:
                self._conflict("The profile changed after this page was loaded.")
            candidate = self._normalize_profile(profile)
            if str(candidate.get("profile_id") or "") != profile_id:
                self._raise_invalid([
                    self._field_error("profile_id", "immutable", "Profile ID cannot be changed.")
                ])
            content_changed = self._business_content_changed(current, candidate)
            candidate["enabled"] = False if content_changed else bool(current.get("enabled", True))
            comparable_current = self._normalize_profile(current)
            comparable_current.pop("version", None)
            comparable_candidate = deepcopy(candidate)
            comparable_candidate.pop("version", None)
            if canonical_digest(comparable_current) == canonical_digest(comparable_candidate):
                return self._mutation_response(registry, current)
            candidate["version"] = self._next_version(str(current.get("version") or "1.0.0"))
            validation_errors = self._static_errors(candidate)
            validation_errors.extend(self._overlap_errors(candidate, registry["profiles"], replacing_id=profile_id))
            if validation_errors:
                self._raise_invalid(validation_errors)
            registry["profiles"][index] = candidate
            self._write_registry(registry)
            digest = profile_digest(candidate)
            self._audit(profile_id, "update", old_digest, digest, actor)
            return self._mutation_response(registry, candidate)

    def set_enabled(
        self,
        profile_id: str,
        enabled: bool,
        *,
        expected_registry_digest: str,
        expected_profile_digest: str,
        actor: str,
        confirmation: str | None,
    ) -> JsonObject:
        with self._lock:
            self._require_editable()
            registry, errors, _ = self._read_registry()
            self._require_valid_registry(errors)
            self._check_registry_digest(registry, expected_registry_digest)
            index, current = self._find_profile(registry, profile_id)
            old_digest = profile_digest(current)
            if old_digest != expected_profile_digest:
                self._conflict("The profile changed after this page was loaded.")
            currently_enabled = bool(current.get("enabled", True))
            if currently_enabled == enabled:
                return self._mutation_response(registry, current)
            candidate = self._normalize_profile(current)
            candidate["enabled"] = enabled
            candidate["version"] = self._next_version(str(current.get("version") or "1.0.0"))
            validation_errors = self._static_errors(candidate)
            if enabled:
                if confirmation != ENABLE_CONFIRMATION:
                    raise MonthEndProfileError(
                        "Enabling a month-end profile requires explicit confirmation.",
                        code="month_end_profile_confirmation_required",
                    )
                if self._verification_for(profile_id, verification_digest(candidate)) is None:
                    validation_errors.append(
                        self._field_error(
                            "enabled",
                            "online_validation_required",
                            "The current profile content has not passed online SAP metadata validation.",
                        )
                    )
                validation_errors.extend(self._overlap_errors(candidate, registry["profiles"], replacing_id=profile_id))
            if validation_errors:
                self._raise_invalid(validation_errors)
            registry["profiles"][index] = candidate
            self._write_registry(registry)
            digest = profile_digest(candidate)
            self._audit(profile_id, "enable" if enabled else "disable", old_digest, digest, actor)
            return self._mutation_response(registry, candidate)

    def _read_registry(self) -> tuple[JsonObject, list[JsonObject], bool]:
        if not self.path.is_file():
            return {"schema_version": 1, "profiles": []}, [], False
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return (
                {"schema_version": 1, "profiles": []},
                [self._field_error("$", "registry_invalid", f"Cannot read profiles.json: {type(exc).__name__}.")],
                True,
            )
        errors = self._schema_errors(payload)
        if errors:
            return {"schema_version": 1, "profiles": []}, errors, True
        return deepcopy(payload), [], True

    def _schema_errors(self, registry: Any) -> list[JsonObject]:
        result: list[JsonObject] = []
        for error in sorted(self._validator.iter_errors(registry), key=lambda item: list(item.absolute_path)):
            path = ".".join(str(part) for part in error.absolute_path) or "$"
            result.append(self._field_error(path, f"schema_{error.validator}", error.message))
        return result

    def _static_errors(self, profile: JsonObject) -> list[JsonObject]:
        errors = self._schema_errors({"schema_version": 1, "profiles": [profile]})
        for error in errors:
            field = str(error["field"])
            if field.startswith("profiles.0."):
                error["field"] = field[len("profiles.0."):]
            elif field == "profiles.0":
                error["field"] = "$"
        start = self._date(profile.get("effective_from"))
        end = self._date(profile.get("effective_to"))
        if start and end and end < start:
            errors.append(self._field_error("effective_to", "date_order", "Effective-to date cannot be earlier than effective-from date."))
        thresholds = profile.get("thresholds") if isinstance(profile.get("thresholds"), dict) else {}
        age = self._integer(thresholds.get("grir_age_days"))
        high = self._integer(thresholds.get("grir_high_severity_days"))
        if age is not None and high is not None and high < age:
            errors.append(self._field_error("thresholds.grir_high_severity_days", "threshold_order", "High-severity age must be greater than or equal to the normal GR/IR age."))
        boundaries = profile.get("period_boundaries") if isinstance(profile.get("period_boundaries"), dict) else {}
        for key, value in boundaries.items():
            if not isinstance(value, dict):
                continue
            boundary_start = self._date(value.get("start"))
            boundary_end = self._date(value.get("end"))
            if boundary_start and boundary_end and boundary_end < boundary_start:
                errors.append(self._field_error(f"period_boundaries.{key}.end", "date_order", "Period end cannot be earlier than period start."))
        unknown = sorted(set(profile.get("approved_evidence_sources") or []) - ALLOWED_EVIDENCE_SOURCES)
        for source in unknown:
            errors.append(self._field_error("approved_evidence_sources", "unknown_evidence_source", f"Unknown evidence source: {source}."))
        return self._deduplicate_errors(errors)

    def _overlap_errors(
        self,
        candidate: JsonObject,
        profiles: list[JsonObject],
        *,
        replacing_id: str,
    ) -> list[JsonObject]:
        if not bool(candidate.get("enabled", True)):
            return []
        start = self._date(candidate.get("effective_from")) or date.min
        end = self._date(candidate.get("effective_to")) or date.max
        candidate_client = str(candidate.get("sap_client") or "")
        for other in profiles:
            if str(other.get("profile_id") or "") == replacing_id:
                continue
            if not bool(other.get("enabled", True)):
                continue
            if str(other.get("system_alias") or "default") != str(candidate.get("system_alias") or "default"):
                continue
            if str(other.get("company_code") or "") != str(candidate.get("company_code") or ""):
                continue
            other_client = str(other.get("sap_client") or "")
            if candidate_client and other_client and candidate_client != other_client:
                continue
            other_start = self._date(other.get("effective_from")) or date.min
            other_end = self._date(other.get("effective_to")) or date.max
            if max(start, other_start) <= min(end, other_end):
                return [
                    self._field_error(
                        "effective_from",
                        "enabled_effective_range_overlap",
                        f"Enabled profile overlaps {other.get('profile_id')} for the same system, client, and company code.",
                    )
                ]
        return []

    async def _online_validate(self, profile: JsonObject) -> JsonObject:
        if str(profile.get("system_alias") or "") != self.system_alias:
            return {
                "status": "invalid",
                "errors": [self._field_error("system_alias", "runtime_mismatch", "System alias does not match the current runtime.")],
            }
        configured_client = str(profile.get("sap_client") or "")
        if configured_client and configured_client != self.sap_client:
            return {
                "status": "invalid",
                "errors": [self._field_error("sap_client", "runtime_mismatch", "SAP Client does not match the current runtime.")],
            }
        company_code = str(profile.get("company_code") or "")
        company_plan = {
            "service_name": "API_COMPANYCODE_SRV",
            "odata_version": "2.0",
            "entity_set": "A_CompanyCode",
            "http_method": "GET",
            "plan_kind": "direct",
            "step_id": "company_metadata",
            "select_fields": ["CompanyCode", "CompanyCodeName", "Currency", "FiscalYearVariant", "ControllingArea"],
            "filters": [{"field": "CompanyCode", "operator": "eq", "value": company_code, "value_type": "string"}],
            "order_by": ["CompanyCode"],
            "rationale": "Validate exact company-code metadata for a local month-end profile.",
        }
        ledger_plan = {
            "service_name": "API_LEDGER_SRV",
            "odata_version": "2.0",
            "entity_set": "A_Ledger",
            "http_method": "GET",
            "plan_kind": "direct",
            "step_id": "ledger_metadata",
            "select_fields": ["Ledger", "IsLeadingLedger", "LedgerApplication", "LedgerSubApplication"],
            "order_by": ["Ledger"],
            "rationale": "Validate available ledgers for a local month-end profile.",
        }
        try:
            company_payload = await self.sap_read.execute_plan(company_plan, "Validate month-end company metadata")
            ledger_payload = await self.sap_read.execute_plan(ledger_plan, "Validate month-end ledger metadata")
        except Exception as exc:
            return {
                "status": "unavailable",
                "code": "month_end_profile_online_validation_unavailable",
                "message": f"SAP metadata validation is unavailable ({type(exc).__name__}).",
                "errors": [],
            }
        if not self._source_complete(company_payload) or not self._source_complete(ledger_payload):
            return {
                "status": "unavailable",
                "code": "month_end_profile_online_validation_incomplete",
                "message": "SAP metadata validation did not return complete bounded sources.",
                "errors": [],
            }
        company_rows = [row for row in self._rows(company_payload) if str(row.get("CompanyCode") or "") == company_code]
        ledger_rows = self._rows(ledger_payload)
        errors: list[JsonObject] = []
        if len(company_rows) != 1:
            errors.append(self._field_error("company_code", "not_found", "Company code was not returned exactly once by SAP."))
        company = company_rows[0] if len(company_rows) == 1 else {}
        configured_area = str((profile.get("co") or {}).get("controlling_area") or "")
        sap_area = str(company.get("ControllingArea") or "")
        if configured_area and sap_area and configured_area != sap_area:
            errors.append(self._field_error("co.controlling_area", "sap_mismatch", "Controlling area does not match SAP company metadata."))
        available_ledgers = sorted({str(row.get("Ledger") or "") for row in ledger_rows if row.get("Ledger")})
        leading = sorted({str(row.get("Ledger") or "") for row in ledger_rows if self._as_bool(row.get("IsLeadingLedger")) is True})
        requested_ledger = str(profile.get("ledger") or "")
        if requested_ledger and requested_ledger not in available_ledgers:
            errors.append(self._field_error("ledger", "not_found", "Configured ledger is not available in SAP metadata."))
        if not requested_ledger and len(leading) != 1:
            errors.append(self._field_error("ledger", "leading_ledger_not_unique", "SAP metadata must expose exactly one leading ledger when the profile does not specify one."))
        return {
            "status": "invalid" if errors else "validated",
            "provider": "embedded-sap-odata",
            "read_only": True,
            "verified_at": self._now(),
            "errors": errors,
            "metadata": {
                "company_code": company_code,
                "company_name": str(company.get("CompanyCodeName") or ""),
                "currency": str(company.get("Currency") or ""),
                "fiscal_year_variant": str(company.get("FiscalYearVariant") or ""),
                "controlling_area": sap_area,
                "available_ledgers": available_ledgers,
                "leading_ledger": leading[0] if len(leading) == 1 else None,
            },
        }

    def _record_verification(
        self, profile: JsonObject, result: JsonObject, actor: str
    ) -> None:
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO month_end_profile_verifications
                    (profile_id, verification_digest, profile_version, provider, actor, verified_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(profile.get("profile_id") or ""),
                    verification_digest(profile),
                    str(profile.get("version") or ""),
                    str(result.get("provider") or "embedded-sap-odata"),
                    actor,
                    str(result.get("verified_at") or self._now()),
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                ),
            )

    def _verification_for(self, profile_id: str, digest: str) -> JsonObject | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT provider, verified_at, metadata_json
                FROM month_end_profile_verifications
                WHERE profile_id = ? AND verification_digest = ?
                """,
                (profile_id, digest),
            ).fetchone()
        if row is None:
            return None
        return {
            "status": "validated",
            "provider": row[0],
            "verified_at": row[1],
            "metadata": json.loads(row[2]),
        }

    def _audit(self, profile_id: str, action: str, before: str | None, after: str | None, actor: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO month_end_profile_audit_events
                    (event_id, profile_id, action, before_digest, after_digest, actor, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, profile_id, action, before, after, actor, self._now()),
            )

    def _write_registry(self, registry: JsonObject) -> None:
        payload = json.dumps(registry, ensure_ascii=False, indent=2) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            fd, name = tempfile.mkstemp(prefix="profiles.", suffix=".tmp", dir=self.path.parent)
            temp_path = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
            temp_path = None
        except OSError as exc:
            raise MonthEndProfileError(
                "The month-end profile registry could not be written atomically.",
                code="month_end_profile_registry_write_failed",
                detail={"error": type(exc).__name__},
            ) from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _mutation_response(self, registry: JsonObject, profile: JsonObject) -> JsonObject:
        normalized = deepcopy(profile)
        normalized.setdefault("enabled", True)
        return {
            "ok": True,
            "registry_digest": canonical_digest(registry),
            "profile_digest": profile_digest(normalized),
            "verification_digest": verification_digest(normalized),
            "profile": normalized,
            "restart_required": False,
        }

    def _normalize_profile(self, profile: JsonObject) -> JsonObject:
        if not isinstance(profile, dict):
            return {}
        candidate = deepcopy(profile)
        candidate.setdefault("enabled", False)
        candidate.setdefault("version", "1.0.0")
        if not candidate.get("system_alias"):
            candidate["system_alias"] = self.system_alias
        if self.sap_client and not candidate.get("sap_client"):
            candidate["sap_client"] = self.sap_client
        return candidate

    def _business_content_changed(self, old: JsonObject, new: JsonObject) -> bool:
        return verification_digest(old) != verification_digest(new)

    def _find_profile(self, registry: JsonObject, profile_id: str) -> tuple[int, JsonObject]:
        for index, profile in enumerate(registry["profiles"]):
            if str(profile.get("profile_id") or "") == profile_id:
                return index, deepcopy(profile)
        raise MonthEndProfileError(
            "Month-end profile not found.", code="month_end_profile_not_found"
        )

    def _require_editable(self) -> None:
        if not self.editable:
            raise MonthEndProfileError(
                "The configured month-end profile registry is external and read-only.",
                code="month_end_profile_registry_external_read_only",
            )

    @staticmethod
    def _require_valid_registry(errors: list[JsonObject]) -> None:
        if errors:
            raise MonthEndProfileError(
                "The existing profiles.json is invalid and will not be overwritten.",
                code="month_end_profile_registry_invalid",
                detail={"errors": errors},
            )

    @staticmethod
    def _check_registry_digest(registry: JsonObject, expected: str) -> None:
        if not expected or canonical_digest(registry) != expected:
            MonthEndProfileService._conflict("The profile registry changed after this page was loaded.")

    @staticmethod
    def _conflict(message: str) -> None:
        raise MonthEndProfileError(message, code="month_end_profile_conflict")

    @staticmethod
    def _raise_invalid(errors: list[JsonObject]) -> None:
        raise MonthEndProfileError(
            "The month-end profile is invalid.",
            code="month_end_profile_invalid",
            detail={"errors": errors},
        )

    @staticmethod
    def _field_error(field: str, code: str, message: str) -> JsonObject:
        return {"field": field, "code": code, "message": message}

    @staticmethod
    def _deduplicate_errors(errors: list[JsonObject]) -> list[JsonObject]:
        seen: set[tuple[str, str, str]] = set()
        result: list[JsonObject] = []
        for error in errors:
            key = (str(error.get("field")), str(error.get("code")), str(error.get("message")))
            if key not in seen:
                seen.add(key)
                result.append(error)
        return result

    @staticmethod
    def _next_version(value: str) -> str:
        parts = value.split(".")
        try:
            numbers = [int(part) for part in parts]
        except ValueError:
            numbers = [1, 0, 0]
        while len(numbers) < 3:
            numbers.append(0)
        return f"{numbers[0]}.{numbers[1]}.{numbers[2] + 1}"

    @staticmethod
    def _date(value: Any) -> date | None:
        try:
            return date.fromisoformat(str(value or ""))
        except ValueError:
            return None

    @staticmethod
    def _integer(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_bool(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"true", "1", "x", "yes"}:
            return True
        if text in {"false", "0", "", "no"} and value is not None:
            return False
        return None

    @staticmethod
    def _rows(payload: Any) -> list[JsonObject]:
        if not isinstance(payload, dict):
            return []
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        rows = data.get("results") if isinstance(data, dict) else None
        return [dict(row) for row in (rows or []) if isinstance(row, dict)]

    @staticmethod
    def _source_complete(payload: Any) -> bool:
        return bool(
            isinstance(payload, dict)
            and payload.get("ok") is not False
            and payload.get("source_complete") is True
            and payload.get("source_truncated") is not True
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.database_path, timeout=10)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _initialize_database(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS month_end_profile_verifications (
                    profile_id TEXT NOT NULL,
                    verification_digest TEXT NOT NULL,
                    profile_version TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY (profile_id, verification_digest)
                );
                CREATE TABLE IF NOT EXISTS month_end_profile_audit_events (
                    event_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    before_digest TEXT,
                    after_digest TEXT,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            verification_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(month_end_profile_verifications)"
                ).fetchall()
            }
            if "actor" not in verification_columns:
                conn.execute(
                    "ALTER TABLE month_end_profile_verifications "
                    "ADD COLUMN actor TEXT NOT NULL DEFAULT 'local-user'"
                )
