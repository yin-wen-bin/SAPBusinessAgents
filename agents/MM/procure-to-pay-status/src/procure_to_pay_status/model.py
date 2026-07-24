"""Domain models for the Procure-to-Pay status assistant."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any, Mapping


SapRow = Mapping[str, Any]


class ItemStatus(StrEnum):
    """The most useful business state for a PO item."""

    CANCELLED = "cancelled"
    NOT_RECEIVED = "not_received"
    PARTIALLY_RECEIVED = "partially_received"
    RECEIVED_NOT_INVOICED = "received_not_invoiced"
    PARTIALLY_INVOICED = "partially_invoiced"
    INVOICED_NOT_PAID = "invoiced_not_paid"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"


STATUS_LABELS_ZH: dict[ItemStatus, str] = {
    ItemStatus.CANCELLED: "已删除/取消",
    ItemStatus.NOT_RECEIVED: "未收货",
    ItemStatus.PARTIALLY_RECEIVED: "部分收货",
    ItemStatus.RECEIVED_NOT_INVOICED: "已收货未发票",
    ItemStatus.PARTIALLY_INVOICED: "部分发票",
    ItemStatus.INVOICED_NOT_PAID: "已发票未付款",
    ItemStatus.PARTIALLY_PAID: "部分付款",
    ItemStatus.PAID: "已付款",
}


@dataclass(frozen=True)
class QueryParameters:
    po_number: str
    item_number: str | None = None


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    severity: str = "info"
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentFlow:
    material_documents: tuple[str, ...] = ()
    invoice_documents: tuple[str, ...] = ()
    accounting_documents: tuple[str, ...] = ()
    clearing_documents: tuple[str, ...] = ()


@dataclass(frozen=True)
class ItemStatusResult:
    item_number: str
    material: str
    description: str
    plant: str
    ordered_quantity: Decimal
    received_quantity: Decimal
    invoiced_quantity: Decimal
    unit: str
    invoiced_amount: Decimal
    paid_amount: Decimal
    open_amount: Decimal
    currency: str
    status: ItemStatus
    explanation: str
    findings: tuple[Finding, ...] = ()
    documents: DocumentFlow = field(default_factory=DocumentFlow)

    @property
    def status_label(self) -> str:
        return STATUS_LABELS_ZH[self.status]


@dataclass(frozen=True)
class P2PReport:
    po_number: str
    company_code: str
    vendor: str
    currency: str
    as_of: date
    items: tuple[ItemStatusResult, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready representation without Decimal/date leakage."""

        def convert(value: Any) -> Any:
            if isinstance(value, Decimal):
                return str(value)
            if isinstance(value, date):
                return value.isoformat()
            if isinstance(value, StrEnum):
                return value.value
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [convert(item) for item in value]
            return value

        data = convert(asdict(self))
        for item, result in zip(data["items"], self.items, strict=True):
            item["status_label"] = result.status_label
        return data


@dataclass(frozen=True)
class P2PTables:
    """Relevant source rows for a single PO.

    The adapter performs remote filtering. The analyzer owns the business joins,
    so an RFC/OData implementation and this fixture adapter behave the same way.
    """

    ekko: tuple[SapRow, ...] = ()
    ekpo: tuple[SapRow, ...] = ()
    ekbe: tuple[SapRow, ...] = ()
    mkpf: tuple[SapRow, ...] = ()
    mseg: tuple[SapRow, ...] = ()
    rbkp: tuple[SapRow, ...] = ()
    rseg: tuple[SapRow, ...] = ()
    bkpf: tuple[SapRow, ...] = ()
    bseg: tuple[SapRow, ...] = ()

