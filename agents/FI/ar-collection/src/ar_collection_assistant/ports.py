from __future__ import annotations

from datetime import date
from typing import Protocol

from .models import ARSnapshot


class ARDataGateway(Protocol):
    """Read-only boundary implemented by fixture and future SAP adapters.

    A production implementation may read BSID/BSAD, BKPF/BSEG, KNA1/KNB1,
    VBRK/VBRP and bank-statement APIs. The assistant never posts clearing or
    sends communications through this interface.
    """

    def load_snapshot(self, as_of: date) -> ARSnapshot:
        ...


class SAPGatewayNotConfigured(RuntimeError):
    pass


class UnconfiguredSAPGateway:
    """Explicit placeholder so production wiring fails safely, not silently."""

    def load_snapshot(self, as_of: date) -> ARSnapshot:
        raise SAPGatewayNotConfigured(
            "No SAP AR adapter is configured. Supply an ARDataGateway backed by "
            "released OData/RFC APIs, or use FixtureARGateway for local execution."
        )
