from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path

import pytest

from sap_business_agents_platform.agent_rules import evaluate_business_agent
from sap_business_agents_platform.manifests import validate_manifest
from sap_business_agents_platform.month_end import (
    prepare_month_end_scope,
    resolve_month_end_skill_requirements,
)
from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform.workflows import validate_workflow


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "agents" / "FI" / "month-end-closing" / "agent.json"
EXAMPLE_PROFILE = ROOT / "agents" / "FI" / "month-end-closing" / "config" / "profiles.example.json"


def _payload(rows: list[dict[str, object]], *, step_id: str = "rows", complete: bool = True) -> dict[str, object]:
    return {
        "ok": True,
        "source_complete": complete,
        "data": {
            "source_complete": complete,
            "source_truncated": not complete,
            "results": rows,
            "step_results": {
                step_id: {
                    "source_complete": complete,
                    "source_truncated": not complete,
                    "results": rows,
                }
            },
        },
    }


def _profile_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    payload = json.loads(EXAMPLE_PROFILE.read_text(encoding="utf-8"))
    profile = payload["profiles"][0]
    profile["company_code"] = "1710"
    profile["sap_client"] = "100"
    profile["effective_from"] = "2020-01-01"
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("SAPBA_MONTH_END_PROFILE_PATH", str(path))
    monkeypatch.setenv("SAPBA_SAP_SYSTEM_ALIAS", "default")
    monkeypatch.setenv("SAP_CLIENT", "100")
    return path


def _scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    _profile_file(tmp_path, monkeypatch)
    return prepare_month_end_scope(
        {
            "run_input": {
                "company_code": "1710",
                "fiscal_year": "2020",
                "period": 1,
                "as_of": "2020-01-31",
            },
            "company_evidence": _payload(
                [
                    {
                        "CompanyCode": "1710",
                        "Currency": "USD",
                        "FiscalYearVariant": "K4",
                    }
                ],
                step_id="company_metadata",
            ),
            "ledger_evidence": _payload(
                [
                    {"Ledger": "0L", "IsLeadingLedger": True},
                    {"Ledger": "2L", "IsLeadingLedger": False},
                ],
                step_id="ledger_metadata",
            ),
        }
    )


def _evaluation_payload(scope: dict[str, object]) -> dict[str, object]:
    gl_rows = [
        {
            "Ledger": "0L",
            "CompanyCode": "1710",
            "FiscalYear": "2020",
            "FiscalPeriod": "1",
            "AccountingDocument": "1900000001",
            "AccountingDocumentItem": "1",
            "LedgerGLLineItem": "1",
            "Supplier": "17300001",
            "PostingDate": "2020-01-02",
            "NetDueDate": "2020-01-15",
            "ClearingDate": "",
            "AmountInCompanyCodeCurrency": "100.00",
            "CompanyCodeCurrency": "USD",
            "DebitCreditCode": "H",
        }
    ]
    billing_rows: list[dict[str, object]] = []
    grir_steps = {
        name: {"source_complete": True, "source_truncated": False, "results": []}
        for name in (
            "gl_items",
            "grir_gl_history",
            "purchase_order_items",
            "material_documents",
            "material_document_headers",
            "supplier_invoice_items",
            "supplier_invoice_headers",
        )
    }
    return {
        "agent_id": "month-end-closing",
        "run_input": {
            "company_code": "1710",
            "fiscal_year": "2020",
            "period": 1,
            "as_of": "2020-01-31",
        },
        "scope": scope,
        "evidence": {
            "gl_line_items": _payload(gl_rows, step_id="gl_line_items"),
            "due_items": _payload(gl_rows, step_id="due_items"),
            "billing_documents": _payload(billing_rows, step_id="billing_documents"),
            "grir_chain": {
                "ok": True,
                "source_complete": True,
                "data": {"source_complete": True, "step_results": grir_steps},
            },
        },
        "fallbacks": {
            "asset_depreciation": {
                "status": "complete",
                "read_only": True,
                "validated": True,
                "rows": [{"BUKRS": "1710", "AFBLGJ": "2020", "AFBLPE": "1"}],
                "completeness": {"source_complete": True, "paging_complete": True},
            },
            "fi_period_control": {
                "status": "complete",
                "read_only": True,
                "validated": True,
                "rows": [
                    {
                        "BUKRS": "1000",
                        "FRYE1": "2020",
                        "FRPE1": "2",
                        "TOYE1": "2020",
                        "TOPE1": "12",
                    }
                ],
                "completeness": {"source_complete": True, "paging_complete": True},
            },
            "mm_period_status": {
                "status": "complete",
                "read_only": True,
                "validated": True,
                "rows": [{"BUKRS": "1710", "LFGJA": "2020", "LFMON": "2"}],
                "completeness": {"source_complete": True, "paging_complete": True},
            },
        },
    }


