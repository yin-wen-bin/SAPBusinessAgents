from datetime import date

from ap_payment_assistant.intent import ApIntentParser
from ap_payment_assistant.models import Intent


AS_OF = date(2026, 7, 22)


def test_extracts_vendor_and_next_week_window() -> None:
    parsed = ApIntentParser().parse(
        "供应商 10001234 下周有哪些到期应付款？", as_of=AS_OF
    )

    assert parsed.intent == Intent.UPCOMING_DUE
    assert parsed.parameters.vendor_id == "10001234"
    assert parsed.parameters.due_from == date(2026, 7, 27)
    assert parsed.parameters.due_to == date(2026, 8, 2)
    assert parsed.extraction_notes == ("下周按周一至周日解析",)


def test_extracts_invoice_reference_for_payment_status() -> None:
    parsed = ApIntentParser().parse("发票 INV-PAID-001 付款了吗？", as_of=AS_OF)

    assert parsed.intent == Intent.INVOICE_STATUS
    assert parsed.parameters.invoice_reference == "INV-PAID-001"


def test_extracts_company_document_and_fiscal_year() -> None:
    parsed = ApIntentParser().parse(
        "公司代码 1000 会计凭证 1900000005 财年 2026 的付款状态", as_of=AS_OF
    )

    assert parsed.intent == Intent.INVOICE_STATUS
    assert parsed.parameters.company_code == "1000"
    assert parsed.parameters.accounting_document == "1900000005"
    assert parsed.parameters.fiscal_year == 2026


def test_risk_intent_has_precedence_over_due_window() -> None:
    parsed = ApIntentParser().parse("检查供应商 10001234 下周付款风险", as_of=AS_OF)

    assert parsed.intent == Intent.PAYMENT_RISK
    assert parsed.parameters.due_from == date(2026, 7, 27)


def test_explicit_date_range_is_normalized_when_reversed() -> None:
    parsed = ApIntentParser().parse(
        "供应商 10001234 在 2026-08-02 至 2026-07-27 到期的项目", as_of=AS_OF
    )

    assert parsed.parameters.due_from == date(2026, 7, 27)
    assert parsed.parameters.due_to == date(2026, 8, 2)

