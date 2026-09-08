from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from .models import AnalysisReport, GrirException


CSV_FIELDS = (
    "company_code", "plant", "po_number", "po_item", "vendor", "material", "description",
    "currency", "primary_reason", "all_reasons", "severity", "gr_quantity", "ir_quantity",
    "quantity_difference", "gr_amount", "ir_amount", "amount_difference", "oldest_open_date",
    "age_days", "responsibility", "recommendation", "history_documents",
)


def write_report(report: AnalysisReport, output_path: str | Path, output_format: str) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "json":
        path.write_text(
            json.dumps(asdict(report), ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
    elif output_format == "csv":
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(_exception_row(item) for item in report.items)
    else:
        raise ValueError(f"unsupported output format: {output_format}")
    return path


def _exception_row(item: GrirException) -> dict[str, Any]:
    return {
        "company_code": item.po.company_code,
        "plant": item.po.plant,
        "po_number": item.po.key.po_number,
        "po_item": item.po.key.po_item,
        "vendor": item.po.vendor,
        "material": item.po.material,
        "description": item.po.description,
        "currency": item.po.currency,
        "primary_reason": item.primary_reason.value,
        "all_reasons": ";".join(reason.value for reason in item.reasons),
        "severity": item.severity.value,
        "gr_quantity": str(item.gr_quantity),
        "ir_quantity": str(item.ir_quantity),
        "quantity_difference": str(item.quantity_difference),
        "gr_amount": str(item.gr_amount),
        "ir_amount": str(item.ir_amount),
        "amount_difference": str(item.amount_difference),
        "oldest_open_date": item.oldest_open_date.isoformat(),
        "age_days": item.age_days,
        "responsibility": item.responsibility,
        "recommendation": item.recommendation,
        "history_documents": ";".join(item.history_documents),
    }


def _json_default(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"cannot JSON encode {type(value).__name__}")
