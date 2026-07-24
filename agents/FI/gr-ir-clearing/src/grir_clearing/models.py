from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum


class HistoryEventType(StrEnum):
    GOODS_RECEIPT = "goods_receipt"
    GOODS_RECEIPT_REVERSAL = "goods_receipt_reversal"
    GOODS_RETURN = "goods_return"
    INVOICE_RECEIPT = "invoice_receipt"
    INVOICE_REVERSAL = "invoice_reversal"
    CREDIT_MEMO = "credit_memo"


class ReasonCode(StrEnum):
    GR_WITHOUT_IR = "gr_without_ir"
    IR_WITHOUT_GR = "ir_without_gr"
    QUANTITY_DIFFERENCE = "quantity_difference"
    PRICE_DIFFERENCE = "price_difference"
    RETURN_PENDING = "return_pending"
    LONG_OUTSTANDING = "long_outstanding"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, order=True)
class PurchaseOrderKey:
    po_number: str
    po_item: str


@dataclass(frozen=True)
class PurchaseOrderItem:
    key: PurchaseOrderKey
    company_code: str
    plant: str
    vendor: str
    currency: str
    ordered_quantity: Decimal
    net_price: Decimal
    price_unit: Decimal = Decimal("1")
    material: str = ""
    description: str = ""
    purchasing_group: str = ""

    def __post_init__(self) -> None:
        if self.price_unit <= 0:
            raise ValueError("price_unit must be greater than zero")


@dataclass(frozen=True)
class PurchaseOrderHistoryEvent:
    key: PurchaseOrderKey
    event_id: str
    event_type: HistoryEventType
    posting_date: date
    quantity: Decimal
    amount: Decimal
    currency: str
    document_number: str
    fiscal_year: str = ""
    movement_type: str = ""
    reference: str = ""

    @property
    def is_gr_side(self) -> bool:
        return self.event_type in {
            HistoryEventType.GOODS_RECEIPT,
            HistoryEventType.GOODS_RECEIPT_REVERSAL,
            HistoryEventType.GOODS_RETURN,
        }


@dataclass(frozen=True)
class AnalysisCriteria:
    as_of_date: date
    company_code: str | None = None
    plant: str | None = None
    po_number: str | None = None
    activity_from: date | None = None
    activity_to: date | None = None

    def __post_init__(self) -> None:
        if self.activity_from and self.activity_to and self.activity_from > self.activity_to:
            raise ValueError("activity_from must not be after activity_to")
        if self.activity_from and self.activity_from > self.as_of_date:
            raise ValueError("activity_from must not be after as_of_date")
        if self.activity_to and self.activity_to > self.as_of_date:
            raise ValueError("activity_to must not be after as_of_date")


@dataclass(frozen=True)
class GrirException:
    po: PurchaseOrderItem
    primary_reason: ReasonCode
    reasons: tuple[ReasonCode, ...]
    gr_quantity: Decimal
    ir_quantity: Decimal
    quantity_difference: Decimal
    gr_amount: Decimal
    ir_amount: Decimal
    amount_difference: Decimal
    oldest_open_date: date
    age_days: int
    responsibility: str
    recommendation: str
    severity: Severity
    history_documents: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AnalysisSummary:
    examined_po_items: int
    exception_count: int
    total_absolute_amount_difference: Decimal
    counts_by_reason: dict[str, int]
    counts_by_responsibility: dict[str, int]


@dataclass(frozen=True)
class AnalysisReport:
    criteria: AnalysisCriteria
    source_name: str
    items: tuple[GrirException, ...]
    summary: AnalysisSummary
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
