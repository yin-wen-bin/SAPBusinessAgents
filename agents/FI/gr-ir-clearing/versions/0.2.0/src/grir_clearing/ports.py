from __future__ import annotations

from datetime import date
from typing import Mapping, Protocol, Sequence

from .models import (
    AnalysisCriteria,
    PurchaseOrderHistoryEvent,
    PurchaseOrderItem,
    PurchaseOrderKey,
)


class GrirDataSource(Protocol):
    """Normalized boundary between the analysis core and an SAP data source."""

    @property
    def source_name(self) -> str: ...

    def list_po_items(self, criteria: AnalysisCriteria) -> Sequence[PurchaseOrderItem]: ...

    def load_po_history(
        self,
        keys: Sequence[PurchaseOrderKey],
        as_of_date: date,
    ) -> Mapping[PurchaseOrderKey, Sequence[PurchaseOrderHistoryEvent]]: ...


class SapTableGateway(Protocol):
    """Small replaceable port for RFC/OData/CDS/table-reader implementations."""

    def read_rows(
        self,
        table: str,
        fields: Sequence[str],
        filters: Mapping[str, object],
    ) -> Sequence[Mapping[str, object]]: ...
