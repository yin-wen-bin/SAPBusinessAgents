"""Replaceable SAP data port for the AP Payment Assistant."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, Sequence, runtime_checkable

from .models import PayableItem, VendorBankAccount, VendorProfile


@dataclass(frozen=True, slots=True)
class PayablesFilter:
    vendor_id: str | None = None
    company_code: str | None = None
    invoice_reference: str | None = None
    accounting_document: str | None = None
    fiscal_year: int | None = None
    due_from: date | None = None
    due_to: date | None = None
    include_cleared: bool = False


@runtime_checkable
class SapApDataAdapter(Protocol):
    """Boundary implemented by mock, OData, RFC, CDS, or BTP destinations.

    Expected live mappings:
    - open/cleared vendor items: BSIK/BSAK plus BKPF/BSEG semantics;
    - vendor/company payment data: LFA1/LFB1;
    - payment proposal/run status: REGUH/REGUP;
    - bank accounts: LFBK or an approved vendor-bank API.
    """

    def search_payables(self, filters: PayablesFilter) -> Sequence[PayableItem]: ...

    def get_vendor_profile(self, vendor_id: str) -> VendorProfile | None: ...

    def get_vendor_bank_accounts(self, vendor_id: str) -> Sequence[VendorBankAccount]: ...

    def health(self) -> dict[str, str]: ...

