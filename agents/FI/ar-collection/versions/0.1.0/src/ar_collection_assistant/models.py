from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


class DisputeStatus(str, Enum):
    NONE = "none"
    OPEN = "open"


class MatchStatus(str, Enum):
    EXACT = "exact"
    LIKELY = "likely"
    UNMATCHED = "unmatched"


class QueryIntent(str, Enum):
    LIST_WEEKLY_COLLECTIONS = "list_weekly_collections"
    GET_AGING = "get_aging"
    LIST_UNMATCHED_RECEIPTS = "list_unmatched_receipts"


@dataclass(frozen=True)
class CustomerAccount:
    customer_id: str
    name: str
    company_code: str
    currency: str
    credit_limit: Decimal
    current_exposure: Decimal
    dunning_block: bool = False
    preferred_language: str = "zh-CN"


@dataclass(frozen=True)
class OpenItem:
    document_id: str
    customer_id: str
    company_code: str
    invoice_id: str
    posting_date: date
    due_date: date
    amount: Decimal
    currency: str
    reference: str = ""
    document_type: str = "DR"
    dispute_status: DisputeStatus = DisputeStatus.NONE


@dataclass(frozen=True)
class PaymentHistory:
    customer_id: str
    average_days_late: Decimal
    on_time_rate: Decimal
    broken_promises_12m: int = 0


@dataclass(frozen=True)
class BankReceipt:
    receipt_id: str
    value_date: date
    amount: Decimal
    currency: str
    payer_name: str
    reference: str
    bank_account: str


@dataclass(frozen=True)
class PaymentMatch:
    receipt_id: str
    status: MatchStatus
    confidence: Decimal
    candidate_document_id: str | None
    candidate_invoice_id: str | None
    candidate_customer_id: str | None
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ARSnapshot:
    accounts: tuple[CustomerAccount, ...]
    open_items: tuple[OpenItem, ...]
    payment_history: tuple[PaymentHistory, ...]
    unmatched_receipts: tuple[BankReceipt, ...]
    source_system: str
    extracted_at: datetime


def to_primitive(value: Any) -> Any:
    """Convert the domain graph to JSON-safe values without losing money precision."""
    if is_dataclass(value):
        return {key: to_primitive(item) for key, item in asdict(value).items()}
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_primitive(item) for item in value]
    return value
