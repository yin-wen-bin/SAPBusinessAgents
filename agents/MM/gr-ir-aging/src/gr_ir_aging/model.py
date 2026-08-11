"""Domain models for GR/IR ageing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any, Mapping


EvidenceRow = Mapping[str, Any]


class ResultStatus(StrEnum):
    COMPLETE = "complete"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class QueryParameters:
    company_code: str
    key_date: date
    purchasing_documents: tuple[str, ...] = ()
    plants: tuple[str, ...] = ()
    gl_accounts: tuple[str, ...] = ()
    ageing_threshold: int = 0
    ageing_buckets: tuple[int, ...] = (30, 60, 90)


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    severity: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class GrIrItem:
    company_code: str
    purchase_order: str
    purchase_order_item: str
    supplier: str
    material: str
    plant: str
    service_item: bool
    gr_quantity: Decimal
    ir_quantity: Decimal
    quantity_unit: str
    gr_value: Decimal
    ir_value: Decimal
    residual_amount: Decimal
    company_currency: str
    transaction_currency: str
    last_activity_date: date
    ageing_days: int
    ageing_bucket: str
    source_documents: tuple[str, ...]
    findings: tuple[Finding, ...] = ()


@dataclass(frozen=True)
class BucketTotal:
    currency: str
    bucket: str
    amount: Decimal
    item_count: int


@dataclass(frozen=True)
class AnalysisResult:
    status: ResultStatus
    query: QueryParameters
    items: tuple[GrIrItem, ...]
    totals: tuple[BucketTotal, ...]
    findings: tuple[Finding, ...]
    source_complete: bool
    read_only: bool = True

    def to_dict(self) -> dict[str, Any]:
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

        return convert(asdict(self))
