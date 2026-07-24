from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Sequence

from .models import (
    AnalysisCriteria,
    HistoryEventType,
    PurchaseOrderHistoryEvent,
    PurchaseOrderItem,
    PurchaseOrderKey,
)


class FixtureGrirDataSource:
    """JSON-backed source with the same normalized contract as a real SAP source."""

    def __init__(
        self,
        items: Sequence[PurchaseOrderItem],
        history: Sequence[PurchaseOrderHistoryEvent],
        source_name: str = "fixture",
    ) -> None:
        self._items = tuple(items)
        self._history = tuple(history)
        self._source_name = source_name

    @property
    def source_name(self) -> str:
        return self._source_name

    @classmethod
    def from_file(cls, path: str | Path) -> "FixtureGrirDataSource":
        source_path = Path(path)
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        items = [cls._parse_item(row) for row in payload.get("po_items", [])]
        history = [cls._parse_history(row) for row in payload.get("history", [])]
        return cls(items, history, payload.get("source_name", source_path.name))

    def list_po_items(self, criteria: AnalysisCriteria) -> Sequence[PurchaseOrderItem]:
        return tuple(
            item
            for item in self._items
            if (not criteria.company_code or item.company_code == criteria.company_code)
            and (not criteria.plant or item.plant == criteria.plant)
            and (not criteria.po_number or item.key.po_number == criteria.po_number)
        )

    def load_po_history(
        self,
        keys: Sequence[PurchaseOrderKey],
        as_of_date: date,
    ) -> Mapping[PurchaseOrderKey, Sequence[PurchaseOrderHistoryEvent]]:
        requested = set(keys)
        grouped: dict[PurchaseOrderKey, list[PurchaseOrderHistoryEvent]] = defaultdict(list)
        for event in self._history:
            if event.key in requested and event.posting_date <= as_of_date:
                grouped[event.key].append(event)
        return {
            key: tuple(sorted(grouped.get(key, []), key=lambda row: (row.posting_date, row.event_id)))
            for key in keys
        }

    @staticmethod
    def _parse_item(row: Mapping[str, object]) -> PurchaseOrderItem:
        return PurchaseOrderItem(
            key=PurchaseOrderKey(str(row["po_number"]), str(row["po_item"])),
            company_code=str(row["company_code"]),
            plant=str(row["plant"]),
            vendor=str(row["vendor"]),
            currency=str(row["currency"]),
            ordered_quantity=Decimal(str(row["ordered_quantity"])),
            net_price=Decimal(str(row["net_price"])),
            price_unit=Decimal(str(row.get("price_unit", "1"))),
            material=str(row.get("material", "")),
            description=str(row.get("description", "")),
            purchasing_group=str(row.get("purchasing_group", "")),
        )

    @staticmethod
    def _parse_history(row: Mapping[str, object]) -> PurchaseOrderHistoryEvent:
        return PurchaseOrderHistoryEvent(
            key=PurchaseOrderKey(str(row["po_number"]), str(row["po_item"])),
            event_id=str(row["event_id"]),
            event_type=HistoryEventType(str(row["event_type"])),
            posting_date=date.fromisoformat(str(row["posting_date"])),
            quantity=Decimal(str(row["quantity"])),
            amount=Decimal(str(row["amount"])),
            currency=str(row["currency"]),
            document_number=str(row["document_number"]),
            fiscal_year=str(row.get("fiscal_year", "")),
            movement_type=str(row.get("movement_type", "")),
            reference=str(row.get("reference", "")),
        )
