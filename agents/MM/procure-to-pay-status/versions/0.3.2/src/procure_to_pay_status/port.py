"""Replaceable SAP data-access boundary."""

from __future__ import annotations

from typing import Protocol

from .model import P2PTables


class P2PDataSource(Protocol):
    """Load source rows needed to analyze one purchase order.

    Implementations should filter at the SAP source. They may issue multiple
    remote calls but must preserve original SAP field names in uppercase.
    """

    def load_purchase_order(self, po_number: str) -> P2PTables:
        """Return relevant MM and FI rows for ``po_number``."""

