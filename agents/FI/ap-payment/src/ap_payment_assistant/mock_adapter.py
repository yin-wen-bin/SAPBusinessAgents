"""JSON-backed SAP adapter used for local development and tests."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Any, Sequence

from .adapter import PayablesFilter
from .models import PayableItem, VendorBankAccount, VendorProfile


class MockSapApDataAdapter:
    def __init__(self, fixture_path: str | Path | None = None) -> None:
        if fixture_path is None:
            fixture = files("ap_payment_assistant").joinpath("fixtures/mock_sap_ap.json")
            payload = json.loads(fixture.read_text(encoding="utf-8"))
            self._fixture_name = str(fixture)
        else:
            path = Path(fixture_path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self._fixture_name = str(path.resolve())
        self._vendors = {
            record["vendor_id"]: self._profile(record) for record in payload.get("vendors", [])
        }
        self._bank_accounts = tuple(
            self._bank_account(record) for record in payload.get("bank_accounts", [])
        )
        self._items = tuple(self._payable(record) for record in payload.get("payables", []))

    def search_payables(self, filters: PayablesFilter) -> Sequence[PayableItem]:
        result: list[PayableItem] = []
        for item in self._items:
            if filters.vendor_id and item.vendor_id != filters.vendor_id:
                continue
            if filters.company_code and item.company_code != filters.company_code:
                continue
            if filters.invoice_reference and (
                item.invoice_reference.casefold() != filters.invoice_reference.casefold()
            ):
                continue
            if filters.accounting_document and (
                item.accounting_document != filters.accounting_document
            ):
                continue
            if filters.fiscal_year and item.fiscal_year != filters.fiscal_year:
                continue
            if filters.due_from and item.due_date < filters.due_from:
                continue
            if filters.due_to and item.due_date > filters.due_to:
                continue
            if not filters.include_cleared and item.is_cleared:
                continue
            result.append(item)
        return tuple(sorted(result, key=lambda item: (item.due_date, item.accounting_document)))

    def get_vendor_profile(self, vendor_id: str) -> VendorProfile | None:
        return self._vendors.get(vendor_id)

    def get_vendor_bank_accounts(self, vendor_id: str) -> Sequence[VendorBankAccount]:
        return tuple(account for account in self._bank_accounts if account.vendor_id == vendor_id)

    def health(self) -> dict[str, str]:
        return {"status": "ok", "adapter": "mock", "fixture": self._fixture_name}

    @staticmethod
    def _profile(record: dict[str, Any]) -> VendorProfile:
        return VendorProfile(
            vendor_id=record["vendor_id"],
            name=record["name"],
            country=record["country"],
            default_company_code=record.get("default_company_code"),
            source_objects=tuple(record.get("source_objects", ("LFA1", "LFB1"))),
        )

    @staticmethod
    def _bank_account(record: dict[str, Any]) -> VendorBankAccount:
        return VendorBankAccount(
            account_id=record["account_id"],
            vendor_id=record["vendor_id"],
            bank_country=record["bank_country"],
            bank_key=record["bank_key"],
            masked_account=record["masked_account"],
            iban_masked=record.get("iban_masked"),
            is_primary=bool(record.get("is_primary", False)),
            is_verified=bool(record.get("is_verified", False)),
            valid_from=date.fromisoformat(record["valid_from"]),
            changed_on=_optional_date(record.get("changed_on")),
            source_objects=tuple(record.get("source_objects", ("LFBK",))),
        )

    @staticmethod
    def _payable(record: dict[str, Any]) -> PayableItem:
        return PayableItem(
            vendor_id=record["vendor_id"],
            company_code=record["company_code"],
            accounting_document=record["accounting_document"],
            fiscal_year=int(record["fiscal_year"]),
            line_item=str(record.get("line_item", "001")),
            invoice_reference=record["invoice_reference"],
            document_type=record.get("document_type", "KR"),
            invoice_date=date.fromisoformat(record["invoice_date"]),
            posting_date=date.fromisoformat(record["posting_date"]),
            baseline_date=date.fromisoformat(record["baseline_date"]),
            due_date=date.fromisoformat(record["due_date"]),
            amount=Decimal(str(record["amount"])),
            currency=record["currency"],
            payment_block=record.get("payment_block"),
            clearing_document=record.get("clearing_document"),
            clearing_date=_optional_date(record.get("clearing_date")),
            purchase_order=record.get("purchase_order"),
            payment_run_id=record.get("payment_run_id"),
            bank_account_id=record.get("bank_account_id"),
            source_objects=tuple(record.get("source_objects", ("BKPF", "BSEG"))),
        )


def _optional_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None

