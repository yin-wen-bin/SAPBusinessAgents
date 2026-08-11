"""Validation and normalization for Thin SAPClaw evidence snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .model import EvidenceRow


class EvidenceValidationError(ValueError):
    """Raised when a snapshot cannot support an auditable conclusion."""


REQUIRED_ENTITY = "A_OperationalAcctgDocItemCube"
REQUIRED_FIELDS = {
    "CompanyCode",
    "FiscalYear",
    "AccountingDocument",
    "AccountingDocumentItem",
    "PurchasingDocument",
    "PurchasingDocumentItem",
    "PostingDate",
    "DebitCreditCode",
    "AmountInCompanyCodeCurrency",
    "CompanyCodeCurrency",
}


def parse_sap_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d+)?\)/", text)
    if match:
        return datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone.utc).date()
    compact = text[:10].replace("-", "")
    if len(compact) == 8 and compact.isdigit():
        try:
            return date(int(compact[:4]), int(compact[4:6]), int(compact[6:]))
        except ValueError:
            return None
    return None


def _text(row: Mapping[str, Any], field: str) -> str:
    return str(row.get(field, "") or "").strip()


@dataclass(frozen=True)
class EvidenceSnapshot:
    rows: tuple[EvidenceRow, ...]
    metadata: Mapping[str, Any]
    complete: bool

    @classmethod
    def load(cls, path: str | Path) -> "EvidenceSnapshot":
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise EvidenceValidationError("Evidence root must be an object")
        completeness = payload.get("completeness")
        if not isinstance(completeness, dict) or completeness.get("complete") is not True:
            raise EvidenceValidationError("Evidence snapshot is not marked complete")
        if completeness.get("has_next") is True or completeness.get("source_complete") is False:
            raise EvidenceValidationError("Evidence pagination is incomplete")
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, dict):
            raise EvidenceValidationError("Evidence metadata must be an object")
        if metadata.get("read_only") is not True:
            raise EvidenceValidationError("Evidence does not prove a read-only Thin runtime")
        entities = payload.get("entities")
        if not isinstance(entities, dict):
            raise EvidenceValidationError("Evidence must contain an entities object")
        rows = entities.get(REQUIRED_ENTITY)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise EvidenceValidationError(f"{REQUIRED_ENTITY} must be a list of objects")
        seen: set[tuple[str, str, str, str]] = set()
        for index, row in enumerate(rows):
            missing = sorted(field for field in REQUIRED_FIELDS if not _text(row, field))
            if missing:
                raise EvidenceValidationError(f"Row {index} is missing required fields: {', '.join(missing)}")
            if parse_sap_date(row.get("PostingDate")) is None:
                raise EvidenceValidationError(f"Row {index} has an invalid PostingDate")
            key = tuple(_text(row, field) for field in (
                "CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem"
            ))
            if key in seen:
                raise EvidenceValidationError(f"Duplicate accounting item key: {'/'.join(key)}")
            seen.add(key)
        return cls(tuple(rows), metadata, True)
