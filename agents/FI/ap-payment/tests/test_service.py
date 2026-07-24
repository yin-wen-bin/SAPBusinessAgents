from datetime import date

from ap_payment_assistant.mock_adapter import MockSapApDataAdapter
from ap_payment_assistant.service import ApPaymentAssistant


AS_OF = date(2026, 7, 22)


def assistant() -> ApPaymentAssistant:
    return ApPaymentAssistant(MockSapApDataAdapter())


def test_typical_next_week_query_returns_items_totals_and_risks() -> None:
    result = assistant().ask("供应商 10001234 下周有哪些到期应付款？", as_of=AS_OF)

    assert result.ok is True
    assert result.summary["matched_items"] == 5
    assert result.summary["amount_by_currency"] == {"CNY": "43000.00"}
    assert {risk.rule_id for risk in result.risks} == {
        "DUPLICATE_INVOICE_REFERENCE",
        "DUPLICATE_AMOUNT",
        "PAYMENT_BLOCK",
    }
    assert result.items[0]["status"] == "scheduled"
    assert result.trace["source_objects"] == ["BKPF", "BSEG", "BSIK", "REGUH", "REGUP"]


def test_paid_invoice_status_includes_clearing_evidence() -> None:
    result = assistant().ask("发票 INV-PAID-001 付款了吗？", as_of=AS_OF)

    assert result.ok is True
    assert result.items[0]["status"] == "paid"
    assert result.items[0]["clearing_document"] == "2000000456"
    assert result.risks == ()


def test_full_risk_check_detects_overdue_item() -> None:
    result = assistant().ask("检查供应商 10001234 的付款风险", as_of=AS_OF)

    overdue = [risk for risk in result.risks if risk.rule_id == "OVERDUE_PAYMENT"]
    assert len(overdue) == 1
    assert overdue[0].evidence["overdue_days"] == 12


def test_risk_check_respects_an_explicit_due_window() -> None:
    result = assistant().ask("检查供应商 10001234 下周付款风险", as_of=AS_OF)

    assert result.summary["matched_items"] == 5
    assert all(item["due_date"] >= "2026-07-27" for item in result.items)
    assert "OVERDUE_PAYMENT" not in {risk.rule_id for risk in result.risks}


def test_unverified_cross_border_bank_account_is_high_risk_and_masked() -> None:
    result = assistant().ask("检查供应商 10004567 的付款风险", as_of=AS_OF)

    finding = next(risk for risk in result.risks if risk.rule_id == "ABNORMAL_BANK_ACCOUNT")
    assert finding.severity.value == "high"
    assert finding.evidence["masked_account"] == "********5519"
    assert "bank_country" in finding.evidence
    assert "完整" not in finding.explanation


def test_missing_vendor_returns_actionable_validation_error() -> None:
    result = assistant().ask("查询未来30天的到期项目", as_of=AS_OF)

    assert result.ok is False
    assert result.errors == ("该查询需要供应商编号",)
    assert result.items == ()


def test_unspecified_due_window_defaults_to_thirty_days() -> None:
    result = assistant().ask("供应商 10001234 有哪些到期应付款？", as_of=AS_OF)

    assert result.parameters.due_from == AS_OF
    assert result.parameters.due_to == date(2026, 8, 21)
    assert "未指定到期范围，默认未来30天" in result.trace["extraction_notes"]


def test_response_is_json_serializable_shape() -> None:
    payload = assistant().ask(
        "供应商 10001234 下周有哪些到期应付款？", as_of=AS_OF
    ).to_dict()

    assert payload["intent"] == "upcoming_due"
    assert payload["parameters"]["as_of"] == "2026-07-22"
    assert payload["risks"][0]["severity"] == "high"
