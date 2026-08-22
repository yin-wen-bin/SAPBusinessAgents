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
PROFILE = "SAP000000001"
COUNTER = "001"


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


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return Decimal(0)


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


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {
        "1",
        "true",
        "x",
        "yes",
        "c",
        "complete",
        "completed",
    }


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
        "select_fields": select_fields,
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


def select_qualified_coverage(
    master_rows: list[JsonObject],
    coverage_rows: list[JsonObject],
    *,
    as_of: date,
) -> list[JsonObject]:
    externally_procured = {
        (
            str(row.get("Material") or ""),
            str(row.get("MRPPlant") or ""),
            str(row.get("MRPArea") or ""),
        )
        for row in master_rows
        if str(row.get("MaterialProcurementCategory") or "").upper() == "F"
    }
    qualified = []
    for row in coverage_rows:
        identity = (
            str(row.get("Material") or ""),
            str(row.get("MRPPlant") or ""),
            str(row.get("MRPArea") or ""),
        )
        shortage_end = _date(row.get("MaterialShortageEndDate"))
        if (
            identity in externally_procured
            and _decimal(row.get("MaterialShortageQuantity")) > 0
            and shortage_end is not None
            and shortage_end >= as_of
        ):
            qualified.append(row)
    return sorted(
        qualified,
        key=lambda row: tuple(
            str(row.get(field) or "")
            for field in (
                "Material",
                "MaterialShortageProfile",
                "MaterialShortageProfileCount",
                "MRPArea",
                "MRPPlanningSegmentNumber",
                "MRPPlanningSegmentType",
                "MRPPlant",
            )
        ),
    )


def _requirement_id(row: JsonObject) -> str:
    segment = str(row.get("MRPPlanningSegmentNumber") or "")
    return "|".join(
        (
            str(row.get("MaterialShortageProfile") or ""),
            str(row.get("MaterialShortageProfileCount") or ""),
            str(row.get("MRPArea") or row.get("MRPPlant") or ""),
            segment if segment else "(blank)",
            str(row.get("MRPPlanningSegmentType") or ""),
        )
    )


def _open_schedule_quantity(row: JsonObject) -> Decimal:
    ordered = _decimal(row.get("ScheduleLineOrderQuantity"))
    committed = row.get("ScheduleLineCommittedQuantity")
    return (
        max(ordered - _decimal(committed), Decimal(0))
        if committed not in {None, ""}
        else ordered
    )


