from __future__ import annotations

from datetime import date
from decimal import Decimal

from .models import BankReceipt, CustomerAccount, MatchStatus, OpenItem, PaymentMatch


def _normalize(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _score_candidate(
    receipt: BankReceipt,
    item: OpenItem,
    account: CustomerAccount,
    as_of: date,
) -> tuple[Decimal, tuple[str, ...]]:
    if receipt.currency != item.currency:
        return Decimal("0"), ("currency_mismatch",)

    score = Decimal("0")
    reasons: list[str] = []
    receipt_reference = _normalize(receipt.reference)
    item_references = {
        _normalize(item.invoice_id),
        _normalize(item.document_id),
        _normalize(item.reference),
    } - {""}

    if receipt_reference and receipt_reference in item_references:
        score += Decimal("0.55")
        reasons.append("reference_exact")
    elif receipt_reference and any(
        len(reference) >= 4
        and (reference in receipt_reference or receipt_reference in reference)
        for reference in item_references
    ):
        score += Decimal("0.35")
        reasons.append("reference_partial")

    difference = abs(receipt.amount - item.amount)
    if difference == 0:
        score += Decimal("0.30")
        reasons.append("amount_exact")
    elif item.amount and difference / item.amount <= Decimal("0.02"):
        score += Decimal("0.15")
        reasons.append("amount_within_2_percent")

    payer = _normalize(receipt.payer_name)
    customer_name = _normalize(account.name)
    if payer and customer_name and (payer in customer_name or customer_name in payer):
        score += Decimal("0.10")
        reasons.append("payer_name_match")

    if item.posting_date <= receipt.value_date <= as_of:
        score += Decimal("0.05")
        reasons.append("value_date_plausible")

    return min(score, Decimal("1")), tuple(reasons)


def match_receipts(
    receipts: tuple[BankReceipt, ...],
    items: tuple[OpenItem, ...],
    accounts: tuple[CustomerAccount, ...],
    as_of: date,
) -> tuple[PaymentMatch, ...]:
    """Suggest matches only; this function never clears or posts in SAP."""
    account_by_id = {account.customer_id: account for account in accounts}
    reserved_documents: set[str] = set()
    matches: list[PaymentMatch] = []

    for receipt in sorted(receipts, key=lambda candidate: candidate.receipt_id):
        candidates: list[tuple[Decimal, OpenItem, tuple[str, ...]]] = []
        for item in items:
            if item.document_id in reserved_documents:
                continue
            account = account_by_id[item.customer_id]
            score, reasons = _score_candidate(receipt, item, account, as_of)
            candidates.append((score, item, reasons))

        candidates.sort(key=lambda candidate: (candidate[0], candidate[1].document_id), reverse=True)
        if not candidates or candidates[0][0] < Decimal("0.65"):
            matches.append(
                PaymentMatch(
                    receipt_id=receipt.receipt_id,
                    status=MatchStatus.UNMATCHED,
                    confidence=Decimal("0"),
                    candidate_document_id=None,
                    candidate_invoice_id=None,
                    candidate_customer_id=None,
                    reasons=("no_candidate_above_threshold",),
                )
            )
            continue

        score, item, reasons = candidates[0]
        ambiguous = len(candidates) > 1 and score - candidates[1][0] < Decimal("0.10")
        if ambiguous:
            matches.append(
                PaymentMatch(
                    receipt_id=receipt.receipt_id,
                    status=MatchStatus.UNMATCHED,
                    confidence=score,
                    candidate_document_id=None,
                    candidate_invoice_id=None,
                    candidate_customer_id=None,
                    reasons=reasons + ("ambiguous_top_candidates",),
                )
            )
            continue

        status = (
            MatchStatus.EXACT
            if "reference_exact" in reasons and "amount_exact" in reasons
            else MatchStatus.LIKELY
        )
        reserved_documents.add(item.document_id)
        matches.append(
            PaymentMatch(
                receipt_id=receipt.receipt_id,
                status=status,
                confidence=score.quantize(Decimal("0.01")),
                candidate_document_id=item.document_id,
                candidate_invoice_id=item.invoice_id,
                candidate_customer_id=item.customer_id,
                reasons=reasons,
            )
        )

    return tuple(matches)
