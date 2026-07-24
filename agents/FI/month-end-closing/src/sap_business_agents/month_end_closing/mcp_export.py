"""Import normalized observations exported by SAPClaw Runtime MCP calls."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any

from .gateway import SapDataUnavailable
from .models import CheckDefinition, CheckObservation, ClosingContext, DataSourceTrace


class SapClawMcpExportGateway:
    """Primary gateway for a scope-bound, auditable SAPClaw MCP export bundle."""

    provider_name = "sapclaw_runtime_mcp"

    def __init__(
        self,
        payload: dict[str, Any],
        source_path: Path,
        expected_client: str = "100",
    ) -> None:
        self._payload = payload
        self._source_path = source_path.resolve()
        self._expected_client = expected_client
        self._validate_header()

    @classmethod
    def from_file(
        cls, path: str | Path, expected_client: str = "100"
    ) -> "SapClawMcpExportGateway":
        source_path = Path(path)
        try:
            with source_path.open("r", encoding="utf-8") as stream:
                payload = json.load(stream)
        except (OSError, json.JSONDecodeError) as exc:
            raise SapDataUnavailable(f"cannot read SAPClaw MCP export: {exc}") from exc
        if not isinstance(payload, dict):
            raise SapDataUnavailable("SAPClaw MCP export root must be an object")
        return cls(payload, source_path, expected_client)

    def report_currency(self, context: ClosingContext) -> str:
        self._validate_scope(context)
        return _required_text(self._payload, "currency")

    def collect(self, context: ClosingContext, check: CheckDefinition) -> CheckObservation:
        self._validate_scope(context)
        checks = self._payload.get("checks")
        if not isinstance(checks, dict) or check.check_id not in checks:
            raise SapDataUnavailable(
                f"SAPClaw MCP export has no observation for {check.check_id}"
            )
        raw = checks[check.check_id]
        if not isinstance(raw, dict):
            raise SapDataUnavailable(
                f"SAPClaw MCP observation for {check.check_id} must be an object"
            )
        source = raw.get("source")
        if not isinstance(source, dict):
            raise SapDataUnavailable(
                f"SAPClaw MCP observation for {check.check_id} requires source metadata"
            )
        complete = raw.get("complete") is True
        case_ids = _string_list(source, "case_ids", required=complete)
        incomplete_reason = str(raw.get("incomplete_reason", "")).strip()
        quality_issues = _string_list(raw, "data_quality_issues", required=False)
        detail_parts = [
            f"exported_at={_required_text(self._payload, 'exported_at')}",
            f"row_count={source.get('row_count', 'unknown')}",
        ]
        if incomplete_reason:
            detail_parts.append(f"incomplete_reason={incomplete_reason}")
        if quality_issues:
            detail_parts.append("data_quality=" + "; ".join(quality_issues))
        trace = DataSourceTrace(
            provider=self.provider_name,
            status="used" if complete else "unavailable",
            service_name=_required_text(source, "service_name"),
            resource=_required_text(source, "resource"),
            case_ids=case_ids,
            artifacts=(str(self._source_path),),
            detail="; ".join(detail_parts),
        )
        if not complete:
            reason = incomplete_reason or "result is not complete"
            raise SapDataUnavailable(
                f"SAPClaw MCP export for {check.check_id} is incomplete: {reason}",
                (trace,),
            )
        if quality_issues:
            raise SapDataUnavailable(
                "SAPClaw MCP export failed data-quality checks: "
                + "; ".join(quality_issues),
                (
                    replace(
                        trace,
                        status="unavailable",
                        detail="; ".join(quality_issues),
                    ),
                ),
            )
        try:
            value = Decimal(str(raw["value"]))
            amount = Decimal(str(raw.get("amount", 0)))
        except (KeyError, InvalidOperation) as exc:
            raise SapDataUnavailable(
                f"invalid SAPClaw MCP numeric observation for {check.check_id}", (trace,)
            ) from exc
        currency = str(raw.get("currency", self._payload["currency"])).strip()
        if currency != self._payload["currency"]:
            raise SapDataUnavailable(
                f"SAPClaw MCP observation currency {currency} differs from bundle currency "
                f"{self._payload['currency']}",
                (trace,),
            )
        evidence = raw.get("evidence", [])
        if not isinstance(evidence, list) or not all(
            isinstance(item, dict) for item in evidence
        ):
            raise SapDataUnavailable(
                f"invalid SAPClaw MCP evidence for {check.check_id}", (trace,)
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
            raise SapDataUnavailable("SAPClaw MCP export schema_version must be 1.0")
        if self._payload.get("source_type") != "sapclaw-runtime-mcp-export":
            raise SapDataUnavailable(
                "SAPClaw MCP export source_type must be sapclaw-runtime-mcp-export"
            )
        if self._payload.get("read_only") is not True:
            raise SapDataUnavailable("SAPClaw MCP export must declare read_only=true")
        if self._payload.get("query_plans_validated") is not True:
            raise SapDataUnavailable(
                "SAPClaw MCP export must declare query_plans_validated=true"
            )
        for key in ("exported_at", "currency", "sap_system", "sap_client"):
            _required_text(self._payload, key)
        if _required_text(self._payload, "sap_client") != self._expected_client:
            raise SapDataUnavailable(
                "SAPClaw MCP export client mismatch: expected "
                f"{self._expected_client}, got {_required_text(self._payload, 'sap_client')}"
            )
        exported_at = _required_text(self._payload, "exported_at")
        try:
            parsed = datetime.fromisoformat(exported_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SapDataUnavailable("SAPClaw MCP exported_at must be ISO-8601") from exc
        if parsed.tzinfo is None:
            raise SapDataUnavailable("SAPClaw MCP exported_at must include a timezone")
        if not isinstance(self._payload.get("scope"), dict):
            raise SapDataUnavailable("SAPClaw MCP export requires a scope object")
        if not isinstance(self._payload.get("checks"), dict):
            raise SapDataUnavailable("SAPClaw MCP export requires checks")

    def _validate_scope(self, context: ClosingContext) -> None:
        expected = context.to_dict()
        scope = self._payload["scope"]
        actual = {key: scope.get(key) for key in expected}
        if actual != expected:
            raise SapDataUnavailable(
                f"SAPClaw MCP export scope mismatch: expected {expected}, got {actual}"
            )


def _required_text(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SapDataUnavailable(f"missing or invalid SAPClaw MCP export field: {key}")
    return value.strip()


def _string_list(
    item: dict[str, Any], key: str, *, required: bool = True
) -> tuple[str, ...]:
    value = item.get(key, [])
    if not isinstance(value, list) or not all(
        isinstance(part, str) and part.strip() for part in value
    ):
        raise SapDataUnavailable(f"invalid SAPClaw MCP export string list: {key}")
    if required and not value:
        raise SapDataUnavailable(f"missing SAPClaw MCP export string list: {key}")
    return tuple(part.strip() for part in value)
