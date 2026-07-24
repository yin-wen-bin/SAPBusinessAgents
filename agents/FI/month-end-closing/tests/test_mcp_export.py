from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path

import pytest

from sap_business_agents.month_end_closing import ClosingContext, load_checklist
from sap_business_agents.month_end_closing.gateway import SapDataUnavailable
from sap_business_agents.month_end_closing.mcp_export import SapClawMcpExportGateway
from sap_business_agents.month_end_closing.models import source_mode


ROOT = Path(__file__).parents[1]
CONTEXT = ClosingContext("1010", 2026, 7)


def ap_check():
    checklist = load_checklist(ROOT / "config" / "month_end_checklist.toml")
    return next(item for item in checklist.checks if item.check_id == "AP_OVERDUE_ITEMS")


def write_bundle(tmp_path: Path, *, complete: bool = True) -> Path:
    payload = {
        "schema_version": "1.0",
        "source_type": "sapclaw-runtime-mcp-export",
        "read_only": True,
        "query_plans_validated": True,
        "exported_at": "2026-07-31T17:30:00+08:00",
        "sap_system": "S4H",
        "sap_client": "100",
        "scope": {
            "company_code": "1010",
            "fiscal_year": 2026,
            "period": 7,
        },
        "currency": "EUR",
        "checks": {
            "AP_OVERDUE_ITEMS": {
                "complete": complete,
                "value": 3,
                "amount": "450.00",
                "currency": "EUR",
                "data_quality_issues": [],
                "evidence": [{"supplier": "V1", "count": 3}],
                "source": {
                    "service_name": "API_OPLACCTGDOCITEMCUBE_SRV",
                    "resource": "A_OperationalAcctgDocItemCube",
                    "case_ids": ["case-ap-1"],
                    "row_count": 3,
                },
            }
        },
    }
    path = tmp_path / "mcp-export.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_mcp_export_is_a_traceable_primary_observation(tmp_path: Path) -> None:
    gateway = SapClawMcpExportGateway.from_file(write_bundle(tmp_path))

    assert gateway.report_currency(CONTEXT) == "EUR"
    observation = gateway.collect(CONTEXT, ap_check())

    assert observation.value == 3
    assert observation.amount == Decimal("450.00")
    assert source_mode(observation.sources) == "mcp"
    assert observation.sources[0].case_ids == ("case-ap-1",)


def test_incomplete_mcp_export_fails_closed_for_se16n_fallback(tmp_path: Path) -> None:
    gateway = SapClawMcpExportGateway.from_file(
        write_bundle(tmp_path, complete=False)
    )

    with pytest.raises(SapDataUnavailable, match="incomplete") as error:
        gateway.collect(CONTEXT, ap_check())

    assert error.value.sources[0].status == "unavailable"


def test_mcp_export_rejects_wrong_client(tmp_path: Path) -> None:
    path = write_bundle(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["sap_client"] = "550"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SapDataUnavailable, match="client mismatch"):
        SapClawMcpExportGateway.from_file(path)
