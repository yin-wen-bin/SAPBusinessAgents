from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .aging import days_overdue
from .models import (
    BankReceipt,
    CustomerAccount,
    DisputeStatus,
    MatchStatus,
    OpenItem,
    PaymentHistory,
    PaymentMatch,
)


@dataclass(frozen=True)
class RiskAssessment:
    score: Decimal
    risk_level: str
    priority: str
    next_action_date: date
    gross_overdue_amount: Decimal
    pending_receipt_amount: Decimal
    net_collection_amount: Decimal
    max_days_overdue: int
    breakdown: dict[str, Decimal]
    reasons: tuple[str, ...]


def _days_component(max_days: int) -> Decimal:
    if max_days <= 0:
        return Decimal("0")
    if max_days <= 15:
        return Decimal("5")
    if max_days <= 30:
        return Decimal("10")
    if max_days <= 60:
        return Decimal("18")
    if max_days <= 90:
        return Decimal("24")
    return Decimal("30")


def assess_risk(
    account: CustomerAccount,
    items: list[OpenItem],
    history: PaymentHistory,
    receipts: dict[str, BankReceipt],
    matches: list[PaymentMatch],
    as_of: date,
) -> RiskAssessment:
    overdue_items = [item for item in items if days_overdue(item, as_of) > 0]
    gross_overdue = sum((item.amount for item in overdue_items), Decimal("0"))
    overdue_document_ids = {item.document_id for item in overdue_items}
    pending_receipt = sum(
        (
            min(receipts[match.receipt_id].amount, next(
                item.amount for item in overdue_items if item.document_id == match.candidate_document_id
            ))
            for match in matches
            if match.status in {MatchStatus.EXACT, MatchStatus.LIKELY}
            and match.candidate_document_id in overdue_document_ids
        ),
        Decimal("0"),
    )
    net_overdue = max(gross_overdue - pending_receipt, Decimal("0"))
    max_days = max((days_overdue(item, as_of) for item in overdue_items), default=0)

    credit_base = account.credit_limit if account.credit_limit > 0 else Decimal("1")
    amount_component = min(net_overdue / credit_base, Decimal("1")) * Decimal("25")
    overdue_days_component = _days_component(max_days)
    history_component = min(
        min(history.average_days_late / Decimal("60"), Decimal("1")) * Decimal("10")
        + (Decimal("1") - history.on_time_rate) * Decimal("8")
        + Decimal(history.broken_promises_12m) * Decimal("2"),
        Decimal("20"),
    )
    utilization = account.current_exposure / credit_base
    credit_component = min(utilization / Decimal("1.5"), Decimal("1")) * Decimal("15")
    receipt_mitigation = (
        min(pending_receipt / gross_overdue, Decimal("1")) * Decimal("15")
        if gross_overdue
        else Decimal("0")
    )
    raw_score = amount_component + overdue_days_component + history_component + credit_component - receipt_mitigation
    score = max(Decimal("0"), min(raw_score, Decimal("100"))).quantize(Decimal("0.1"))

    disputed_amount = sum(
        (item.amount for item in overdue_items if item.dispute_status == DisputeStatus.OPEN),
        Decimal("0"),
    )
    hold = account.dunning_block or (gross_overdue > 0 and disputed_amount == gross_overdue)
    reasons: list[str] = []
    if max_days > 90:
        reasons.append("over_90_days_past_due")
    if utilization > Decimal("1"):
        reasons.append("credit_limit_exceeded")
    if history.on_time_rate < Decimal("0.6"):
        reasons.append("low_historical_on_time_rate")
    if pending_receipt:
        reasons.append("unapplied_receipt_requires_review")
    if disputed_amount:
        reasons.append("open_dispute_requires_review")
    if account.dunning_block:
        reasons.append("customer_dunning_block")

    if hold:
        priority = "HOLD_REVIEW"
        next_action = as_of
    elif score >= Decimal("65") or max_days > 90 or utilization > Decimal("1.2"):
        priority = "P1"
        next_action = as_of
    elif score >= Decimal("45") or max_days > 60:
        priority = "P2"
        next_action = as_of + timedelta(days=1)
    elif score >= Decimal("25"):
        priority = "P3"
        next_action = as_of + timedelta(days=3)
    else:
        priority = "P4"
        next_action = as_of + timedelta(days=5)

    risk_level = "high" if score >= 65 else "medium" if score >= 40 else "low"
    return RiskAssessment(
        score=score,
        risk_level=risk_level,
        priority=priority,
        next_action_date=next_action,
        gross_overdue_amount=gross_overdue,
        pending_receipt_amount=pending_receipt,
        net_collection_amount=net_overdue,
        max_days_overdue=max_days,
        breakdown={
            "overdue_amount": amount_component.quantize(Decimal("0.1")),
            "overdue_days": overdue_days_component,
            "payment_history": history_component.quantize(Decimal("0.1")),
            "credit_utilization": credit_component.quantize(Decimal("0.1")),
            "unapplied_receipt_mitigation": -receipt_mitigation.quantize(Decimal("0.1")),
        },
        reasons=tuple(reasons),
    )