def build(case_path: Path, profile_path: Path, output: Path, artifacts: Path) -> JsonObject:
    case = _load(case_path)
    if case.get("schema_version") != "2.0" or case.get("agent_id") != "material-shortage-procurement-response":
        raise ValueError("case must be a material-shortage-procurement-response v2 case")
    values = case.get("input") if isinstance(case.get("input"), dict) else {}
    expected = {
        "material",
        "plant",
        "mrp_area",
        "purchasing_organization",
        "shortage_profile",
        "shortage_counter",
        "as_of",
    }
    if set(values) != expected:
        raise ValueError("case input has unexpected or missing fields")
    if values.get("shortage_profile") != PROFILE or values.get("shortage_counter") != COUNTER:
        raise ValueError("case must use the approved shortage profile and counter")
    as_of = _date(values.get("as_of"))
    if as_of is None:
        raise ValueError("case as_of is invalid")
    profile = direct_sap_read._load_object(profile_path.resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)

    material = _literal(values["material"])
    plant = _literal(values["plant"])
    mrp_area = _literal(values["mrp_area"])
    purchasing_org = _literal(values["purchasing_organization"])
    shortage_profile = _literal(values["shortage_profile"])
    shortage_counter = _literal(values["shortage_counter"])
    sources: list[JsonObject] = []

    master_manifest, master_rows = _run_source(
        profile,
        _request(
            "shortage_mrp_master",
            "API_MRP_MATERIALS_SRV_01",
            "A_MRPMaterial",
            [
                "Material",
                "MRPArea",
                "MRPPlant",
                "MRPController",
                "MRPType",
                "MaterialProcurementCategory",
                "MaterialProcurementCatName",
                "BaseUnit",
                "UnitOfMeasureName",
                "SafetyStockQuantity",
            ],
            f"Material eq {material} and MRPPlant eq {plant} and MRPArea eq {mrp_area}",
            ["Material", "MRPPlant", "MRPArea"],
            max_rows=100,
        ),
        artifacts,
    )
    sources.append(master_manifest)

    coverage_manifest, coverage_rows = _run_source(
        profile,
        _request(
            "shortage_mrp",
            "API_MRP_MATERIALS_SRV_01",
            "MaterialCoverages",
            [
                "Material",
                "MaterialShortageProfile",
                "MaterialShortageProfileCount",
                "MRPArea",
                "MRPPlanningSegmentNumber",
                "MRPPlanningSegmentType",
                "MRPPlant",
                "MRPController",
                "MaterialBaseUnit",
                "MaterialShortageQuantity",
                "MaterialShortageStartDate",
                "MaterialShortageEndDate",
                "MaterialShortageDuration",
                "DaysOfSupplyDuration",
                "VltdUnrestrictedUseStkQty",
                "MaterialLastMRPDateTime",
            ],
            (
                f"Material eq {material} and MRPArea eq {mrp_area} and MRPPlant eq {plant} "
                f"and MaterialShortageProfile eq {shortage_profile} "
                f"and MaterialShortageProfileCount eq {shortage_counter}"
            ),
            [
                "Material",
                "MaterialShortageProfile",
                "MaterialShortageProfileCount",
                "MRPArea",
                "MRPPlanningSegmentNumber",
                "MRPPlanningSegmentType",
                "MRPPlant",
            ],
            max_rows=100,
        ),
        artifacts,
        primary=True,
    )
    sources.append(coverage_manifest)

    pr_manifest, pr_rows = _run_source(
        profile,
        _request(
            "shortage_pr",
            "API_PURCHASEREQ_PROCESS_SRV",
            "A_PurchaseRequisitionItem",
            [
                "PurchaseRequisition",
                "PurchaseRequisitionItem",
                "Material",
                "Plant",
                "DeliveryDate",
                "RequestedQuantity",
                "OrderedQuantity",
                "BaseUnit",
                "ProcessingStatus",
                "PurReqnReleaseStatus",
                "ReleaseIsNotCompleted",
                "IsClosed",
                "IsDeleted",
            ],
            f"Material eq {material} and Plant eq {plant}",
            ["PurchaseRequisition", "PurchaseRequisitionItem"],
        ),
        artifacts,
    )
    sources.append(pr_manifest)

    po_manifest, po_rows = _run_source(
        profile,
        _request(
            "shortage_po_items",
            "API_PURCHASEORDER_PROCESS_SRV",
            "A_PurchaseOrderItem",
            [
                "PurchaseOrder",
                "PurchaseOrderItem",
                "Material",
                "Plant",
                "OrderQuantity",
                "PurchaseOrderQuantityUnit",
            ],
            f"Material eq {material} and Plant eq {plant}",
            ["PurchaseOrder", "PurchaseOrderItem"],
        ),
        artifacts,
    )
    sources.append(po_manifest)

    po_items_by_document: dict[str, set[str]] = {}
    for row in po_rows:
        document = str(row.get("PurchaseOrder") or "")
        item = str(row.get("PurchaseOrderItem") or "")
        if document and item:
            po_items_by_document.setdefault(document, set()).add(item)
    schedule_rows: list[JsonObject] = []
    for index, (document, items) in enumerate(sorted(po_items_by_document.items()), start=1):
        item_filter = " or ".join(
            f"PurchasingDocumentItem eq {_literal(item)}" for item in sorted(items)
        )
        schedule_manifest, rows = _run_source(
            profile,
            _request(
                f"shortage_po_schedules_{index:03d}",
                "API_PURCHASEORDER_PROCESS_SRV",
                "A_PurchaseOrderScheduleLine",
                [
                    "PurchasingDocument",
                    "PurchasingDocumentItem",
                    "ScheduleLine",
                    "ScheduleLineDeliveryDate",
                    "ScheduleLineOrderQuantity",
                    "ScheduleLineCommittedQuantity",
                    "PurchaseOrderQuantityUnit",
                ],
                f"PurchasingDocument eq {_literal(document)} and ({item_filter})",
                ["PurchasingDocument", "PurchasingDocumentItem", "ScheduleLine"],
            ),
            artifacts,
        )
        sources.append(schedule_manifest)
        schedule_rows.extend(rows)

    source_manifest, source_rows = _run_source(
        profile,
        _request(
            "shortage_sources",
            "API_INFORECORD_PROCESS_SRV",
            "A_PurgInfoRecdOrgPlantData",
            [
                "PurchasingInfoRecord",
                "PurchasingInfoRecordCategory",
                "PurchasingOrganization",
                "Plant",
                "Supplier",
                "Material",
                "PurgDocOrderQuantityUnit",
                "MaterialPlannedDeliveryDurn",
                "IsMarkedForDeletion",
                "IsRelevantForAutomSrcg",
            ],
            (
                f"Material eq {material} and PurchasingOrganization eq {purchasing_org} "
                f"and Plant eq {plant}"
            ),
            [
                "PurchasingInfoRecord",
                "PurchasingInfoRecordCategory",
                "PurchasingOrganization",
                "Plant",
            ],
        ),
        artifacts,
    )
    sources.append(source_manifest)

    qualified = select_qualified_coverage(master_rows, coverage_rows, as_of=as_of)
    qualification_status = "qualified" if qualified else "blocked"
    qualification_reasons = [] if qualified else ["qualified_shortage_test_data_missing"]
    records = [
        {
            "material": str(row.get("Material") or values["material"]),
            "plant": str(row.get("MRPPlant") or values["plant"]),
            "requirement_id": _requirement_id(row),
            "requirement_date": (_date(row.get("MaterialShortageStartDate")) or date.min).isoformat()
            if row.get("MaterialShortageStartDate")
            else "",
            "mrp_element_type": "material_coverage",
            "shortage_quantity": str(_decimal(row.get("MaterialShortageQuantity"))),
            "unit": str(row.get("MaterialBaseUnit") or ""),
            "business_status": "attention",
        }
        for row in qualified
    ]
    shortage_quantity = sum(
        (_decimal(row.get("MaterialShortageQuantity")) for row in qualified),
        Decimal(0),
    )
    pending_pr = sum(
        1
        for row in pr_rows
        if not _truthy(row.get("IsDeleted"))
        and not _truthy(row.get("IsClosed"))
        and str(row.get("ProcessingStatus") or "").upper() == "N"
        and str(row.get("PurReqnReleaseStatus") or "").upper()
        not in {"05", "08", "C", "RELEASED", "COMPLETED"}
    )
    expedite_po = sum(
        1
        for row in schedule_rows
        if (_date(row.get("ScheduleLineDeliveryDate")) or date.max) < as_of
        and _open_schedule_quantity(row) > 0
    )
    valid_sources = sum(
        1
        for row in source_rows
        if not _truthy(row.get("IsMarkedForDeletion"))
        and _truthy(row.get("IsRelevantForAutomSrcg"))
    )
    normalized = {
        "records": records,
        "metrics": {
            "shortage_quantity": str(shortage_quantity),
            "pending_pr": pending_pr,
            "expedite_po": expedite_po,
            "valid_source_candidates": valid_sources,
        },
        "limitations": [],
        "source_complete": all(source.get("source_complete") is True for source in sources),
    }
    last_mrp_dates = [
        parsed
        for row in coverage_rows
        for parsed in [_date(row.get("MaterialLastMRPDateTime"))]
        if parsed is not None
    ]
    latest_mrp = max(last_mrp_dates) if last_mrp_dates else None
    observations = []
    if latest_mrp is not None and (as_of - latest_mrp).days > 30:
        observations.append(
            {
                "code": "mrp_snapshot_stale",
                "severity": "warning",
                "blocking": False,
                "last_mrp_date": latest_mrp.isoformat(),
                "age_days": (as_of - latest_mrp).days,
            }
        )
    baseline = {
        "schema_version": "2.0",
        "runtime": "codex_app_direct_sap",
        "used_sap_business_agents": False,
        "http_methods": ["GET"],
        "qualification": {
            "status": qualification_status,
            "reasons": qualification_reasons,
            "evidence_source_ids": ["shortage_mrp_master", "shortage_mrp"],
            "evidence_hash": _hash_json(
                {
                    "master": [
                        {
                            key: row.get(key)
                            for key in (
                                "Material",
                                "MRPPlant",
                                "MRPArea",
                                "MaterialProcurementCategory",
                            )
                        }
                        for row in master_rows
                    ],
                    "coverage": [
                        {
                            key: row.get(key)
                            for key in (
                                "Material",
                                "MRPPlant",
                                "MRPArea",
                                "MaterialShortageProfile",
                                "MaterialShortageProfileCount",
                                "MaterialShortageQuantity",
                                "MaterialShortageStartDate",
                                "MaterialShortageEndDate",
                            )
                        }
                        for row in qualified
                    ],
                }
            ),
        },
        "nonblocking_observations": observations,
        "sources": sources,
        "result_hash": _hash_json(normalized),
        "normalized_result": normalized,
    }
    output.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return baseline


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an independent GET-only direct-SAP baseline for the material-shortage Agent."
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
