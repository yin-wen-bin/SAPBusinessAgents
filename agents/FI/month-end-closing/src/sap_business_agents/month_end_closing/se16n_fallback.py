"""Validated import boundary for reviewed SAP SE16N export observations."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from typing import Any

from .gateway import SapDataUnavailable
from .models import CheckDefinition, CheckObservation, ClosingContext, DataSourceTrace


class Se16nObservationGateway:
    """Read normalized observations backed by checksum-verified SE16N files.

    The gateway deliberately does not infer business meaning from arbitrary ALV
    column labels. A reviewer records the normalized metric in the manifest and
    binds it to the exact exported files, scope, SAP system, client, and hashes.
    """

    provider_name = "sap_se16n_export"

    def __init__(
        self,
        payload: dict[str, Any],
        manifest_path: Path,
        expected_client: str = "100",
    ) -> None:
        self._payload = payload
        self._manifest_path = manifest_path.resolve()
        self._base_dir = self._manifest_path.parent
        self._expected_client = expected_client
        self._validate_header()

    @classmethod
    def from_file(
        cls, path: str | Path, expected_client: str = "100"
    ) -> "Se16nObservationGateway":
        manifest_path = Path(path)
        try:
            with manifest_path.open("r", encoding="utf-8") as stream:
                payload = json.load(stream)
        except (OSError, json.JSONDecodeError) as exc:
            raise SapDataUnavailable(f"cannot read SE16N manifest: {exc}") from exc
        if not isinstance(payload, dict):
            raise SapDataUnavailable("SE16N manifest root must be an object")
        return cls(payload, manifest_path, expected_client)

    def report_currency(self, context: ClosingContext) -> str:
        self._validate_scope(context)
        return _required_text(self._payload, "currency")

    def collect(self, context: ClosingContext, check: CheckDefinition) -> CheckObservation:
        self._validate_scope(context)
        checks = self._payload.get("checks")
        if not isinstance(checks, dict) or check.check_id not in checks:
            raise SapDataUnavailable(
                f"SE16N manifest has no reviewed observation for {check.check_id}"
            )
        raw = checks[check.check_id]
        if not isinstance(raw, dict):
            raise SapDataUnavailable(
                f"SE16N observation for {check.check_id} must be an object"
            )

        trace = self._validated_trace(check, raw)
        issues = _string_list(raw, "data_quality_issues", required=False)
        if issues:
            unavailable = DataSourceTrace(
                provider=trace.provider,
                status="unavailable",
                resource=trace.resource,
                artifacts=trace.artifacts,
                detail="; ".join(issues),
            )
            raise SapDataUnavailable(
                f"reviewed SE16N observation failed data-quality checks: {'; '.join(issues)}",
                (unavailable,),
            )

        try:
            value = Decimal(str(raw["value"]))
            amount = Decimal(str(raw.get("amount", 0)))
        except (KeyError, InvalidOperation) as exc:
            raise SapDataUnavailable(
                f"invalid SE16N numeric observation for {check.check_id}", (trace,)
            ) from exc
        currency = str(raw.get("currency", self._payload["currency"])).strip()
        if currency != self._payload["currency"]:
            raise SapDataUnavailable(
                f"SE16N observation currency {currency} differs from manifest currency "
                f"{self._payload['currency']}",
                (trace,),
            )
        evidence = raw.get("evidence", [])
        if not isinstance(evidence, list) or not all(
            isinstance(item, dict) for item in evidence
        ):
            raise SapDataUnavailable(
                f"invalid SE16N evidence for {check.check_id}", (trace,)
            )
        return CheckObservation(
            value=value,
            amount=amount,
            currency=currency,
            evidence=tuple(evidence),
            sources=(trace,),
        )

    def _validate_header(self) -> None:
        if self._payload.get("schema_version") != "1.0":
            raise SapDataUnavailable("SE16N manifest schema_version must be 1.0")
        if self._payload.get("source_type") != "sap-se16n-export":
            raise SapDataUnavailable(
                "SE16N manifest source_type must be sap-se16n-export"
            )
        for key in ("sap_system", "sap_client", "reviewed_by", "reviewed_at", "currency"):
            _required_text(self._payload, key)
        if _required_text(self._payload, "sap_client") != self._expected_client:
            raise SapDataUnavailable(
                "SE16N manifest client mismatch: expected "
                f"{self._expected_client}, got {_required_text(self._payload, 'sap_client')}"
            )
        reviewed_at = _required_text(self._payload, "reviewed_at")
        try:
            parsed = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SapDataUnavailable("SE16N manifest reviewed_at must be ISO-8601") from exc
        if parsed.tzinfo is None:
            raise SapDataUnavailable("SE16N manifest reviewed_at must include a timezone")
        scope = self._payload.get("scope")
        if not isinstance(scope, dict):
            raise SapDataUnavailable("SE16N manifest requires a scope object")
        checks = self._payload.get("checks")
        if not isinstance(checks, dict) or not checks:
            raise SapDataUnavailable("SE16N manifest requires reviewed checks")

    def _validate_scope(self, context: ClosingContext) -> None:
        expected = context.to_dict()
        scope = self._payload["scope"]
        actual = {key: scope.get(key) for key in expected}
        if actual != expected:
            raise SapDataUnavailable(
                f"SE16N manifest scope mismatch: expected {expected}, got {actual}"
            )

    def _validated_trace(
        self, check: CheckDefinition, raw: dict[str, Any]
    ) -> DataSourceTrace:
        exports = raw.get("exports")
        if not isinstance(exports, list) or not exports:
            raise SapDataUnavailable(
                f"SE16N observation for {check.check_id} requires at least one export"
            )
        allowed_tables = {table.upper() for table in check.tables}
        artifacts: list[str] = []
        tables: list[str] = []
        total_rows = 0
        for item in exports:
            if not isinstance(item, dict):
                raise SapDataUnavailable("each SE16N export reference must be an object")
            table = _required_text(item, "table").upper()
            if table not in allowed_tables:
                raise SapDataUnavailable(
                    f"SE16N table {table} is not allowlisted for {check.check_id}"
                )
            selection_scope = item.get("selection_scope")
            if selection_scope != self._payload["scope"]:
                raise SapDataUnavailable(
                    f"SE16N selection scope for {table} does not match manifest scope"
                )
            row_count = item.get("row_count")
            if not isinstance(row_count, int) or row_count < 0:
                raise SapDataUnavailable(f"SE16N export {table} has invalid row_count")
            relative_or_absolute = Path(_required_text(item, "file"))
            artifact = (
                relative_or_absolute
                if relative_or_absolute.is_absolute()
                else self._base_dir / relative_or_absolute
            ).resolve()
            if artifact.suffix.lower() not in {".xlsx", ".xls", ".csv", ".json"}:
                raise SapDataUnavailable(
                    f"SE16N artifact must be XLSX, XLS, CSV, or validated ALV JSON: {artifact}"
                )
            if not artifact.is_file():
                raise SapDataUnavailable(f"SE16N artifact does not exist: {artifact}")
            expected_hash = _required_text(item, "sha256").lower()
            if len(expected_hash) != 64 or any(
                char not in "0123456789abcdef" for char in expected_hash
            ):
                raise SapDataUnavailable(f"SE16N export {table} has invalid sha256")
            actual_hash = _sha256(artifact)
            if actual_hash != expected_hash:
                raise SapDataUnavailable(
                    f"SE16N artifact hash mismatch for {artifact.name}"
                )
            if artifact.suffix.lower() == ".json":
                _validate_alv_grid_artifact(
                    artifact,
                    table=table,
                    row_count=row_count,
                    sap_system=_required_text(self._payload, "sap_system"),
                    sap_client=_required_text(self._payload, "sap_client"),
                )
            artifacts.append(str(artifact))
            tables.append(table)
            total_rows += row_count

        return DataSourceTrace(
            provider=self.provider_name,
            status="used",
            resource=str(self._manifest_path),
            artifacts=tuple(artifacts),
            detail=(
                f"SAP {_required_text(self._payload, 'sap_system')} client "
                f"{_required_text(self._payload, 'sap_client')}; tables={','.join(tables)}; "
                f"export_rows={total_rows}; reviewed_by="
                f"{_required_text(self._payload, 'reviewed_by')}"
            ),
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_alv_grid_artifact(
    path: Path,
    *,
    table: str,
    row_count: int,
    sap_system: str,
    sap_client: str,
) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SapDataUnavailable(f"cannot read SE16N ALV JSON artifact: {path}") from exc
    if not isinstance(payload, dict):
        raise SapDataUnavailable(f"SE16N ALV JSON root must be an object: {path}")
    expected = {
        "schema_version": "1.0",
        "source_type": "sap-gui-se16n-alv-grid",
        "read_only": True,
        "sap_system": sap_system,
        "sap_client": sap_client,
        "table": table,
        "row_count": row_count,
    }
    actual = {key: payload.get(key) for key in expected}
    if actual != expected:
        raise SapDataUnavailable(
            f"SE16N ALV JSON metadata mismatch for {path.name}: "
            f"expected {expected}, got {actual}"
        )
    rows = payload.get("rows")
    columns = payload.get("columns")
    if not isinstance(rows, list) or len(rows) != row_count:
        raise SapDataUnavailable(f"SE16N ALV JSON row count mismatch for {path.name}")
    if not isinstance(columns, list) or not columns:
        raise SapDataUnavailable(f"SE16N ALV JSON columns are missing for {path.name}")


def _required_text(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SapDataUnavailable(f"missing or invalid SE16N manifest field: {key}")
    return value.strip()


def _string_list(
    item: dict[str, Any], key: str, *, required: bool = True
) -> tuple[str, ...]:
    value = item.get(key, [])
    if not isinstance(value, list) or not all(
        isinstance(part, str) and part.strip() for part in value
    ):
        raise SapDataUnavailable(f"invalid SE16N manifest string list: {key}")
    if required and not value:
        raise SapDataUnavailable(f"missing SE16N manifest string list: {key}")
    return tuple(part.strip() for part in value)
