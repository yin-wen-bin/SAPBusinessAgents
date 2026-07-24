from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from sap_business_agents.month_end_closing import ClosingContext, load_checklist
from sap_business_agents.month_end_closing.gateway import (
    CompositeSapGateway,
    SapDataUnavailable,
)
from sap_business_agents.month_end_closing.models import (
    CheckObservation,
    DataSourceTrace,
    source_mode,
)
from sap_business_agents.month_end_closing.se16n_fallback import (
    Se16nObservationGateway,
)


ROOT = Path(__file__).parents[1]
CONTEXT = ClosingContext("1010", 2026, 7)


def ap_check():
    checklist = load_checklist(ROOT / "config" / "month_end_checklist.toml")
    return next(item for item in checklist.checks if item.check_id == "AP_OVERDUE_ITEMS")


def gl_period_check():
    checklist = load_checklist(ROOT / "config" / "month_end_checklist.toml")
    return next(
        item for item in checklist.checks if item.check_id == "GL_PERIOD_CONTROL_ISSUE"
    )


def write_manifest(tmp_path: Path) -> Path:
    export = tmp_path / "BSIK_1010_2026_07.xlsx"
    export.write_bytes(b"reviewed SE16N export fixture")
    digest = hashlib.sha256(export.read_bytes()).hexdigest()
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "source_type": "sap-se16n-export",
        "sap_system": "S4Q",
        "sap_client": "100",
        "reviewed_by": "Finance Reviewer",
        "reviewed_at": "2026-07-31T18:00:00+08:00",
        "scope": {
            "company_code": "1010",
            "fiscal_year": 2026,
            "period": 7,
        },
        "currency": "EUR",
        "checks": {
            "AP_OVERDUE_ITEMS": {
                "value": 2,
                "amount": "125.00",
                "currency": "EUR",
                "data_quality_issues": [],
                "evidence": [{"supplier": "V1", "count": 2}],
                "exports": [
                    {
                        "table": "BSIK",
                        "file": export.name,
                        "sha256": digest,
                        "row_count": 2,
                        "selection_scope": {
                            "company_code": "1010",
                            "fiscal_year": 2026,
                            "period": 7,
                        },
                    }
                ],
            }
        },
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


class UnavailableMcpGateway:
    provider_name = "sapclaw_runtime_mcp"

    def report_currency(self, context: ClosingContext) -> str:
        return "EUR"

    def collect(self, context, check):
        raise SapDataUnavailable("service or field is not available")


class SuccessfulMcpGateway(UnavailableMcpGateway):
    def collect(self, context, check):
        return CheckObservation(
            Decimal("1"),
            Decimal("10"),
            "EUR",
            sources=(
                DataSourceTrace(
                    provider=self.provider_name,
                    status="used",
                    service_name="TEST_SERVICE",
                    resource="TestEntity",
                ),
            ),
        )


class MustNotBeCalledGateway:
    provider_name = "sap_se16n_export"

    def report_currency(self, context):
        raise AssertionError("fallback currency must not be requested")

    def collect(self, context, check):
        raise AssertionError("fallback must not be read after MCP succeeds")


def test_se16n_manifest_is_used_only_after_mcp_check_failure(tmp_path: Path) -> None:
    fallback = Se16nObservationGateway.from_file(write_manifest(tmp_path))
    gateway = CompositeSapGateway(UnavailableMcpGateway(), fallback)

    assert gateway.report_currency(CONTEXT) == "EUR"
    observation = gateway.collect(CONTEXT, ap_check())

    assert observation.value == 2
    assert observation.amount == Decimal("125.00")
    assert source_mode(observation.sources) == "se16n_fallback"
    assert [item.status for item in observation.sources] == ["unavailable", "used"]
    assert observation.sources[1].artifacts[0].endswith("BSIK_1010_2026_07.xlsx")


def test_successful_mcp_observation_never_reads_fallback() -> None:
    gateway = CompositeSapGateway(SuccessfulMcpGateway(), MustNotBeCalledGateway())

    observation = gateway.collect(CONTEXT, ap_check())

    assert source_mode(observation.sources) == "mcp"


def test_se16n_manifest_rejects_tampered_export(tmp_path: Path) -> None:
    manifest_path = write_manifest(tmp_path)
    export = tmp_path / "BSIK_1010_2026_07.xlsx"
    export.write_bytes(b"tampered")
    gateway = Se16nObservationGateway.from_file(manifest_path)

    with pytest.raises(SapDataUnavailable, match="hash mismatch"):
        gateway.collect(CONTEXT, ap_check())


def test_se16n_manifest_rejects_wrong_scope(tmp_path: Path) -> None:
    gateway = Se16nObservationGateway.from_file(write_manifest(tmp_path))

    with pytest.raises(SapDataUnavailable, match="scope mismatch"):
        gateway.collect(ClosingContext("2020", 2026, 7), ap_check())


def test_se16n_manifest_rejects_non_allowlisted_table(tmp_path: Path) -> None:
    manifest_path = write_manifest(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["checks"]["AP_OVERDUE_ITEMS"]["exports"][0]["table"] = "COEP"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    gateway = Se16nObservationGateway.from_file(manifest_path)

    with pytest.raises(SapDataUnavailable, match="not allowlisted"):
        gateway.collect(CONTEXT, ap_check())


def test_se16n_manifest_rejects_wrong_client(tmp_path: Path) -> None:
    manifest_path = write_manifest(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["sap_client"] = "550"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SapDataUnavailable, match="client mismatch"):
        Se16nObservationGateway.from_file(manifest_path)


def test_se16n_manifest_accepts_validated_read_only_alv_json(tmp_path: Path) -> None:
    artifact = tmp_path / "T001.json"
    grid_payload = {
        "schema_version": "1.0",
        "source_type": "sap-gui-se16n-alv-grid",
        "read_only": True,
        "sap_system": "S4Q",
        "sap_client": "100",
        "table": "T001",
        "row_count": 1,
        "columns": [{"technical_name": "BUKRS", "display_title": "CoCode"}],
        "rows": [{"BUKRS": "1010"}],
    }
    artifact.write_text(json.dumps(grid_payload), encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest_path = write_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["checks"] = {
        "GL_PERIOD_CONTROL_ISSUE": {
            "value": 1,
            "amount": "0",
            "currency": "EUR",
            "data_quality_issues": [],
            "evidence": [{"company_code": "1010"}],
            "exports": [
                {
                    "table": "T001",
                    "file": artifact.name,
                    "sha256": digest,
                    "row_count": 1,
                    "selection_scope": manifest["scope"],
                }
            ],
        }
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    observation = Se16nObservationGateway.from_file(manifest_path).collect(
        CONTEXT, gl_period_check()
    )

    assert observation.value == 1
    assert observation.sources[0].artifacts == (str(artifact.resolve()),)
