from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import ModuleType

from sap_business_agents.month_end_closing import (
    ClosingConclusion,
    ClosingContext,
    FixtureSapGateway,
    MonthEndClosingAssistant,
    load_checklist,
)
from sap_business_agents.month_end_closing.cli import main, parse_question


ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "month_end_checklist.toml"
FIXTURE = ROOT / "fixtures" / "1010_2026_07.json"
FIXED_TIME = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


def fixture_payload() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def assistant(payload: dict[str, object]) -> MonthEndClosingAssistant:
    return MonthEndClosingAssistant(
        load_checklist(CONFIG),
        FixtureSapGateway(payload),
        clock=lambda: FIXED_TIME,
    )


def clean_payload() -> dict[str, object]:
    payload = fixture_payload()
    for observation in payload["checks"].values():  # type: ignore[union-attr]
        observation["value"] = 0
        observation["amount"] = "0.00"
        observation["evidence"] = []
    return payload


def test_fixture_slice_returns_blocked_report_with_traceable_todos() -> None:
    report = assistant(fixture_payload()).assess(ClosingContext("1010", 2026, 7))
    payload = report.to_dict()

    assert report.conclusion is ClosingConclusion.BLOCKED
    assert payload["summary"]["checks_total"] == 12
    assert payload["summary"]["checks_passed"] == 4
    assert payload["summary"]["checks_exception"] == 8
    assert payload["summary"]["blocking_total"] == 3
    assert payload["summary"]["total_exception_amount"] == "349300.00"
    assert payload["summary"]["currency"] == "CNY"
    assert payload["report_currency"] == "CNY"
    assert payload["summary"]["by_module"]["FI-AA"]["count"] == 1
    assert payload["summary"]["by_department"]["Asset Accounting"]["count"] == 1
    assert payload["summary"]["by_severity"]["critical"]["count"] == 3
    assert payload["safety"]["closing_action_executed"] is False
    assert len(report.todos) == len(report.findings)
    assert report.todos[0].todo_id == "TODO-1010-202607-AP_OVERDUE_ITEMS"
    assert any(item.requires_human_confirmation for item in report.todos)


def test_clean_data_is_ready_but_never_executes_closing() -> None:
    report = assistant(clean_payload()).assess(ClosingContext("1010", 2026, 7))

    assert report.conclusion is ClosingConclusion.READY
    assert not report.findings
    assert not report.todos
    assert report.to_dict()["safety"]["closing_action_requires_human_confirmation"] is True


def test_platform_evidence_mode_uses_shared_production_rule(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    evidence = {
        "run_input": {},
        "scope": {},
        "evidence": {},
        "fallbacks": {},
    }
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    module = ModuleType("sap_business_agents_platform.month_end")
    module.evaluate_month_end_closing = lambda payload: {  # type: ignore[attr-defined]
        "business_status": "inconclusive",
        "received_shared_bundle": payload == evidence,
    }
    monkeypatch.setitem(sys.modules, "sap_business_agents_platform.month_end", module)

    assert main(["--platform-evidence", str(path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "business_status": "inconclusive",
        "received_shared_bundle": True,
    }


def test_pre_close_snapshot_is_not_reported_as_final_ready() -> None:
    pre_close = MonthEndClosingAssistant(
        load_checklist(CONFIG),
        FixtureSapGateway(clean_payload()),
        clock=lambda: datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc),
    ).assess(ClosingContext("1010", 2026, 7))

    assert pre_close.conclusion is ClosingConclusion.NOT_RECOMMENDED
    timing = pre_close.to_dict()["assessment_timing"]
    assert timing["is_pre_close_snapshot"] is True
    assert timing["final_close_certification"] is False


def test_scope_failure_never_falls_back_to_checklist_currency() -> None:
    payload = deepcopy(clean_payload())
    payload["currency"] = ""
    report = assistant(payload).assess(ClosingContext("1010", 2026, 7))

    assert report.report_currency == "UNRESOLVED"
    assert report.summary["checks_error"] == 12


def test_nonblocking_material_exception_is_not_recommended() -> None:
    payload = clean_payload()
    payload["checks"]["AP_OVERDUE_ITEMS"] = {  # type: ignore[index]
        "value": 1,
        "amount": "10.00",
        "evidence": [],
    }
    report = assistant(payload).assess(ClosingContext("1010", 2026, 7))

    assert report.conclusion is ClosingConclusion.NOT_RECOMMENDED
    assert report.summary["blocking_total"] == 0


def test_missing_required_observation_fails_closed() -> None:
    payload = deepcopy(clean_payload())
    del payload["checks"]["AR_UNAPPLIED_RECEIPTS"]  # type: ignore[index]
    report = assistant(payload).assess(ClosingContext("1010", 2026, 7))

    assert report.conclusion is ClosingConclusion.BLOCKED
    assert report.summary["checks_error"] == 1
    finding = next(item for item in report.findings if item.check_id == "AR_UNAPPLIED_RECEIPTS")
    assert finding.blocking is True
    assert finding.owner_department == "Accounts Receivable"
    assert finding.owner == "AR Closing Team"
    assert "SAP Support" in finding.remediation


def test_non_normalized_currency_fails_closed_instead_of_mixing_amounts() -> None:
    payload = clean_payload()
    payload["checks"]["CO_UNALLOCATED_COSTS"]["currency"] = "USD"  # type: ignore[index]
    report = assistant(payload).assess(ClosingContext("1010", 2026, 7))

    assert report.conclusion is ClosingConclusion.BLOCKED
    assert report.summary["checks_error"] == 1
    finding = next(item for item in report.findings if item.check_id == "CO_UNALLOCATED_COSTS")
    assert "not normalized" in finding.message
    assert finding.amount == 0


def test_parse_typical_chinese_question() -> None:
    context = parse_question("检查 2026 年 7 月公司代码 1010 的月结状态。")

    assert context == ClosingContext("1010", 2026, 7)


def test_cli_prints_structured_json(capsys) -> None:
    exit_code = main(
        [
            "--question",
            "检查 2026 年 7 月公司代码 1010 的月结状态。",
            "--config",
            str(CONFIG),
            "--fixture",
            str(FIXTURE),
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert result["scope"] == {"company_code": "1010", "fiscal_year": 2026, "period": 7}
    assert result["conclusion"] == "存在阻塞项"
