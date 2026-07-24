from __future__ import annotations

from decimal import Decimal

from .models import CustomerAccount, DisputeStatus, OpenItem
from .scoring import RiskAssessment


def _money(amount: Decimal, currency: str) -> str:
    return f"{amount:,.2f} {currency}"


def build_communication_draft(
    account: CustomerAccount,
    overdue_items: list[OpenItem],
    assessment: RiskAssessment,
    covered_documents: set[str],
) -> dict[str, object]:
    disputed = [item for item in overdue_items if item.dispute_status == DisputeStatus.OPEN]
    review_reasons = list(assessment.reasons)
    review_reasons.append("human_approval_required_before_send")

    if assessment.gross_overdue_amount <= 0:
        return {
            "status": "not_required",
            "channel": None,
            "recipient_role": None,
            "subject": None,
            "body": None,
            "review_reasons": [],
            "send_policy": "no_automatic_send",
            "sent": False,
        }

    if assessment.priority == "HOLD_REVIEW":
        return {
            "status": "withheld",
            "channel": None,
            "recipient_role": "internal_ar_reviewer",
            "subject": None,
            "body": None,
            "review_reasons": review_reasons,
            "send_policy": "no_automatic_send",
            "sent": False,
        }

    collectible_items = [
        item
        for item in overdue_items
        if item.document_id not in covered_documents and item.dispute_status != DisputeStatus.OPEN
    ]
    invoice_lines = "\n".join(
        f"- 发票 {item.invoice_id}，到期日 {item.due_date.isoformat()}，金额 {_money(item.amount, item.currency)}"
        for item in collectible_items
    )
    if not invoice_lines:
        invoice_lines = "- 请先复核未清项目与银行到账的匹配结果。"

    receipt_note = ""
    if assessment.pending_receipt_amount:
        receipt_note = (
            f"\n我们已识别到 {_money(assessment.pending_receipt_amount, account.currency)} 的待复核到账，"
            "下列催收金额已暂不包含相关候选发票。"
        )

    body = (
        f"尊敬的{account.name}财务团队：\n\n"
        "您好。根据我方应收记录，以下款项已到期：\n"
        f"{invoice_lines}\n\n"
        f"当前建议跟进金额为 {_money(assessment.net_collection_amount, account.currency)}。"
        f"{receipt_note}\n"
        "烦请核对并告知预计付款日期；如已付款，请提供银行流水参考号，以便我方核销。\n\n"
        "谢谢。"
    )
    if disputed:
        review_reasons.append("exclude_disputed_items_from_customer_draft")

    return {
        "status": "review_required",
        "channel": "email",
        "recipient_role": "customer_ar_contact",
        "subject": f"应收款项跟进 - {account.name}",
        "body": body,
        "review_reasons": review_reasons,
        "send_policy": "no_automatic_send",
        "sent": False,
    }
