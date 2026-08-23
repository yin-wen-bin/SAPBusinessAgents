from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

try:
    from scripts import direct_sap_read
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    import direct_sap_read


JsonObject = dict[str, Any]
SAFE_VALUE = re.compile(r"^[0-9A-Za-z_-]+$")
SAP_V2_DATE = re.compile(r"^/Date\((-?\d+)(?:[+-]\d{4})?\)/$")
SAP_V2_TIME = re.compile(
    r"^PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?$"
)


def _load(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _hash_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _decimal(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _date(value: Any) -> date | None:
    if value in {None, ""}:
        return None
    text = str(value).strip()
    match = SAP_V2_DATE.fullmatch(text)
    if match:
        try:
            return (
                datetime(1970, 1, 1, tzinfo=timezone.utc)
                + timedelta(milliseconds=int(match.group(1)))
            ).date()
        except (OverflowError, ValueError):
            return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _time_key(value: Any) -> tuple[int, int, Decimal]:
    text = str(value or "").strip()
    match = SAP_V2_TIME.fullmatch(text)
    if match:
        return (
            int(match.group("hours") or 0),
            int(match.group("minutes") or 0),
            Decimal(match.group("seconds") or "0"),
        )
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 6:
        return int(digits[:2]), int(digits[2:4]), Decimal(digits[4:6])
    return 0, 0, Decimal(0)


def _literal(value: Any) -> str:
    text = str(value or "").strip()
    if not text or not SAFE_VALUE.fullmatch(text):
        raise ValueError("direct baseline input contains an unsafe SAP identifier")
    return "'" + text.replace("'", "''") + "'"


def _request(
    source_id: str,
    service_name: str,
    entity_set: str,
    select_fields: list[str],
    filter_text: str,
    order_by: list[str],
    *,
    max_rows: int = 30000,
) -> JsonObject:
    return {
        "source_id": source_id,
        "service_name": service_name,
        "service_path": f"/sap/opu/odata/sap/{service_name}",
        "odata_version": "2.0",
        "entity_set": entity_set,
        "select_fields": list(dict.fromkeys([*select_fields, *order_by])),
        "filter": filter_text,
        "order_by": order_by,
        "page_size": min(max_rows, 5000),
        "max_rows": max_rows,
    }


def _run_source(
    profile: JsonObject,
    request: JsonObject,
    artifacts: Path,
    *,
    primary: bool = False,
) -> tuple[JsonObject, list[JsonObject]]:
    output = artifacts / str(request["source_id"])
    raw_manifest = direct_sap_read.run(profile, request, output)
    manifest = {
        key: raw_manifest[key]
        for key in (
            "source_id",
            "service_name",
            "odata_version",
            "entity_set",
            "schema_hash",
            "query_hash",
            "row_count",
            "page_count",
            "stable_order_by",
            "paging_complete",
            "source_complete",
        )
    }
    manifest["primary"] = primary
    rows = json.loads((output / "rows.json").read_text(encoding="utf-8"))
    return manifest, [row for row in rows if isinstance(row, dict)]


def _stock_by_batch(rows: list[JsonObject]) -> tuple[dict[str, Decimal], str]:
    quantities: dict[str, Decimal] = {}
    units: set[str] = set()
    for row in rows:
        if str(row.get("InventoryStockType") or "") != "01":
            continue
        if str(row.get("InventorySpecialStockType") or ""):
            continue
        quantity = _decimal(row.get("MatlWrhsStkQtyInMatlBaseUnit"))
        unit = str(row.get("MaterialBaseUnit") or "").strip()
        if quantity is None or quantity < 0 or not unit:
            raise ValueError("current stock contains an invalid quantity or base unit")
        batch = str(row.get("Batch") or "")
        quantities[batch] = quantities.get(batch, Decimal(0)) + quantity
        units.add(unit)
    if len(units) > 1:
        raise ValueError("current stock contains multiple unverified base units")
    return quantities, next(iter(units), "")


def _fifo(
    item_rows: list[JsonObject],
    header_rows: list[JsonObject],
    *,
    snapshot: date,
    expected_stock: dict[str, Decimal],
    expected_unit: str,
) -> list[JsonObject]:
    headers = {
        (str(row.get("MaterialDocumentYear") or ""), str(row.get("MaterialDocument") or "")): row
        for row in header_rows
    }
    events: list[JsonObject] = []
    stable_keys: set[tuple[str, str, str]] = set()
    for row in item_rows:
        stable_key = (
            str(row.get("MaterialDocumentYear") or ""),
            str(row.get("MaterialDocument") or ""),
            str(row.get("MaterialDocumentItem") or ""),
        )
        if not all(stable_key) or stable_key in stable_keys:
            raise ValueError("movement history contains a missing or duplicate stable key")
        stable_keys.add(stable_key)
        header = headers.get(stable_key[:2])
        if header is None:
            raise ValueError("movement history has no matching material-document header")
        posting = _date(header.get("PostingDate"))
        created = _date(header.get("CreationDate")) or posting
        quantity = _decimal(row.get("QuantityInBaseUnit"))
        direction = str(row.get("DebitCreditCode") or "").upper()
        unit = str(row.get("MaterialBaseUnit") or "").strip()
        if (
            posting is None
            or posting > snapshot
            or created is None
            or quantity is None
            or quantity < 0
            or direction not in {"S", "H"}
            or not unit
            or unit != expected_unit
        ):
            raise ValueError("movement history contains an invalid date, quantity, direction, or unit")
        events.append(
            {
                "stable_key": stable_key,
                "posting_date": posting,
                "creation_date": created,
                "creation_time": _time_key(header.get("CreationTime")),
                "direction": direction,
                "quantity": quantity,
                "unit": unit,
                "batch": str(row.get("Batch") or ""),
            }
        )
    events.sort(
        key=lambda event: (
            event["posting_date"],
            event["creation_date"],
            event["creation_time"],
            0 if event["direction"] == "S" else 1,
            event["stable_key"],
        )
    )
    layers_by_batch: dict[str, list[JsonObject]] = {}
    for event in events:
        layers = layers_by_batch.setdefault(str(event["batch"]), [])
        quantity = Decimal(event["quantity"])
        if event["direction"] == "S":
            layers.append(
                {
                    "receipt_date": event["posting_date"],
                    "quantity": quantity,
                    "unit": event["unit"],
                }
            )
            continue
        remaining = quantity
        while remaining > 0 and layers:
            layer = layers[0]
            consumed = min(Decimal(layer["quantity"]), remaining)
            layer["quantity"] = Decimal(layer["quantity"]) - consumed
            remaining -= consumed
            if Decimal(layer["quantity"]) == 0:
                layers.pop(0)
        if remaining > 0:
            raise ValueError("movement history creates a negative FIFO inventory layer")
    actual = {
        batch: sum((Decimal(layer["quantity"]) for layer in layers), Decimal(0))
        for batch, layers in layers_by_batch.items()
        if sum((Decimal(layer["quantity"]) for layer in layers), Decimal(0)) != 0
    }
    expected = {batch: value for batch, value in expected_stock.items() if value != 0}
    if actual != expected:
        raise ValueError("FIFO movement layers do not reconcile to current stock")
    return [
        {
            "batch": batch or None,
            "receipt_date": layer["receipt_date"].isoformat(),
            "age_days": (snapshot - layer["receipt_date"]).days,
            "remaining_quantity": str(layer["quantity"]),
            "unit": str(layer["unit"]),
        }
        for batch, layers in sorted(layers_by_batch.items())
        for layer in layers
        if Decimal(layer["quantity"]) > 0
    ]


def build(case_path: Path, profile_path: Path, output: Path, artifacts: Path) -> JsonObject:
    case = _load(case_path)
    if case.get("schema_version") != "2.0" or case.get("agent_id") != "inventory-health-balancing":
        raise ValueError("case must be an inventory-health-balancing v2 case")
    values = case.get("input") if isinstance(case.get("input"), dict) else {}
    expected = {
        "material",
        "plant",
        "storage_location",
        "slow_moving_days",
        "obsolete_days",
        "expiry_days",
    }
    if set(values) != expected:
        raise ValueError("case input has unexpected or missing fields")
    slow_days = int(values["slow_moving_days"])
    obsolete_days = int(values["obsolete_days"])
    if slow_days < 1 or obsolete_days <= slow_days:
        raise ValueError("FIFO age thresholds are invalid")
    snapshot = date.today()
    profile = direct_sap_read._load_object(profile_path.resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)
    material = _literal(values["material"])
    plant = _literal(values["plant"])
    storage = _literal(values["storage_location"])
    sources: list[JsonObject] = []

    stock_filter = (
        f"Material eq {material} and Plant eq {plant} and StorageLocation eq {storage} "
        "and InventoryStockType eq '01' and InventorySpecialStockType eq ''"
    )
    stock_fields = [
        "Material",
        "Plant",
        "StorageLocation",
        "Batch",
        "Supplier",
        "Customer",
        "WBSElementInternalID",
        "SDDocument",
        "SDDocumentItem",
        "InventorySpecialStockType",
        "InventoryStockType",
        "MatlWrhsStkQtyInMatlBaseUnit",
        "MaterialBaseUnit",
    ]
    stock_keys = [
        "Material",
        "Plant",
        "StorageLocation",
        "Batch",
        "Supplier",
        "Customer",
        "WBSElementInternalID",
        "SDDocument",
        "SDDocumentItem",
        "InventorySpecialStockType",
        "InventoryStockType",
    ]
    initial_manifest, initial_rows = _run_source(
        profile,
        _request(
            "inventory_stock_initial",
            "API_MATERIAL_STOCK_SRV",
            "A_MatlStkInAcctMod",
            stock_fields,
            stock_filter,
            stock_keys,
            max_rows=1000,
        ),
        artifacts,
        primary=True,
    )
    sources.append(initial_manifest)
    initial_stock, unit = _stock_by_batch(initial_rows)

    movement_manifest, movement_rows = _run_source(
        profile,
        _request(
            "inventory_movement_items",
            "API_MATERIAL_DOCUMENT_SRV",
            "A_MaterialDocumentItem",
            [
                "MaterialDocumentYear",
                "MaterialDocument",
                "MaterialDocumentItem",
                "Material",
                "Plant",
                "StorageLocation",
                "Batch",
                "GoodsMovementType",
                "DebitCreditCode",
                "QuantityInBaseUnit",
                "MaterialBaseUnit",
                "InventoryStockType",
                "InventorySpecialStockType",
                "ReversedMaterialDocumentYear",
                "ReversedMaterialDocument",
            ],
            (
                f"Material eq {material} and Plant eq {plant} and StorageLocation eq {storage} "
                "and InventoryStockType eq '01' and InventorySpecialStockType eq ''"
            ),
            ["MaterialDocumentYear", "MaterialDocument", "MaterialDocumentItem"],
        ),
        artifacts,
    )
    sources.append(movement_manifest)
    document_pairs = sorted(
        {
            (
                str(row.get("MaterialDocumentYear") or ""),
                str(row.get("MaterialDocument") or ""),
            )
            for row in movement_rows
            if row.get("MaterialDocumentYear") and row.get("MaterialDocument")
        }
    )
    if not document_pairs and any(value > 0 for value in initial_stock.values()):
        raise ValueError("positive current stock has no complete movement history")
    header_rows: list[JsonObject] = []
    for index in range(0, len(document_pairs), 20):
        pairs = document_pairs[index : index + 20]
        filter_text = " or ".join(
            "(MaterialDocumentYear eq "
            + _literal(year)
            + " and MaterialDocument eq "
            + _literal(document)
            + ")"
            for year, document in pairs
        )
        header_manifest, rows = _run_source(
            profile,
            _request(
                f"inventory_movement_headers_{index // 20 + 1:03d}",
                "API_MATERIAL_DOCUMENT_SRV",
                "A_MaterialDocumentHeader",
                [
                    "MaterialDocumentYear",
                    "MaterialDocument",
                    "PostingDate",
                    "CreationDate",
                    "CreationTime",
                ],
                filter_text,
                ["MaterialDocumentYear", "MaterialDocument"],
            ),
            artifacts,
        )
        sources.append(header_manifest)
        header_rows.extend(rows)

    confirmation_manifest, confirmation_rows = _run_source(
        profile,
        _request(
            "inventory_stock_confirmation",
            "API_MATERIAL_STOCK_SRV",
            "A_MatlStkInAcctMod",
            stock_fields,
            stock_filter,
            stock_keys,
            max_rows=1000,
        ),
        artifacts,
    )
    sources.append(confirmation_manifest)
    confirmation_stock, confirmation_unit = _stock_by_batch(confirmation_rows)
    if initial_stock != confirmation_stock or unit != confirmation_unit:
        raise ValueError("current stock changed while the direct baseline was being collected")
    layers = _fifo(
        movement_rows,
        header_rows,
        snapshot=snapshot,
        expected_stock=confirmation_stock,
        expected_unit=confirmation_unit,
    )

    bucket_quantities = {
        "below_slow_moving": Decimal(0),
        "slow_moving_only": Decimal(0),
        "obsolete": Decimal(0),
    }
    for layer in layers:
        age = int(layer["age_days"])
        bucket = (
            "obsolete"
            if age >= obsolete_days
            else "slow_moving_only"
            if age >= slow_days
            else "below_slow_moving"
        )
        layer["bucket_id"] = bucket
        bucket_quantities[bucket] += Decimal(str(layer["remaining_quantity"]))
    unrestricted = sum(confirmation_stock.values(), Decimal(0))
    classified = sum(bucket_quantities.values(), Decimal(0))
    if classified != unrestricted:
        raise ValueError("classified FIFO buckets do not reconcile to current stock")
    movement_dates = [
        parsed
        for row in header_rows
        for parsed in [_date(row.get("PostingDate"))]
        if parsed is not None and parsed <= snapshot
    ]
    last_movement = max(movement_dates) if movement_dates else None
    oldest_layer = min((_date(row["receipt_date"]) for row in layers), default=None)
    normalized = {
        "records": [
            {
                "snapshot_date": snapshot.isoformat(),
                "material": str(values["material"]),
                "plant": str(values["plant"]),
                "storage_location": str(values["storage_location"]),
                "current_unrestricted_stock": str(unrestricted),
                "unit": confirmation_unit,
                "aging_method": "fifo_movement_layers",
                "aging_complete": True,
                "last_movement_activity_date": last_movement.isoformat() if last_movement else None,
                "oldest_remaining_layer_date": oldest_layer.isoformat() if oldest_layer else None,
                "slow_moving_status": "candidate" if sum(
                    (value for key, value in bucket_quantities.items() if key != "below_slow_moving"),
                    Decimal(0),
                ) > 0 else "not_candidate",
                "obsolete_status": "candidate" if bucket_quantities["obsolete"] > 0 else "not_candidate",
                "expiry_status": "not_candidate",
                "business_status": "attention" if any(
                    value > 0 for key, value in bucket_quantities.items() if key != "below_slow_moving"
                ) else "normal",
                "source_complete": True,
                "evidence_complete": True,
            }
        ],
        "metrics": {
            "current_unrestricted_stock": str(unrestricted),
            "days_since_last_movement_activity": (
                (snapshot - last_movement).days if last_movement else None
            ),
            "oldest_remaining_layer_age_days": (
                (snapshot - oldest_layer).days if oldest_layer else None
            ),
            "classified_stock_quantity": str(classified),
            "unclassified_stock_quantity": "0",
            "below_threshold_stock_quantity": str(bucket_quantities["below_slow_moving"]),
            "slow_moving_only_stock_quantity": str(bucket_quantities["slow_moving_only"]),
            "obsolete_stock_quantity": str(bucket_quantities["obsolete"]),
            "expiry_candidate_count": 0,
        },
        "aging_buckets": [
            {"bucket_id": key, "quantity": str(value), "unit": confirmation_unit}
            for key, value in bucket_quantities.items()
        ],
        "remaining_fifo_layers": layers,
        "limitations": [],
        "source_complete": all(source.get("source_complete") is True for source in sources),
    }
    qualification_reasons = []
    if not normalized["source_complete"]:
        qualification_reasons.append("direct_source_incomplete")
    if unrestricted <= 0:
        qualification_reasons.append("positive_inventory_test_data_missing")
    baseline = {
        "schema_version": "2.0",
        "runtime": "codex_app_direct_sap",
        "used_sap_business_agents": False,
        "http_methods": ["GET"],
        "qualification": {
            "status": "qualified" if not qualification_reasons else "blocked",
            "reasons": qualification_reasons,
            "evidence_source_ids": [source["source_id"] for source in sources],
            "evidence_hash": _hash_json(
                {
                    "stock": confirmation_stock,
                    "movement_keys": [
                        [
                            row.get("MaterialDocumentYear"),
                            row.get("MaterialDocument"),
                            row.get("MaterialDocumentItem"),
                        ]
                        for row in movement_rows
                    ],
                    "layers": layers,
                }
            ),
        },
        "sources": sources,
        "result_hash": _hash_json(normalized),
        "normalized_result": normalized,
    }
    output.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return baseline


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an independent GET-only FIFO inventory-aging SAP baseline."
    )
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path.home() / ".codex" / "secure" / "sap-direct-readonly.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    baseline = build(
        args.case.resolve(),
        args.profile.resolve(),
        args.output.resolve(),
        args.artifacts.resolve(),
    )
    print(
        json.dumps(
            {
                "qualification": baseline["qualification"]["status"],
                "source_count": len(baseline["sources"]),
                "source_complete": baseline["normalized_result"]["source_complete"],
                "record_count": len(baseline["normalized_result"]["records"]),
                "result_hash": baseline["result_hash"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if baseline["qualification"]["status"] == "qualified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