def test_month_end_manifest_uses_embedded_get_and_pending_acceptance() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    validate_manifest(manifest, MANIFEST)

    assert manifest["version"] == "0.2.0"
    assert manifest["validation"]["verdict"] == "NOT_TESTED"
    assert manifest["validation"]["baselineRuntime"] == "embedded-odata"
    assert manifest["execution"]["inputSchema"]["properties"]["period"] == {
        "type": "integer",
        "title": {"zh": "会计期间", "en": "Fiscal period"},
        "minimum": 1,
        "maximum": 16,
    }
    sap_steps = [step for step in manifest["execution"]["steps"] if step["executor"] == "sap_read"]
    assert sap_steps
    assert all(step["request"]["plan"]["http_method"] == "GET" for step in sap_steps)
    due_plan = next(
        step["request"]["plan"]
        for step in sap_steps
        if step["id"] == "read_due_items"
    )
    assert {item["field"] for item in due_plan["filters"]} == {
        "CompanyCode",
        "Ledger",
        "FinancialAccountType",
    }
    assert next(
        item for item in due_plan["filters"] if item["field"] == "FinancialAccountType"
    )["value"] == "K"
    assert due_plan["partition"] == {
        "field": "PostingDate",
        "strategy": "adaptive_date",
        "from": "1900-01-01",
        "to": "{{input.as_of}}",
        "maxPartitions": 366,
        "maxTotalResults": 50000,
    }
    text = MANIFEST.read_text(encoding="utf-8")
    assert "sapclaw_runtime" not in text
    assert "Thin Runtime" not in text


def test_prepare_scope_resolves_k4_leading_ledger_and_profile_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _scope(tmp_path, monkeypatch)

    assert scope["status"] == "complete"
    assert scope["ledger"] == "0L"
    assert scope["period_start"] == "2020-01-01"
    assert scope["period_end"] == "2020-01-31"
    assert scope["profile_id"] == "example-1010"
    assert str(scope["profile_hash"]).startswith("sha256:")
    assert scope["grir_enabled"] is True


def test_missing_profile_fails_closed_without_discarding_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAPBA_MONTH_END_PROFILE_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setenv("SAP_CLIENT", "100")
    scope = prepare_month_end_scope(
        {
            "run_input": {
                "company_code": "1710",
                "fiscal_year": "2020",
                "period": 1,
                "as_of": "2020-01-31",
            },
            "company_evidence": _payload(
                [{"CompanyCode": "1710", "Currency": "USD", "FiscalYearVariant": "K4"}]
            ),
            "ledger_evidence": _payload([{"Ledger": "0L", "IsLeadingLedger": True}]),
        }
    )

    assert scope["metadata_complete"] is True
    assert scope["profile_id"] == ""
    assert "month_end_profile_registry_missing" in scope["config_gaps"]


def test_future_as_of_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _profile_file(tmp_path, monkeypatch)
    future = (date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="future"):
        prepare_month_end_scope(
            {
                "run_input": {
                    "company_code": "1710",
                    "fiscal_year": str(date.today().year),
                    "period": 1,
                    "as_of": future,
                },
                "company_evidence": _payload([]),
                "ledger_evidence": _payload([]),
            }
        )


