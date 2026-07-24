from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Mapping, Sequence

from .models import (
    AnalysisCriteria,
    HistoryEventType,
    PurchaseOrderHistoryEvent,
    PurchaseOrderItem,
    PurchaseOrderKey,
)
from .ports import SapTableGateway


class SapTableGrirDataSource:
    """Maps EKKO/EKPO/EKBE rows from any table-capable SAP gateway.

    The gateway owns authentication, authorization, paging and RFC/OData details.
    Values returned here are normalized to signed GR-side and IR-side movements.
    """

    _RETURN_MOVEMENTS = {"122", "161"}
    _GR_REVERSAL_MOVEMENTS = {"102", "106", "123", "162"}

    def __init__(self, gateway: SapTableGateway, source_name: str = "sap-tables") -> None:
        self._gateway = gateway
        self._source_name = source_name

    @property
    def source_name(self) -> str:
        return self._source_name

    def list_po_items(self, criteria: AnalysisCriteria) -> Sequence[PurchaseOrderItem]:
        header_filters: dict[str, object] = {}
        if criteria.company_code:
            header_filters["BUKRS"] = criteria.company_code
        if criteria.po_number:
            header_filters["EBELN"] = criteria.po_number

        headers = self._gateway.read_rows(
            "EKKO",
            ("EBELN", "BUKRS", "LIFNR", "WAERS", "EKGRP"),
            header_filters,
        )
        header_by_po = {self._text(row, "EBELN"): row for row in headers}
        if not header_by_po:
            return ()

        item_filters: dict[str, object] = {"EBELN": tuple(header_by_po)}
        if criteria.plant:
            item_filters["WERKS"] = criteria.plant
        item_rows = self._gateway.read_rows(
            "EKPO",
            ("EBELN", "EBELP", "WERKS", "MATNR", "TXZ01", "MENGE", "NETPR", "PEINH", "LOEKZ"),
            item_filters,
        )

        result: list[PurchaseOrderItem] = []
        for row in item_rows:
            if self._text(row, "LOEKZ"):
                continue
            po_number = self._text(row, "EBELN")
            header = header_by_po.get(po_number)
            if not header:
                continue
            result.append(
                PurchaseOrderItem(
                    key=PurchaseOrderKey(po_number, self._text(row, "EBELP")),
                    company_code=self._text(header, "BUKRS"),
                    plant=self._text(row, "WERKS"),
                    vendor=self._text(header, "LIFNR"),
                    currency=self._text(header, "WAERS"),
                    ordered_quantity=self._decimal(row, "MENGE"),
                    net_price=self._decimal(row, "NETPR"),
                    price_unit=self._decimal(row, "PEINH", Decimal("1")),
                    material=self._text(row, "MATNR"),
                    description=self._text(row, "TXZ01"),
                    purchasing_group=self._text(header, "EKGRP"),
                )
            )
        return tuple(result)

    def load_po_history(
        self,
        keys: Sequence[PurchaseOrderKey],
        as_of_date: date,
    ) -> Mapping[PurchaseOrderKey, Sequence[PurchaseOrderHistoryEvent]]:
        if not keys:
            return {}
        rows = self._gateway.read_rows(
            "EKBE",
            (
                "EBELN", "EBELP", "BELNR", "GJAHR", "BUDAT", "BEWTP", "VGABE",
                "BWART", "MENGE", "WRBTR", "WAERS", "SHKZG", "XBLNR",
            ),
            {
                "PO_KEYS": tuple((key.po_number, key.po_item) for key in keys),
                "BUDAT_LE": as_of_date.isoformat(),
            },
        )
        requested = set(keys)
        grouped: dict[PurchaseOrderKey, list[PurchaseOrderHistoryEvent]] = defaultdict(list)
        for index, row in enumerate(rows):
            key = PurchaseOrderKey(self._text(row, "EBELN"), self._text(row, "EBELP"))
            if key not in requested:
                continue
            event = self._map_history_row(key, row, index)
            if event and event.posting_date <= as_of_date:
                grouped[key].append(event)
        return {
            key: tuple(sorted(grouped.get(key, []), key=lambda event: (event.posting_date, event.event_id)))
            for key in keys
        }

    def _map_history_row(
        self,
        key: PurchaseOrderKey,
        row: Mapping[str, object],
        index: int,
    ) -> PurchaseOrderHistoryEvent | None:
        category = self._text(row, "BEWTP").upper()
        movement = self._text(row, "BWART")
        credit = self._text(row, "SHKZG").upper() == "H"
        sign = Decimal("-1") if credit else Decimal("1")

        if category == "E":
            if movement in self._RETURN_MOVEMENTS:
                event_type = HistoryEventType.GOODS_RETURN
                sign = Decimal("-1")
            elif movement in self._GR_REVERSAL_MOVEMENTS:
                event_type = HistoryEventType.GOODS_RECEIPT_REVERSAL
                sign = Decimal("-1")
            else:
                event_type = HistoryEventType.GOODS_RECEIPT
        elif category == "Q":
            event_type = HistoryEventType.CREDIT_MEMO if credit else HistoryEventType.INVOICE_RECEIPT
        else:
            return None

        document = self._text(row, "BELNR")
        fiscal_year = self._text(row, "GJAHR")
        return PurchaseOrderHistoryEvent(
            key=key,
            event_id=f"{document}/{fiscal_year}/{index}",
            event_type=event_type,
            posting_date=self._date(row, "BUDAT"),
            quantity=abs(self._decimal(row, "MENGE")) * sign,
            amount=abs(self._decimal(row, "WRBTR")) * sign,
            currency=self._text(row, "WAERS"),
            document_number=document,
            fiscal_year=fiscal_year,
            movement_type=movement,
            reference=self._text(row, "XBLNR"),
        )

    @staticmethod
    def _text(row: Mapping[str, object], field: str) -> str:
        value = row.get(field, "")
        return "" if value is None else str(value).strip()

    @staticmethod
    def _decimal(
        row: Mapping[str, object],
        field: str,
        default: Decimal = Decimal("0"),
    ) -> Decimal:
        value = row.get(field)
        return default if value in (None, "") else Decimal(str(value))

    @staticmethod
    def _date(row: Mapping[str, object], field: str) -> date:
        value = row.get(field)
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()
        if len(text) == 8 and text.isdigit():
            return datetime.strptime(text, "%Y%m%d").date()
        return date.fromisoformat(text)
