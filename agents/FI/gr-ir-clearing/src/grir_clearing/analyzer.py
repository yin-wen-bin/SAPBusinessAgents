from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable, Sequence

from .models import (
    GrirException,
    HistoryEventType,
    PurchaseOrderHistoryEvent,
    PurchaseOrderItem,
    ReasonCode,
    Severity,
)


@dataclass(frozen=True)
class RuleConfig:
    quantity_tolerance: Decimal = Decimal("0.001")
    amount_tolerance: Decimal = Decimal("0.01")
    long_outstanding_days: int = 90
    high_severity_days: int = 180

    def __post_init__(self) -> None:
        if self.quantity_tolerance < 0 or self.amount_tolerance < 0:
            raise ValueError("tolerances must be non-negative")
        if self.long_outstanding_days < 0 or self.high_severity_days < 0:
            raise ValueError("aging thresholds must be non-negative")


_RESPONSIBILITY = {
    ReasonCode.GR_WITHOUT_IR: "供应商 / 应付账款",
    ReasonCode.IR_WITHOUT_GR: "收货部门 / 采购",
    ReasonCode.QUANTITY_DIFFERENCE: "收货部门 / 采购 / 应付账款",
    ReasonCode.PRICE_DIFFERENCE: "采购 / 应付账款",
    ReasonCode.RETURN_PENDING: "采购 / 供应商 / 应付账款",
    ReasonCode.LONG_OUTSTANDING: "总账会计 / 采购",
}

_RECOMMENDATION = {
    ReasonCode.GR_WITHOUT_IR: "核实供应商发票状态；应收未收则催票并过账，确定不再开票则按审批政策评估 MR11。",
    ReasonCode.IR_WITHOUT_GR: "核实实物和入库凭证；已收货则补做收货，未收货或发票错误则冲销/更正发票。",
    ReasonCode.QUANTITY_DIFFERENCE: "逐笔核对交货、收货和发票数量；补录缺失凭证，或冲销错误收货并取得贷项凭证。",
    ReasonCode.PRICE_DIFFERENCE: "核对 PO 条件与发票价格；更正 PO/发票或取得借贷项凭证，仅对确认无需后续业务的尾差执行 MR11。",
    ReasonCode.RETURN_PENDING: "核对退货物料凭证和供应商贷项凭证；催收并过账贷项，或冲销错误退货/发票。",
    ReasonCode.LONG_OUTSTANDING: "发起跨部门清理；确认不再发生后续收货/发票且审批完成后，使用 MR11 清理余额并留存依据。",
}


class GrirAnalyzer:
    def __init__(self, config: RuleConfig | None = None) -> None:
        self.config = config or RuleConfig()

    def analyze_item(
        self,
        po: PurchaseOrderItem,
        history: Sequence[PurchaseOrderHistoryEvent],
        as_of_date: date,
    ) -> GrirException | None:
        relevant = tuple(event for event in history if event.posting_date <= as_of_date)
        gr_events = tuple(event for event in relevant if event.is_gr_side)
        ir_events = tuple(event for event in relevant if not event.is_gr_side)
        gr_quantity = sum((event.quantity for event in gr_events), Decimal("0"))
        ir_quantity = sum((event.quantity for event in ir_events), Decimal("0"))
        gr_amount = sum((event.amount for event in gr_events), Decimal("0"))
        ir_amount = sum((event.amount for event in ir_events), Decimal("0"))
        quantity_difference = gr_quantity - ir_quantity
        amount_difference = gr_amount - ir_amount

        primary = self._classify(
            relevant,
            gr_quantity,
            ir_quantity,
            quantity_difference,
            amount_difference,
        )
        if primary is None:
            return None

        use_quantity = abs(quantity_difference) > self.config.quantity_tolerance
        contribution: Callable[[PurchaseOrderHistoryEvent], Decimal]
        if use_quantity:
            contribution = lambda event: event.quantity if event.is_gr_side else -event.quantity
            tolerance = self.config.quantity_tolerance
        else:
            contribution = lambda event: event.amount if event.is_gr_side else -event.amount
            tolerance = self.config.amount_tolerance
        oldest_open_date = self._current_open_cycle_start(relevant, contribution, tolerance)
        age_days = max(0, (as_of_date - oldest_open_date).days)

        reasons = [primary]
        if age_days >= self.config.long_outstanding_days and primary != ReasonCode.LONG_OUTSTANDING:
            reasons.append(ReasonCode.LONG_OUTSTANDING)

        severity = Severity.LOW
        if primary == ReasonCode.RETURN_PENDING or age_days >= self.config.high_severity_days:
            severity = Severity.HIGH
        elif age_days >= self.config.long_outstanding_days or primary in {
            ReasonCode.IR_WITHOUT_GR,
            ReasonCode.QUANTITY_DIFFERENCE,
        }:
            severity = Severity.MEDIUM

        recommendation = _RECOMMENDATION[primary]
        if ReasonCode.LONG_OUTSTANDING in reasons and primary != ReasonCode.LONG_OUTSTANDING:
            recommendation = f"{recommendation} 因账龄已超阈值，同时执行长期挂账复核。"

        return GrirException(
            po=po,
            primary_reason=primary,
            reasons=tuple(reasons),
            gr_quantity=gr_quantity,
            ir_quantity=ir_quantity,
            quantity_difference=quantity_difference,
            gr_amount=gr_amount,
            ir_amount=ir_amount,
            amount_difference=amount_difference,
            oldest_open_date=oldest_open_date,
            age_days=age_days,
            responsibility=_RESPONSIBILITY[primary],
            recommendation=recommendation,
            severity=severity,
            history_documents=tuple(dict.fromkeys(event.document_number for event in relevant)),
        )

    def _classify(
        self,
        history: Sequence[PurchaseOrderHistoryEvent],
        gr_quantity: Decimal,
        ir_quantity: Decimal,
        quantity_difference: Decimal,
        amount_difference: Decimal,
    ) -> ReasonCode | None:
        qtol = self.config.quantity_tolerance
        atol = self.config.amount_tolerance
        has_return = any(event.event_type == HistoryEventType.GOODS_RETURN for event in history)

        if has_return and ir_quantity - gr_quantity > qtol:
            return ReasonCode.RETURN_PENDING
        if gr_quantity > qtol and abs(ir_quantity) <= qtol:
            return ReasonCode.GR_WITHOUT_IR
        if ir_quantity > qtol and abs(gr_quantity) <= qtol:
            return ReasonCode.IR_WITHOUT_GR
        if abs(quantity_difference) > qtol:
            return ReasonCode.QUANTITY_DIFFERENCE
        if abs(amount_difference) > atol:
            return ReasonCode.PRICE_DIFFERENCE
        return None

    @staticmethod
    def _current_open_cycle_start(
        history: Sequence[PurchaseOrderHistoryEvent],
        contribution: Callable[[PurchaseOrderHistoryEvent], Decimal],
        tolerance: Decimal,
    ) -> date:
        if not history:
            raise ValueError("cannot calculate aging without PO history")
        balance = Decimal("0")
        anchor: date | None = None
        ordered = sorted(history, key=lambda event: (event.posting_date, event.event_id))
        for event in ordered:
            previous = balance
            balance += contribution(event)
            if abs(balance) <= tolerance:
                balance = Decimal("0")
                anchor = None
            elif abs(previous) <= tolerance or previous * balance < 0:
                anchor = event.posting_date
        return anchor or ordered[0].posting_date
