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


def _strict_decimal(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


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
    manifest_path = output / "manifest.json"
    rows_path = output / "rows.json"
    if manifest_path.is_file() and rows_path.is_file():
        cached = _load(manifest_path)
        expected_query_hash = _hash_json(
            {key: request[key] for key in request if key != "source_id"}
        )
        if (
            cached.get("query_hash") == expected_query_hash
            and cached.get("source_complete") is True
            and cached.get("paging_complete") is True
            and cached.get("http_method") == "GET"
        ):
            rows = json.loads(rows_path.read_text(encoding="utf-8"))
            manifest = {
                key: cached[key]
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
            return manifest, [row for row in rows if isinstance(row, dict)]
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


def _pr_action(row: JsonObject) -> str:
    release_status = str(row.get("PurReqnReleaseStatus") or "").upper()
    release_not_complete = _truthy(row.get("ReleaseIsNotCompleted"))
    source_assigned = _truthy(row.get("SourceOfSupplyIsAssigned")) or bool(
        str(row.get("FixedSupplier") or "").strip()
    )
    if release_status == "05" and release_not_complete:
        return "manual_review"
    if release_status == "01":
        return "complete_version"
    if release_status == "02":
        return "process_active" if source_assigned else "assign_source"
    if release_status in {"03", "04"}:
        return "complete_release"
    if release_status == "05":
        return "ready_to_convert" if source_assigned else "assign_source"
    if release_status == "08":
        return "handle_rejection"
    return "manual_review"


def _chunks(values: list[str], size: int = 20) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


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
                "PurchaseRequisitionItemText",
                "Material",
                "Plant",
                "DeliveryDate",
                "RequestedQuantity",
                "OrderedQuantity",
                "BaseUnit",
                "ProcessingStatus",
                "PurReqnReleaseStatus",
                "ReleaseIsNotCompleted",
                "SourceOfSupplyIsAssigned",
                "FixedSupplier",
                "PurchasingOrganization",
                "PurchasingGroup",
                "RequisitionerName",
                "PurReqCreationDate",
                "CreatedByUser",
                "PurchaseRequisitionPrice",
                "PurReqnPriceQuantity",
                "PurReqnItemCurrency",
                "IsClosed",
                "IsDeleted",
            ],
            (
                f"Material eq {material} and Plant eq {plant} "
                "and ProcessingStatus eq 'N'"
            ),
            ["PurchaseRequisition", "PurchaseRequisitionItem"],
            max_rows=5000,
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
                "PurchaseOrderItemText",
                "OrderQuantity",
                "PurchaseOrderQuantityUnit",
                "PurchaseRequisition",
                "PurchaseRequisitionItem",
                "SupplierMaterialNumber",
                "PurchasingDocumentDeletionCode",
                "IsCompletelyDelivered",
                "GoodsReceiptIsExpected",
            ],
            f"Material eq {material} and Plant eq {plant}",
            ["PurchaseOrder", "PurchaseOrderItem"],
            max_rows=1000,
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
    po_header_rows: list[JsonObject] = []
    for index, documents in enumerate(
        _chunks(sorted(po_items_by_document)), start=1
    ):
        document_filter = " or ".join(
            f"PurchaseOrder eq {_literal(document)}" for document in documents
        )
        header_manifest, rows = _run_source(
            profile,
            _request(
                f"shortage_po_headers_{index:03d}",
                "API_PURCHASEORDER_PROCESS_SRV",
                "A_PurchaseOrder",
                [
                    "PurchaseOrder",
                    "Supplier",
                    "PurchasingOrganization",
                    "PurchasingGroup",
                    "SupplierRespSalesPersonName",
                    "SupplierPhoneNumber",
                ],
                f"({document_filter})",
                ["PurchaseOrder"],
                max_rows=100,
            ),
            artifacts,
        )
        sources.append(header_manifest)
        po_header_rows.extend(rows)
    schedule_rows: list[JsonObject] = []
    for index, documents in enumerate(
        _chunks(sorted(po_items_by_document)), start=1
    ):
        document_filter = " or ".join(
            f"PurchasingDocument eq {_literal(document)}" for document in documents
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
                f"({document_filter})",
                ["PurchasingDocument", "PurchasingDocumentItem", "ScheduleLine"],
                max_rows=1000,
            ),
            artifacts,
        )
        sources.append(schedule_manifest)
        schedule_rows.extend(
            row
            for row in rows
            if str(row.get("PurchasingDocumentItem") or "")
            in po_items_by_document.get(str(row.get("PurchasingDocument") or ""), set())
        )

    receipt_manifest, all_receipt_rows = _run_source(
        profile,
        _request(
            "shortage_po_receipts",
            "API_MATERIAL_DOCUMENT_SRV",
            "A_MaterialDocumentItem",
            [
                "MaterialDocumentYear",
                "MaterialDocument",
                "MaterialDocumentItem",
                "Material",
                "Plant",
                "PurchaseOrder",
                "PurchaseOrderItem",
                "GoodsMovementType",
                "DebitCreditCode",
                "QuantityInEntryUnit",
                "EntryUnit",
                "ReversedMaterialDocumentYear",
                "ReversedMaterialDocument",
                "ReversedMaterialDocumentItem",
            ],
            f"Material eq {material} and Plant eq {plant}",
            ["MaterialDocumentYear", "MaterialDocument", "MaterialDocumentItem"],
            max_rows=5000,
        ),
        artifacts,
    )
    sources.append(receipt_manifest)
    receipt_rows = [
        row
        for row in all_receipt_rows
        if str(row.get("PurchaseOrder") or "") in po_items_by_document
    ]
    receipt_documents_by_year: dict[str, set[str]] = {}
    for row in receipt_rows:
        year = str(row.get("MaterialDocumentYear") or "")
        document = str(row.get("MaterialDocument") or "")
        if year and document:
            receipt_documents_by_year.setdefault(year, set()).add(document)
    receipt_header_rows: list[JsonObject] = []
    header_query_index = 0
    for year, year_documents in sorted(receipt_documents_by_year.items()):
        for documents in _chunks(sorted(year_documents)):
            header_query_index += 1
            document_filter = " or ".join(
                f"MaterialDocument eq {_literal(document)}" for document in documents
            )
            receipt_header_manifest, rows = _run_source(
                profile,
                _request(
                    f"shortage_receipt_headers_{header_query_index:03d}",
                    "API_MATERIAL_DOCUMENT_SRV",
                    "A_MaterialDocumentHeader",
                    ["MaterialDocumentYear", "MaterialDocument", "PostingDate"],
                    f"MaterialDocumentYear eq {_literal(year)} and ({document_filter})",
                    ["MaterialDocumentYear", "MaterialDocument"],
                    max_rows=100,
                ),
                artifacts,
            )
            sources.append(receipt_header_manifest)
            receipt_header_rows.extend(rows)

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
                "PurchasingGroup",
                "MinimumPurchaseOrderQuantity",
                "StandardPurchaseOrderQuantity",
                "MaximumOrderQuantity",
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
            max_rows=100,
        ),
        artifacts,
    )
    sources.append(source_manifest)

    qualified = select_qualified_coverage(master_rows, coverage_rows, as_of=as_of)
    action_regression = case.get("test_purpose") == "procurement_action_display"
    action_coverage = [
        row
        for row in coverage_rows
        if action_regression
        and str(row.get("Material") or "") == str(values["material"])
        and str(row.get("MRPPlant") or "") == str(values["plant"])
        and str(row.get("MRPArea") or "") == str(values["mrp_area"])
        and str(row.get("MaterialShortageProfile") or "") == str(values["shortage_profile"])
        and str(row.get("MaterialShortageProfileCount") or "") == str(values["shortage_counter"])
        and any(
            str(master.get("MaterialProcurementCategory") or "").upper() == "F"
            for master in master_rows
        )
    ]
    coverage_for_records = qualified or action_coverage
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
        for row in coverage_for_records
    ]
    shortage_quantity = sum(
        (_decimal(row.get("MaterialShortageQuantity")) for row in coverage_for_records),
        Decimal(0),
    )
    limitations: list[str] = []
    pr_actions: list[JsonObject] = []
    for row in pr_rows:
        if (
            _truthy(row.get("IsDeleted"))
            or _truthy(row.get("IsClosed"))
            or str(row.get("ProcessingStatus") or "").upper() != "N"
        ):
            continue
        requested = _strict_decimal(row.get("RequestedQuantity"))
        ordered = _strict_decimal(row.get("OrderedQuantity"))
        if requested is None or ordered is None:
            limitations.append("pr_quantity_evidence")
            continue
        remaining = max(requested - ordered, Decimal(0))
        if remaining <= 0:
            continue
        delivery_date = _date(row.get("DeliveryDate"))
        pr_actions.append(
            {
                "action": _pr_action(row),
                "purchase_requisition": str(row.get("PurchaseRequisition") or ""),
                "purchase_requisition_item": str(row.get("PurchaseRequisitionItem") or ""),
                "release_status": str(row.get("PurReqnReleaseStatus") or ""),
                "requested_quantity": str(requested),
                "ordered_quantity": str(ordered),
                "remaining_quantity": str(remaining),
                "unit": str(row.get("BaseUnit") or ""),
                "delivery_date": delivery_date.isoformat() if delivery_date else "",
                "supplier": str(row.get("FixedSupplier") or ""),
            }
        )
    pr_actions.sort(
        key=lambda row: (
            str(row.get("action") or ""),
            str(row.get("delivery_date") or ""),
            str(row.get("purchase_requisition") or ""),
            str(row.get("purchase_requisition_item") or ""),
        )
    )
    pr_counts = {
        "release": sum(row["action"] == "complete_release" for row in pr_actions),
        "convert": sum(row["action"] == "ready_to_convert" for row in pr_actions),
        "source_or_processing": sum(
            row["action"] in {"complete_version", "assign_source", "process_active"}
            for row in pr_actions
        ),
    }

    po_item_map = {
        (str(row.get("PurchaseOrder") or ""), str(row.get("PurchaseOrderItem") or "")): row
        for row in po_rows
        if row.get("PurchaseOrder") and row.get("PurchaseOrderItem")
    }
    po_header_map = {
        str(row.get("PurchaseOrder") or ""): row
        for row in po_header_rows
        if row.get("PurchaseOrder")
    }
    receipt_header_map = {
        (str(row.get("MaterialDocumentYear") or ""), str(row.get("MaterialDocument") or "")): row
        for row in receipt_header_rows
        if row.get("MaterialDocumentYear") and row.get("MaterialDocument")
    }
    receipt_totals: dict[tuple[str, str], Decimal] = {}
    receipt_units: dict[tuple[str, str], set[str]] = {}
    for row in receipt_rows:
        receipt_key = (
            str(row.get("MaterialDocumentYear") or ""),
            str(row.get("MaterialDocument") or ""),
        )
        header = receipt_header_map.get(receipt_key)
        posting_date = _date(header.get("PostingDate")) if header else None
        if posting_date is None:
            limitations.append("po_receipt_posting_date_evidence")
            continue
        if posting_date > as_of:
            continue
        po_key = (
            str(row.get("PurchaseOrder") or ""),
            str(row.get("PurchaseOrderItem") or ""),
        )
        quantity = _strict_decimal(row.get("QuantityInEntryUnit"))
        debit_credit = str(row.get("DebitCreditCode") or "").upper()
        unit = str(row.get("EntryUnit") or "").upper()
        if not all(po_key) or quantity is None or quantity < 0 or debit_credit not in {"S", "H"}:
            limitations.append("po_receipt_quantity_evidence")
            continue
        receipt_totals[po_key] = receipt_totals.get(po_key, Decimal(0)) + (
            quantity if debit_credit == "S" else -quantity
        )
        if unit:
            receipt_units.setdefault(po_key, set()).add(unit)

    schedules_by_item: dict[tuple[str, str], list[JsonObject]] = {}
    for row in schedule_rows:
        key = (
            str(row.get("PurchasingDocument") or ""),
            str(row.get("PurchasingDocumentItem") or ""),
        )
        if all(key):
            schedules_by_item.setdefault(key, []).append(row)
        else:
            limitations.append("po_schedule_business_key_evidence")
    po_actions: list[JsonObject] = []
    for po_key, rows in schedules_by_item.items():
        item = po_item_map.get(po_key)
        if item is None:
            limitations.append("po_item_evidence")
            continue
        if str(item.get("PurchasingDocumentDeletionCode") or "").strip():
            continue
        unit = str(item.get("PurchaseOrderQuantityUnit") or "").upper()
        if not unit:
            limitations.append("po_order_unit_evidence")
            continue
        if any(receipt_unit != unit for receipt_unit in receipt_units.get(po_key, set())):
            limitations.append("po_receipt_unit_conflict")
            continue
        receipt_pool = receipt_totals.get(po_key, Decimal(0))
        if receipt_pool < 0:
            limitations.append("po_negative_net_receipt")
            continue
        header = po_header_map.get(po_key[0])
        if header is None:
            limitations.append("po_header_evidence")
            header = {}
        for row in sorted(
            rows,
            key=lambda item: (
                _date(item.get("ScheduleLineDeliveryDate")) or date.max,
                str(item.get("ScheduleLine") or ""),
            ),
        ):
            delivery_date = _date(row.get("ScheduleLineDeliveryDate"))
            scheduled = _strict_decimal(row.get("ScheduleLineOrderQuantity"))
            schedule_unit = str(row.get("PurchaseOrderQuantityUnit") or unit).upper()
            if delivery_date is None or scheduled is None or scheduled < 0:
                limitations.append("po_schedule_quantity_or_date_evidence")
                continue
            if schedule_unit != unit:
                limitations.append("po_schedule_unit_conflict")
                continue
            received = max(min(receipt_pool, scheduled), Decimal(0))
            receipt_pool -= received
            open_quantity = max(scheduled - received, Decimal(0))
            if delivery_date >= as_of or open_quantity <= 0:
                continue
            committed = _strict_decimal(row.get("ScheduleLineCommittedQuantity"))
            po_actions.append(
                {
                    "purchase_order": po_key[0],
                    "purchase_order_item": po_key[1],
                    "schedule_line": str(row.get("ScheduleLine") or ""),
                    "supplier": str(header.get("Supplier") or ""),
                    "delivery_date": delivery_date.isoformat(),
                    "scheduled_quantity": str(scheduled),
                    "received_quantity": str(received),
                    "open_quantity": str(open_quantity),
                    "committed_quantity": str(committed) if committed is not None else "",
                    "unit": unit,
                }
            )
    po_actions.sort(
        key=lambda row: (
            str(row.get("delivery_date") or ""),
            str(row.get("purchase_order") or ""),
            str(row.get("purchase_order_item") or ""),
            str(row.get("schedule_line") or ""),
        )
    )
    po_business_complete = not any(
        limitation.startswith("po_") for limitation in limitations
    )
    expedite_po: int | None = len(po_actions) if po_business_complete else None
    valid_sources = sum(
        1
        for row in source_rows
        if not _truthy(row.get("IsMarkedForDeletion"))
        and _truthy(row.get("IsRelevantForAutomSrcg"))
    )
    all_sources_complete = all(
        source.get("source_complete") is True for source in sources
    )
    qualification_reasons: list[str] = []
    if not coverage_for_records:
        qualification_reasons.append("qualified_shortage_test_data_missing")
    if not all_sources_complete:
        qualification_reasons.append("direct_source_incomplete")
    if limitations:
        qualification_reasons.append("business_evidence_incomplete")
    qualification_status = "qualified" if not qualification_reasons else "blocked"
    normalized = {
        "records": records,
        "metrics": {
            "shortage_quantity": str(shortage_quantity),
            "pr_action_total": len(pr_actions),
            "pr_awaiting_release": pr_counts["release"],
            "pr_ready_to_convert": pr_counts["convert"],
            "pr_source_or_processing_required": pr_counts["source_or_processing"],
            "po_schedule_lines_to_expedite": expedite_po,
            "pending_pr": len(pr_actions),
            "expedite_po": expedite_po,
            "valid_source_candidates": valid_sources,
        },
        "action_tables": {
            "pr_actions": pr_actions,
            "po_expedite_actions": po_actions,
        },
        "limitations": sorted(set(limitations)),
        "source_complete": all_sources_complete,
    }
    last_mrp_dates = [
        parsed
        for row in coverage_rows
        for parsed in [_date(row.get("MaterialLastMRPDateTime"))]
        if parsed is not None
    ]
    latest_mrp = max(last_mrp_dates) if last_mrp_dates else None
    observations = []
    if action_regression and action_coverage and not qualified:
        observations.append(
            {
                "code": "zero_shortage_action_regression",
                "severity": "info",
                "blocking": False,
                "detail": "The exact MaterialCoverages row is retained to validate procurement action tables; its shortage quantity is zero.",
            }
        )
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
                        for row in coverage_for_records
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
