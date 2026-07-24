"""Domain types shared by the AP assistant layers.

The types avoid SAP transport details. A live OData/RFC adapter can map
BSIK/BSAK/BKPF/BSEG/LFA1/LFB1/REGUH/REGUP records here without leaking a
connection library into intent or risk logic.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any


class Intent(str, Enum):
    UPCOMING_DUE = "upcoming_due"
    OPEN_ITEMS = "open_items"
    INVOICE_STATUS = "invoice_status"
    PAYMENT_RISK = "payment_risk"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class QueryParameters:
    vendor_id: str | None = None
    company_code: str | None = None
    invoice_reference: str | None = None
    accounting_document: str | None = None
    fiscal_year: int | None = None
    due_from: date | None = None
    due_to: date | None = None
    as_of: date = field(default_factory=date.today)


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    text: str
    intent: Intent
    confidence: float
    parameters: QueryParameters
    extraction_notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PayableItem:
    vendor_id: str
    company_code: str
    accounting_document: str
    fiscal_year: int
    line_item: str
    invoice_reference: str
    document_type: str
    invoice_date: date
    posting_date: date
    baseline_date: date
    due_date: date
    amount: Decimal
    currency: str
    payment_block: str | None = None
    clearing_document: str | None = None
    clearing_date: date | None = None
    purchase_order: str | None = None
    payment_run_id: str | None = None
    bank_account_id: str | None = None
    source_objects: tuple[str, ...] = ("BKPF", "BSEG")

    @property
    def is_cleared(self) -> bool:
        return bool(self.clearing_document or self.clearing_date)

    def payment_status(self, as_of: date) -> str:
        if self.is_cleared:
            return "paid"
        if self.payment_block:
            return "blocked"
        if self.due_date < as_of:
            return "overdue"
        if self.payment_run_id:
            return "scheduled"
        return "open"

    @property
    def document_key(self) -> str:
        return f"{self.company_code}/{self.fiscal_year}/{self.accounting_document}/{self.line_item}"


@dataclass(frozen=True, slots=True)
class VendorProfile:
    vendor_id: str
    name: str
    country: str
    default_company_code: str | None = None
    source_objects: tuple[str, ...] = ("LFA1", "LFB1")


@dataclass(frozen=True, slots=True)
class VendorBankAccount:
    account_id: str
    vendor_id: str
    bank_country: str
    bank_key: str
    masked_account: str
    iban_masked: str | None
    is_primary: bool
    is_verified: bool
    valid_from: date
    changed_on: date | None = None
    source_objects: tuple[str, ...] = ("LFBK",)


@dataclass(frozen=True, slots=True)
class RiskFinding:
    rule_id: str
    severity: Severity
    title: str
    explanation: str
    related_documents: tuple[str, ...]
    evidence: dict[str, Any]
    recommended_action: str


@dataclass(frozen=True, slots=True)
class AssistantResponse:
    ok: bool
    query: str
    intent: Intent
    parameters: QueryParameters
    summary: dict[str, Any]
    items: tuple[dict[str, Any], ...] = ()
    risks: tuple[RiskFinding, ...] = ()
    answer: str = ""
    errors: tuple[str, ...] = ()
    trace: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(asdict(self))


def to_primitive(value: Any) -> Any:
    """Convert domain values into deterministic JSON-compatible primitives."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return {key: to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_primitive(item) for item in value]
    return value
