"""JSON fixture implementation of the SAP data-source port."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .model import P2PTables, SapRow


TABLE_NAMES = ("EKKO", "EKPO", "EKBE", "MKPF", "MSEG", "RBKP", "RSEG", "BKPF", "BSEG")


def _key(row: SapRow, *fields: str) -> tuple[str, ...]:
    return tuple(str(row.get(field, "")).strip() for field in fields)


class FixtureP2PDataSource:
    """Read SAP-like rows from JSON and emulate source-side filtering."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        with self.path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("Fixture root must be a JSON object")
        self._tables: dict[str, list[dict[str, Any]]] = {}
        for name in TABLE_NAMES:
            rows = payload.get(name, [])
            if not isinstance(rows, list):
                raise ValueError(f"Fixture table {name} must be a list")
            self._tables[name] = [self._normalize_row(name, row) for row in rows]

    @staticmethod
    def _normalize_row(table: str, row: Any) -> dict[str, Any]:
        if not isinstance(row, dict):
            raise ValueError(f"Fixture table {table} contains a non-object row")
        return {str(key).upper(): value for key, value in row.items()}

    @staticmethod
    def _matching(rows: Iterable[SapRow], field: str, value: str) -> list[SapRow]:
        return [row for row in rows if str(row.get(field, "")).strip() == value]

    def load_purchase_order(self, po_number: str) -> P2PTables:
        ekko = self._matching(self._tables["EKKO"], "EBELN", po_number)
        ekpo = self._matching(self._tables["EKPO"], "EBELN", po_number)
        ekbe = self._matching(self._tables["EKBE"], "EBELN", po_number)
        mseg = self._matching(self._tables["MSEG"], "EBELN", po_number)
        rseg = self._matching(self._tables["RSEG"], "EBELN", po_number)

        material_keys = {_key(row, "MBLNR", "MJAHR") for row in mseg}
        mkpf = [row for row in self._tables["MKPF"] if _key(row, "MBLNR", "MJAHR") in material_keys]

        invoice_keys = {_key(row, "BELNR", "GJAHR") for row in rseg}
        rbkp = [row for row in self._tables["RBKP"] if _key(row, "BELNR", "GJAHR") in invoice_keys]

        # BKPF-AWKEY for AWTYP=RMRP starts with invoice document + fiscal year.
        invoice_awkeys = {f"{number}{year}" for number, year in invoice_keys}
        invoice_bkpf = [
            row
            for row in self._tables["BKPF"]
            if str(row.get("AWTYP", "")).upper() == "RMRP"
            and any(str(row.get("AWKEY", "")).startswith(key) for key in invoice_awkeys)
        ]
        fi_keys = {_key(row, "BUKRS", "BELNR", "GJAHR") for row in invoice_bkpf}
        invoice_bseg = [row for row in self._tables["BSEG"] if _key(row, "BUKRS", "BELNR", "GJAHR") in fi_keys]

        # Partial-payment items normally reference the original FI invoice in
        # REBZG/REBZJ without clearing it yet.
        partial_payment_bseg = [
            row
            for row in self._tables["BSEG"]
            if (
                str(row.get("BUKRS", "")).strip(),
                str(row.get("REBZG", "")).strip(),
                str(row.get("REBZJ") or row.get("GJAHR", "")).strip(),
            )
            in fi_keys
            and str(row.get("BELNR", "")).strip()
            != str(row.get("REBZG", "")).strip()
        ]
        partial_keys = {_key(row, "BUKRS", "BELNR", "GJAHR") for row in partial_payment_bseg}
        partial_bkpf = [
            row for row in self._tables["BKPF"] if _key(row, "BUKRS", "BELNR", "GJAHR") in partial_keys
        ]

        # Follow BSEG clearing references so that a clearing document can be
        # distinguished from an actual payment document type (KZ/ZP/PY).
        clearing_keys = {
            (
                str(row.get("BUKRS", "")).strip(),
                str(row.get("AUGBL", "")).strip(),
                str(row.get("AUGGJ") or row.get("GJAHR", "")).strip(),
            )
            for row in invoice_bseg
            if str(row.get("AUGBL", "")).strip()
        }
        clearing_bkpf = [
            row for row in self._tables["BKPF"] if _key(row, "BUKRS", "BELNR", "GJAHR") in clearing_keys
        ]
        clearing_bseg = [
            row for row in self._tables["BSEG"] if _key(row, "BUKRS", "BELNR", "GJAHR") in clearing_keys
        ]

        return P2PTables(
            ekko=tuple(ekko),
            ekpo=tuple(ekpo),
            ekbe=tuple(ekbe),
            mkpf=tuple(mkpf),
            mseg=tuple(mseg),
            rbkp=tuple(rbkp),
            rseg=tuple(rseg),
            bkpf=tuple([*invoice_bkpf, *partial_bkpf, *clearing_bkpf]),
            bseg=tuple([*invoice_bseg, *partial_payment_bseg, *clearing_bseg]),
        )


DEFAULT_FIXTURE = Path(__file__).with_name("fixtures") / "p2p_demo.json"