def test_skill_supplement_requires_profile_approval_and_confirmed_api_gap() -> None:
    result = resolve_month_end_skill_requirements(
        {
            "scope": {
                "skill_requirements": {
                    "asset_depreciation": True,
                    "fi_period_control": False,
                    "mm_period_status": True,
                }
            },
            "assessment": {
                "needs_adt": {
                    "asset_depreciation": True,
                    "fi_period_control": True,
                    "mm_period_status": False,
                }
            },
        }
    )

    assert result["asset_depreciation"] is True
    assert result["fi_period_control"] is False
    assert result["mm_period_status"] is False


def test_twelve_checks_share_one_evidence_bundle_and_fx_gap_is_honest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _scope(tmp_path, monkeypatch)
    result = evaluate_business_agent(_evaluation_payload(scope))

    checks = result["workflow_output"]["check_results"]
    by_id = {item["check_id"]: item for item in checks}
    assert len(checks) == 12
    assert by_id["AP_OVERDUE_ITEMS"]["status"] == "attention"
    assert by_id["AP_OVERDUE_ITEMS"]["actual_value"] == 1
    assert by_id["GL_FX_VALUATION_PENDING"]["status"] == "not_assessed"
    assert result["business_status"] == "inconclusive"
    assert result["checklist_complete"] is False
    assert "fx_valuation_run_status_source_unavailable" in result["missing_evidence"]
    assert result["business_report"]["provider"] == {
        "id": "embedded-odata",
        "capability": "sap_read.v2",
        "read_only": True,
        "automatic_fallback": False,
    }
    assert result["business_report"]["safety"]["closing_action_executed"] is False


def test_incomplete_source_cannot_turn_empty_rows_into_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _scope(tmp_path, monkeypatch)
    payload = _evaluation_payload(scope)
    payload["evidence"]["gl_line_items"] = _payload([], step_id="gl_line_items", complete=False)
    payload["evidence"]["due_items"] = _payload([], step_id="due_items", complete=False)
    result = evaluate_business_agent(payload)
    by_id = {item["check_id"]: item for item in result["workflow_output"]["check_results"]}

    assert by_id["AP_OVERDUE_ITEMS"]["status"] == "not_assessed"
    assert result["source_complete"] is False
    assert result["business_status"] == "inconclusive"


def test_follow_up_scopes_are_bounded_and_do_not_change_primary_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _scope(tmp_path, monkeypatch)
    payload = _evaluation_payload(scope)
    template = payload["evidence"]["gl_line_items"]["data"]["results"][0]
    rows = []
    for index in range(55):
        row = deepcopy(template)
        row["Supplier"] = f"{index:08d}"
        row["AccountingDocument"] = f"19{index:08d}"
        rows.append(row)
    payload["evidence"]["gl_line_items"] = _payload(rows, step_id="gl_line_items")
    payload["evidence"]["due_items"] = _payload(rows, step_id="due_items")
    result = evaluate_business_agent(payload)

    assert len(result["workflow_output"]["ap_follow_up_scopes"]) == 50
    assert result["workflow_output"]["follow_up_scope_complete"] is False
    assert result["business_status"] == "inconclusive"


def test_optional_follow_up_workflow_is_typed_bounded_and_inactive() -> None:
    workflow_path = ROOT / "workflows" / "Common" / "month-end-exception-follow-up"
    workflow = json.loads((workflow_path / "workflow.json").read_text(encoding="utf-8"))
    publication = json.loads((workflow_path / "publication.json").read_text(encoding="utf-8"))

    assert validate_workflow(workflow, AgentRepository(ROOT / "agents")) == []
    foreach_nodes = [node for node in workflow["nodes"] if "forEach" in node]
    assert len(foreach_nodes) == 2
    assert all(node["forEach"]["maxItems"] == 50 for node in foreach_nodes)
    assert all(node["forEach"]["maxConcurrency"] == 4 for node in foreach_nodes)
    assert all(
        node["forEach"]["onItemError"] == "collect_inconclusive"
        for node in foreach_nodes
    )
    assert publication["state"] == "inactive"
