from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError


class SkillError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "run_failed",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class SkillRegistry:
    """Unified broker for every approved machine-callable SAPSkillhub contract."""

    def __init__(self, skillhub_root: Path, allowlist_path: Path) -> None:
        self.skillhub_root = skillhub_root
        self.allowlist_path = allowlist_path

    def list(self) -> list[dict[str, Any]]:
        return self.list_all_approved_skills()

    def list_all_approved_skills(self) -> list[dict[str, Any]]:
        """Return the single approved Skill catalog used by every execution mode."""

        records: list[dict[str, Any]] = []
        for raw in self._configured_skills():
            record = self._load_record(raw)
            if self._is_approved(record):
                records.append(record)
        return records

    def get(self, skill_id: str) -> dict[str, Any]:
        for raw in self._configured_skills():
            if raw.get("skill_id") != skill_id:
                continue
            record = self._load_record(raw)
            issues = record.get("validation_issues") or []
            if issues:
                issue = issues[0]
                raise SkillError(
                    str(issue.get("message") or f"Skill {skill_id} failed supply-chain validation."),
                    code=str(issue.get("code") or "skill_contract_incompatible"),
                    detail={"skill_id": skill_id, "validation_issues": issues},
                )
            if not record.get("available"):
                raise SkillError(
                    f"Skill {skill_id} is allowlisted but its entrypoint is unavailable.",
                    code="skill_entrypoint_unavailable",
                    detail={"skill_id": skill_id},
                )
            if not self._is_approved(record):
                raise SkillError(
                    f"Skill {skill_id} is not approved for read-only automation.",
                    code="skill_not_approved",
                    detail={"skill_id": skill_id},
                )
            return record
        raise KeyError(skill_id)

    def _configured_skills(self) -> list[dict[str, Any]]:
        if not self.allowlist_path.exists():
            return []
        payload = json.loads(self.allowlist_path.read_text(encoding="utf-8"))
        return [item for item in payload.get("skills", []) if isinstance(item, dict)]

    def _load_record(self, raw: dict[str, Any]) -> dict[str, Any]:
        record = dict(raw)
        entrypoint = (self.skillhub_root / str(record.get("entrypoint") or "")).resolve()
        within_root = self.skillhub_root == entrypoint or self.skillhub_root in entrypoint.parents
        record["available"] = bool(within_root and entrypoint.is_file())
        record["entrypoint"] = str(entrypoint)
        issues: list[dict[str, str]] = []
        try:
            for schema_name in (
                "input_schema",
                "output_schema",
                "public_output_schema",
                "restricted_row_schema",
            ):
                schema_path_name = f"{schema_name}_path"
                if record.get(schema_path_name):
                    record[schema_name] = _load_trusted_schema(
                        self.skillhub_root, str(record[schema_path_name]), schema_name
                    )
            _validate_skill_supply_chain(self.skillhub_root, record)
        except SkillError as exc:
            record["available"] = False
            issues.append({"code": exc.code, "message": str(exc)})
        record["validation_issues"] = issues
        return record

    @staticmethod
    def _is_approved(record: dict[str, Any]) -> bool:
        has_contract = all(
            isinstance(record.get(name), dict) and record[name].get("type") == "object"
            for name in ("input_schema", "output_schema")
        )
        return bool(
            record.get("read_only") is True
            and record.get("validated") is True
            and record.get("available") is True
            and record.get("output_policy") in {"public", "restricted_artifact"}
            and has_contract
        )

    def validate_input(self, skill_id: str, input_payload: dict[str, Any]) -> None:
        """Validate a trusted Skill contract without starting its subprocess."""

        skill = self.get(skill_id)
        if skill_id == "sap-adt-table-export":
            _validate_adt_input(input_payload)
            _validate_adt_connection_contract(skill.get("input_schema"))
        _validate_json_contract(input_payload, skill.get("input_schema"), "input")

    async def execute(self, skill_id: str, input_payload: dict[str, Any]) -> dict[str, Any]:
        skill = self.get(skill_id)
        if not skill.get("available"):
            raise SkillError(f"Skill {skill_id} is allowlisted but its entrypoint is unavailable.")
        if skill.get("read_only") is not True or skill.get("validated") is not True:
            raise SkillError(f"Skill {skill_id} is not approved for read-only automation.")
        self.validate_input(skill_id, input_payload)
        timeout = max(
            1,
            int(skill.get("platform_timeout_seconds") or skill.get("timeout") or 300),
        )
        broker_temp_root = (self.skillhub_root / ".codex-tmp").resolve()
        if self.skillhub_root != broker_temp_root and self.skillhub_root not in broker_temp_root.parents:
            raise SkillError(
                "Skill temporary directory escapes the SAPSkillhub root.",
                code="skill_temporary_path_invalid",
            )
        broker_temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="sapba-skill-", dir=broker_temp_root
        ) as temporary:
            temporary_root = Path(temporary)
            input_path = temporary_root / "input.json"
            output_path = temporary_root / "output.json"
            input_path.write_text(
                json.dumps(input_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(skill["entrypoint"]),
                "--input",
                str(input_path),
                "--output",
                str(output_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_skill_subprocess_environment(skill_id),
            )
            try:
                _stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                raise SkillError(f"Skill {skill_id} timed out.") from exc
            try:
                output_bytes = output_path.read_bytes()
                result = json.loads(output_bytes.decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                suffix = (
                    f" after exit code {process.returncode}"
                    if process.returncode != 0
                    else ""
                )
                raise SkillError(
                    f"Skill {skill_id} did not write one JSON object to --output{suffix}."
                ) from exc
            if not isinstance(result, dict):
                raise SkillError(f"Skill {skill_id} returned an invalid result.")
            _validate_json_contract(result, skill.get("output_schema"), "output")
            if skill_id == "sap-adt-table-export":
                _validate_adt_output(result)
                manifest_hash = _validate_adt_manifest(output_path, output_bytes, result)
                result["artifacts"].append(
                    {"type": "output_manifest", "sha256": manifest_hash, "verified": True}
                )
                if process.returncode != 0 and result.get("status") != "failed":
                    raise SkillError(
                        f"Skill {skill_id} exited with code {process.returncode} without a failed result."
                    )
            elif process.returncode != 0:
                raise SkillError(
                    f"Skill {skill_id} failed with exit code {process.returncode}. "
                    "Its stderr was intentionally not persisted because it may contain sensitive data."
                )
            return result


def validate_agent_skill_dependencies(
    agent: dict[str, Any], skills: Any
) -> list[str]:
    """Resolve every Skill referenced by a deterministic Agent before it can run."""

    dependencies: list[str] = []
    execution = agent.get("execution") if isinstance(agent, dict) else None
    for step in (execution or {}).get("steps") or []:
        if not isinstance(step, dict) or step.get("executor") != "skill":
            continue
        skill_id = str(step.get("skillId") or "").strip()
        if not skill_id or skill_id in dependencies:
            continue
        try:
            skills.get(skill_id)
        except KeyError as exc:
            raise SkillError(
                f"Agent {agent.get('slug') or '<unknown>'} references an unregistered Skill: {skill_id}",
                code="agent_skill_unregistered",
                detail={
                    "agent_id": agent.get("slug"),
                    "skill_id": skill_id,
                },
            ) from exc
        except SkillError as exc:
            detail = dict(exc.detail or {})
            detail.update(
                {
                    "agent_id": agent.get("slug"),
                    "skill_id": skill_id,
                }
            )
            raise SkillError(
                f"Agent {agent.get('slug') or '<unknown>'} requires unavailable Skill {skill_id}: {exc}",
                code=exc.code,
                detail=detail,
            ) from exc
        dependencies.append(skill_id)
    return dependencies


def _validate_skill_supply_chain(root: Path, record: dict[str, Any]) -> None:
    output_policy = record.get("output_policy")
    if output_policy not in {"public", "restricted_artifact"}:
        raise SkillError(
            "Skill output_policy is not declared.", code="skill_contract_incompatible"
        )
    manifest_name = record.get("manifest_path")
    if not manifest_name:
        return
    manifest_path = (root / str(manifest_name)).resolve()
    if root not in manifest_path.parents or not manifest_path.is_file():
        raise SkillError("Skill manifest is unavailable.", code="skill_manifest_mismatch")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillError("Skill manifest is invalid.", code="skill_manifest_mismatch") from exc
    expected = {
        "skill_id": record.get("skill_id"),
        "read_only": True,
        "validated": True,
    }
    for field, value in expected.items():
        if manifest.get(field) != value:
            raise SkillError(
                f"Skill manifest {field} does not match the approved contract.",
                code="skill_manifest_mismatch",
            )
    projection_mode = record.get("restricted_projection_mode")
    if output_policy == "restricted_artifact" and projection_mode == "declared_split":
        if not all(
            isinstance(record.get(field), dict)
            for field in ("public_output_schema", "restricted_row_schema")
        ):
            raise SkillError(
                "Declared restricted projection schemas are unavailable.",
                code="skill_contract_incompatible",
            )
        def projection_path_matches(manifest_field: str, record_field: str) -> bool:
            declared = str(manifest.get(manifest_field) or "")
            configured = str(record.get(record_field) or "")
            if not declared or not configured:
                return False
            declared_path = (manifest_path.parent / declared).resolve()
            configured_path = (root / configured).resolve()
            return (
                declared_path == configured_path
                and manifest_path.parent in declared_path.parents
                and root in configured_path.parents
            )

        if not projection_path_matches(
            "public_output_schema", "public_output_schema_path"
        ) or not projection_path_matches(
            "restricted_row_schema", "restricted_row_schema_path"
        ):
            raise SkillError(
                "Skill privacy projection drifted from its manifest.",
                code="skill_manifest_mismatch",
            )
    if sorted(manifest.get("allowed_http_methods") or []) != sorted(
        record.get("allowed_http_methods") or []
    ) or sorted(manifest.get("allowed_endpoints") or []) != sorted(
        record.get("allowed_endpoints") or []
    ):
        raise SkillError(
            "Skill HTTP allowlist drifted from the approved contract.",
            code="skill_manifest_mismatch",
        )
    profile_name = record.get("source_profile_path")
    if profile_name:
        profile_path = (root / str(profile_name)).resolve()
        if root not in profile_path.parents or not profile_path.is_file():
            raise SkillError("Skill source profile is unavailable.", code="skill_profile_drift")
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SkillError("Skill source profile is invalid.", code="skill_profile_drift") from exc
        if (
            profile.get("profile_status") != "validated"
            or profile.get("profile_version") != record.get("expected_profile_version")
        ):
            raise SkillError(
                "Skill source profile drifted from the validated version.",
                code="skill_profile_drift",
            )
        active = (profile.get("profiles") or {}).get(profile.get("active_profile_id"), {})
        if active.get("metadata_sha256") != record.get("expected_metadata_sha256"):
            raise SkillError(
                "Skill source metadata fingerprint drifted from the validated target.",
                code="skill_metadata_drift",
            )
        expected_profile_digest = str(record.get("expected_profile_sha256") or "")
        if expected_profile_digest and hashlib.sha256(profile_path.read_bytes()).hexdigest() != expected_profile_digest:
            raise SkillError(
                "Skill source profile content drifted from the approved snapshot.",
                code="skill_profile_drift",
            )
    for schema_field, digest_field in (
        ("output_schema_path", "expected_output_schema_sha256"),
        ("public_output_schema_path", "expected_public_output_schema_sha256"),
        ("restricted_row_schema_path", "expected_restricted_row_schema_sha256"),
    ):
        expected_schema_digest = str(record.get(digest_field) or "")
        if not expected_schema_digest:
            continue
        schema_path = (root / str(record.get(schema_field) or "")).resolve()
        if root not in schema_path.parents or not schema_path.is_file() or hashlib.sha256(
            schema_path.read_bytes()
        ).hexdigest() != expected_schema_digest:
            raise SkillError(
                "Skill schema content drifted from the approved snapshot.",
                code="skill_schema_drift",
            )
    expected_digest = str(record.get("expected_package_sha256") or "")
    if expected_digest:
        package_root = manifest_path.parent
        actual_digest = _skill_package_digest(package_root)
        if not hmac_compare_digest(actual_digest, expected_digest):
            raise SkillError(
                "Skill package digest does not match the approved package.",
                code="skill_package_digest_mismatch",
            )
        record["package_sha256"] = actual_digest
    expected_commit = str(record.get("expected_git_commit") or "").strip()
    if expected_commit:
        try:
            subprocess.run(
                ["git", "cat-file", "-e", f"{expected_commit}^{{commit}}"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            ancestor = subprocess.run(
                ["git", "merge-base", "--is-ancestor", expected_commit, "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SkillError(
                "Skill repository commit could not be verified.",
                code="skill_git_commit_mismatch",
            ) from exc
        if ancestor.returncode != 0:
            raise SkillError(
                "Skill repository no longer contains the approved commit in its current history.",
                code="skill_git_commit_mismatch",
            )
    record["git_commit"] = expected_commit
    record["manifest"] = manifest


def _skill_package_digest(package_root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        path
        for path in package_root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.name != ".env"
        and path.suffix.lower() not in {".pyc", ".pyo"}
        and not path.name.startswith(".")
    )
    for path in files:
        relative = path.relative_to(package_root).as_posix().encode("utf-8") + b"\n"
        digest.update(relative)
        digest.update(path.read_bytes())
    return digest.hexdigest()


def hmac_compare_digest(left: str, right: str) -> bool:
    # A local helper avoids importing secret values while keeping timing-safe digest checks.
    import hmac

    return hmac.compare_digest(left, right)


def _load_trusted_schema(root: Path, relative_path: str, label: str) -> dict[str, Any]:
    path = (root / relative_path).resolve()
    if root != path and root not in path.parents:
        raise SkillError(f"Skill {label} path escapes SAPSkillhub root.")
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillError(f"Skill {label} could not be loaded from its trusted schema path.") from exc
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise SkillError(f"Skill {label} must be an object JSON Schema.")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise SkillError(f"Skill {label} JSON Schema is invalid.") from exc
    return schema


def _validate_json_contract(value: dict[str, Any], schema: Any, label: str) -> None:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise SkillError(f"Skill {label}_schema must be an object JSON Schema.")
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    except (SchemaError, ValidationError) as exc:
        path = ".".join(str(item) for item in getattr(exc, "absolute_path", []))
        suffix = f" at {path}" if path else ""
        raise SkillError(f"Skill {label} does not match its JSON Schema{suffix}.") from exc


_ADT_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,59}$")
_ADT_OBJECT = re.compile(r"^(?=.{1,60}$)(?:[A-Za-z][A-Za-z0-9_]*|/[A-Za-z0-9_]+/[A-Za-z0-9_/]+)$")
_ADT_FILTER_KEYS = {"field", "sign", "option", "operator", "value", "low", "high", "values"}
_ADT_OPERATORS = {"eq", "ne", "gt", "ge", "lt", "le", "bt", "in"}
_SAP_CONNECTION_ENV_KEYS = {
    "SAP_AUTH_TYPE",
    "SAP_BASE_URL",
    "SAP_CLIENT",
    "SAP_ODATA_BASE_URL",
    "SAP_ODATA_TIMEOUT_MS",
    "SAP_PASSWORD",
    "SAP_SYSTEM",
    "SAP_USERNAME",
    "SAP_VERIFY_SSL",
    "SAPBA_SAP_ENV_FILE",
}
_SENSITIVE_KEYS = {
    "url",
    "endpoint",
    "host",
    "client",
    "username",
    "user",
    "password",
    "credential",
    "credentials",
    "ca_path",
    "certificate",
    "verify_ssl",
    "sql",
}


def _validate_adt_input(value: dict[str, Any]) -> None:
    """Fail closed before invoking ADT with a strictly bounded declarative task."""

    if "connection_profile" in value:
        raise SkillError(
            "ADT connection selection is owned by SAPSkillhub and cannot be supplied by the caller.",
            code="skill_input_connection_forbidden",
            detail={"field": "connection_profile", "connection_owner": "SAPSkillhub"},
        )
    if value.get("schema_version") != 1:
        raise SkillError("ADT input schema_version must be 1.")
    if value.get("source_type") not in {"table", "cds"}:
        raise SkillError("ADT source_type must be table or cds.")
    object_name = value.get("object")
    if not isinstance(object_name, str) or not _ADT_OBJECT.fullmatch(object_name):
        raise SkillError("ADT object must be one valid table or CDS identifier.")
    fields = value.get("fields")
    if (
        not isinstance(fields, list)
        or not fields
        or len(fields) > 100
        or len(set(fields)) != len(fields)
        or any(not isinstance(field, str) or not _ADT_IDENTIFIER.fullmatch(field) for field in fields)
    ):
        raise SkillError("ADT fields must be a non-empty unique list of identifiers.")
    filters = value.get("filters")
    if not isinstance(filters, list) or not filters or len(filters) > 20:
        raise SkillError("ADT filters must be non-empty and bounded.")
    for index, item in enumerate(filters):
        if not isinstance(item, dict) or set(item).difference(_ADT_FILTER_KEYS):
            raise SkillError(f"ADT filter {index} contains unsupported keys.")
        field = item.get("field")
        operator = str(item.get("operator") or item.get("option") or "").lower()
        if not isinstance(field, str) or not _ADT_IDENTIFIER.fullmatch(field):
            raise SkillError(f"ADT filter {index} field is invalid.")
        if operator not in _ADT_OPERATORS:
            raise SkillError(f"ADT filter {index} operator is not approved.")
        if ("operator" in item) == ("option" in item):
            raise SkillError(f"ADT filter {index} requires exactly one operator key.")
        if item.get("sign", "I") not in {"I", "E"}:
            raise SkillError(f"ADT filter {index} sign is invalid.")
        if "values" in item and (
            not isinstance(item["values"], list) or not item["values"] or len(item["values"]) > 100
        ):
            raise SkillError(f"ADT filter {index} values must be a non-empty bounded list.")
        if operator == "bt" and ("low" not in item or "high" not in item):
            raise SkillError(f"ADT filter {index} between requires low and high.")
        if operator not in {"bt", "in"} and not any(key in item for key in ("value", "low", "values")):
            raise SkillError(f"ADT filter {index} requires a typed value.")
    order_by = value.get("order_by", [])
    if not isinstance(order_by, list) or len(order_by) > 10:
        raise SkillError("ADT order_by must be a bounded array.")
    for index, item in enumerate(order_by):
        if isinstance(item, str):
            field, direction = item, "asc"
        elif isinstance(item, dict) and set(item).issubset({"field", "direction"}):
            field, direction = item.get("field"), str(item.get("direction") or "asc").lower()
        else:
            raise SkillError(f"ADT order_by {index} is invalid.")
        if not isinstance(field, str) or not _ADT_IDENTIFIER.fullmatch(field) or direction != "asc":
            raise SkillError("ADT order_by must use ascending stable-key identifiers only.")
    max_rows = value.get("max_rows")
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or not 1 <= max_rows <= 30_000:
        raise SkillError("ADT max_rows must be between 1 and 30000.")
    _reject_sensitive_adt_keys(value)


def _validate_adt_connection_contract(schema: Any) -> None:
    """Require an ADT Skill contract whose connection is selected inside SAPSkillhub."""

    if not isinstance(schema, dict):
        return
    properties = schema.get("properties")
    required = schema.get("required")
    exposes_profile = isinstance(properties, dict) and "connection_profile" in properties
    requires_profile = isinstance(required, list) and "connection_profile" in required
    if exposes_profile or requires_profile:
        raise SkillError(
            "Installed sap-adt-table-export contract still exposes caller-managed connection selection.",
            code="skill_contract_incompatible",
            detail={
                "skill_id": "sap-adt-table-export",
                "expected_contract": "skill_managed_default_connection",
                "caller_connection_profile_exposed": exposes_profile,
                "caller_connection_profile_required": requires_profile,
            },
        )


def _skill_subprocess_environment(skill_id: str) -> dict[str, str]:
    """Build an isolated child environment without caller-owned SAP connection settings."""

    environment = dict(os.environ)
    if skill_id != "sap-adt-table-export":
        return environment
    for key in list(environment):
        normalized = key.upper()
        if normalized in _SAP_CONNECTION_ENV_KEYS or normalized.startswith("SAP_ADT_"):
            environment.pop(key, None)
    return environment


def _reject_sensitive_adt_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                raise SkillError(f"ADT input contains prohibited key: {key}")
            _reject_sensitive_adt_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_sensitive_adt_keys(child)


def _validate_adt_output(value: dict[str, Any]) -> None:
    if value.get("schema_version") != 1 or value.get("skill_id") != "sap-adt-table-export":
        raise SkillError("ADT output identity is invalid.")
    if value.get("read_only") is not True:
        raise SkillError("ADT output did not attest read_only=true.")
    status = value.get("status")
    if status not in {"complete", "partial", "failed"}:
        raise SkillError("ADT output status is invalid.")
    completeness = value.get("completeness")
    issues = value.get("validation_issues")
    if not isinstance(completeness, dict) or not isinstance(issues, list):
        raise SkillError("ADT output completeness contract is invalid.")
    if status == "complete" and not (
        value.get("validated") is True
        and completeness.get("source_complete") is True
        and completeness.get("paging_complete") is True
        and not issues
    ):
        raise SkillError("ADT complete result lacks required validation or paging evidence.")
    rows = value.get("rows")
    if not isinstance(rows, list) or value.get("row_count") != len(rows):
        raise SkillError("ADT output row_count does not match returned rows.")


def _validate_adt_manifest(output_path: Path, output_bytes: bytes, result: dict[str, Any]) -> str:
    manifest_path = output_path.with_name(output_path.name + ".manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillError("ADT output is missing its adjacent SHA-256 manifest.") from exc
    expected = hashlib.sha256(output_bytes).hexdigest()
    if not isinstance(manifest, dict) or manifest.get("output_sha256") != expected:
        raise SkillError("ADT output SHA-256 manifest verification failed.")
    for key in ("skill_id", "run_id", "read_only", "status"):
        if manifest.get(key) != result.get(key):
            raise SkillError(f"ADT output manifest {key} does not match the result.")
    return expected
