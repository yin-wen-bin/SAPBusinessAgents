from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def filt(field: str, value: str, *, operator: str = "eq", value_type: str = "string") -> dict[str, Any]:
    return {"field": field, "operator": operator, "value": value, "value_type": value_type}


def bind(field: str, source_step_id: str, source_field: str) -> dict[str, Any]:
    return {"field": field, "source_step_id": source_step_id, "source_field": source_field, "fanout": True, "fetch_all_for_binding": True}


def direct(service: str, entity: str, fields: list[str], filters: list[dict[str, Any]], rationale: str) -> dict[str, Any]:
    return {"service_name": service, "odata_version": "2.0", "entity_set": entity, "http_method": "GET", "plan_kind": "direct", "select_fields": fields, "filters": filters, "rationale": rationale}


def multi(service: str, entity: str, steps: list[dict[str, Any]], rationale: str) -> dict[str, Any]:
    return {"service_name": service, "odata_version": "2.0", "entity_set": entity, "http_method": "GET", "plan_kind": "multi_step", "steps": steps, "rationale": rationale}


def step(step_id: str, service: str, entity: str, fields: list[str], *, filters=None, bindings=None) -> dict[str, Any]:
    value = {"step_id": step_id, "service_name": service, "odata_version": "2.0", "entity_set": entity, "http_method": "GET", "select_fields": fields}
    if filters:
        value["filters"] = filters
    if bindings:
        value["filter_from_previous"] = bindings
    return value


def load(agent: str) -> tuple[Path, dict[str, Any]]:
    path = ROOT / "agents" / "MM" / agent / "agent.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def by_id(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in manifest["execution"]["steps"]}


def inventory() -> None:
    path, manifest = load("inventory-health-balancing")
    steps = by_id(manifest)
    scope_filters = [filt("Material", "{{input.material}}"), filt("Plant", "{{input.plant}}"), filt("StorageLocation", "{{input.storage_location}}")]
    steps["read_movement"]["request"]["plan"] = direct(
        "API_MATERIAL_DOCUMENT_SRV", "A_MaterialDocumentItem",
        ["MaterialDocumentYear", "MaterialDocument", "MaterialDocumentItem", "Material", "Plant", "StorageLocation", "GoodsMovementType", "QuantityInEntryUnit", "EntryUnit", "DebitCreditCode", "ReversedMaterialDocument"],
        scope_filters, "Read exact material-document items; posting dates are joined from headers in the next GET-only step.",
    )
    steps["read_movement_dates"]["request"]["plan"] = multi(
        "API_MATERIAL_DOCUMENT_SRV", "A_MaterialDocumentItem",
        [
            step("movement_date_items", "API_MATERIAL_DOCUMENT_SRV", "A_MaterialDocumentItem", ["MaterialDocumentYear", "MaterialDocument", "MaterialDocumentItem", "Material", "Plant", "StorageLocation"], filters=scope_filters),
            step("movement_date_headers", "API_MATERIAL_DOCUMENT_SRV", "A_MaterialDocumentHeader", ["MaterialDocumentYear", "MaterialDocument", "PostingDate"], filters=[filt("PostingDate", "{{input.date_from}}", operator="ge", value_type="date_start"), filt("PostingDate", "{{input.as_of}}", operator="le", value_type="date_end")], bindings=[bind("MaterialDocumentYear", "movement_date_items", "MaterialDocumentYear"), bind("MaterialDocument", "movement_date_items", "MaterialDocument")]),
        ],
        "Join material-document header posting dates to exact movement keys and apply the requested date window.",
    )
    steps["read_batch_parameters"]["request"]["plan"] = direct(
        "API_BATCH_SRV", "Batch",
        ["Material", "BatchIdentifyingPlant", "Batch", "ShelfLifeExpirationDate", "ManufactureDate", "BatchIsMarkedForDeletion"],
        [filt("Material", "{{input.material}}"), filt("BatchIdentifyingPlant", "{{input.plant}}")],
        "Read target-system Batch entities and expiry dates for the exact material and identifying plant.",
    )
    save(path, manifest)


def shortage() -> None:
    path, manifest = load("material-shortage-procurement-response")
    steps = by_id(manifest)
    master_step = {
        "id": "read_mrp_master",
        "executor": "sap_read",
        "operation": "execute_plan",
        "readOnly": True,
        "failurePolicy": "record_gap",
        "request": {
            "plan": direct(
                "API_MRP_MATERIALS_SRV_01",
                "A_MRPMaterial",
                [
                    "Material", "MRPArea", "MRPPlant", "MRPController", "MRPType",
                    "MaterialProcurementCategory", "MaterialProcurementCatName", "BaseUnit",
                    "UnitOfMeasureName", "SafetyStockQuantity",
                ],
                [
                    filt("Material", "{{input.material}}"),
                    filt("MRPArea", "{{input.mrp_area}}"),
                    filt("MRPPlant", "{{input.plant}}"),
                ],
                "Confirm that the exact MRP material is externally procured and read its base-unit parameters.",
            )
        },
    }
    if "read_mrp_master" in steps:
        steps["read_mrp_master"].update(master_step)
    else:
        read_mrp_index = next(
            index
            for index, item in enumerate(manifest["execution"]["steps"])
            if item.get("id") == "read_mrp"
        )
        manifest["execution"]["steps"].insert(read_mrp_index, master_step)
        steps = by_id(manifest)
    if not any(
        "read_mrp_master" in (item.get("executionStepIds") or [])
        for item in manifest.get("workflow") or []
    ):
        manifest["workflow"].insert(
            0,
            {
                "id": "execute-read_mrp_master",
                "title": {
                    "zh": "确认外购 MRP 主数据",
                    "en": "Confirm externally procured MRP master data",
                },
                "description": {
                    "zh": "执行清单步骤 `read_mrp_master`，确认目标物料的 MRP 范围、采购类型和基础单位。",
                    "en": "Run manifest step `read_mrp_master` to confirm the material's MRP scope, procurement category, and base unit.",
                },
                "operations": {
                    "zh": ["GET API_MRP_MATERIALS_SRV_01@2.0/A_MRPMaterial"],
                    "en": ["GET API_MRP_MATERIALS_SRV_01@2.0/A_MRPMaterial"],
                },
                "tools": [
                    {
                        "name": "Embedded SAP OData Provider",
                        "kind": "GET-only SAP Provider",
                        "purpose": {
                            "zh": "执行 `read_mrp_master` / `execute_plan`",
                            "en": "Execute `read_mrp_master` / `execute_plan`",
                        },
                    }
                ],
                "executionStepIds": ["read_mrp_master"],
            },
        )
    steps["read_mrp"]["request"]["plan"]["select_fields"] = [
        "Material", "MRPArea", "MRPPlant", "MRPPlanningSegmentNumber",
        "MRPPlanningSegmentType", "MaterialBaseUnit", "MaterialShortageQuantity",
        "MaterialShortageStartDate", "MaterialShortageEndDate", "MaterialShortageDuration",
        "DaysOfSupplyDuration", "MRPController", "VltdUnrestrictedUseStkQty",
        "MaterialLastMRPDateTime", "MaterialShortageProfile", "MaterialShortageProfileCount",
    ]
    steps["read_pr_release"]["request"]["plan"] = direct(
        "API_PURCHASEREQ_PROCESS_SRV", "A_PurchaseRequisitionItem",
        ["PurchaseRequisition", "PurchaseRequisitionItem", "Material", "Plant", "DeliveryDate", "RequestedQuantity", "OrderedQuantity", "BaseUnit", "ProcessingStatus", "PurReqnReleaseStatus", "ReleaseIsNotCompleted", "IsClosed", "IsDeleted"],
        [filt("Material", "{{input.material}}"), filt("Plant", "{{input.plant}}")],
        "Read the target-system processing status exposed for exact purchase-requisition items.",
    )
    po_item_filters = [filt("Material", "{{input.material}}"), filt("Plant", "{{input.plant}}")]
    steps["read_po_schedule"]["request"]["plan"] = multi(
        "API_PURCHASEORDER_PROCESS_SRV", "A_PurchaseOrderItem",
        [
            step("schedule_po_items", "API_PURCHASEORDER_PROCESS_SRV", "A_PurchaseOrderItem", ["PurchaseOrder", "PurchaseOrderItem", "Material", "Plant"], filters=po_item_filters),
            step("po_schedules", "API_PURCHASEORDER_PROCESS_SRV", "A_PurchaseOrderScheduleLine", ["PurchasingDocument", "PurchasingDocumentItem", "ScheduleLine", "ScheduleLineDeliveryDate", "ScheduleLineOrderQuantity", "ScheduleLineCommittedQuantity", "PurchaseOrderQuantityUnit"], bindings=[bind("PurchasingDocument", "schedule_po_items", "PurchaseOrder"), bind("PurchasingDocumentItem", "schedule_po_items", "PurchaseOrderItem")]),
        ],
        "Join live purchase-order items to schedule lines using the target-system PurchasingDocument keys.",
    )
    steps["read_source"]["request"]["plan"] = direct(
        "API_INFORECORD_PROCESS_SRV", "A_PurgInfoRecdOrgPlantData",
        ["PurchasingInfoRecord", "PurchasingInfoRecordCategory", "PurchasingOrganization", "Plant", "Supplier", "Material", "PurgDocOrderQuantityUnit", "MaterialPlannedDeliveryDurn", "IsMarkedForDeletion", "IsRelevantForAutomSrcg"],
        [filt("Material", "{{input.material}}"), filt("PurchasingOrganization", "{{input.purchasing_organization}}"), filt("Plant", "{{input.plant}}")],
        "Read source-list evidence from the organization/plant purchasing-info-record entity exposed by live metadata.",
    )
    steps["assess"]["inputMapping"]["checks"]["mrp"] = {
        "master": "{{steps.read_mrp_master.output}}",
        "coverage": "{{steps.read_mrp.output}}",
    }
    steps["evaluate"]["inputMapping"]["evidence"]["mrp_master"] = (
        "{{steps.read_mrp_master.output}}"
    )
    acceptance = manifest["execution"]["acceptance"]
    acceptance["recordScope"] = (
        "Return only authoritative MaterialCoverages rows as comparison records. "
        "MRP master data, SupplyDemandItems, purchase requisitions, PO schedule lines, "
        "and source candidates are contextual evidence summarized by metrics or diagnostics, "
        "not comparison records."
    )
    acceptance["metricDefinitions"]["valid_source_candidates"] = (
        "Count exact purchasing-organization/plant info-record rows that are not marked "
        "for deletion and are relevant for automatic sourcing."
    )
    acceptance.setdefault("valueMappings", {}).setdefault("mrp_element_type", {})[
        "02"
    ] = "material_coverage"
    acceptance["compositeBlankFields"] = ["requirement_id"]
    acceptance["compositeKeyParts"] = {
        "requirement_id": [
            {"name": "profile", "aliases": ["MaterialShortageProfile"]},
            {"name": "counter", "aliases": ["MaterialShortageProfileCount"]},
            {"name": "mrp_area", "aliases": ["MRPArea"]},
            {"name": "segment", "aliases": ["MRPPlanningSegmentNumber"]},
            {"name": "segment_type", "aliases": ["MRPPlanningSegmentType"]},
        ]
    }
    acceptance["nonBlockingObservationCodes"] = ["mrp_snapshot_stale"]
    acceptance["testDataQualificationDefinition"] = (
        "A sample is qualified when the authoritative coverage shortage is positive and active "
        "at the as-of date, MRP master data confirms external procurement category F, and all "
        "required source queries are complete. Complete exact zero-row PR, PO schedule, or source "
        "branches establish zero and do not disqualify the sample. MRP snapshot staleness is a "
        "non-blocking warning."
    )
    acceptance["ignoredNoticeKeywords"] = [
        "non-blocking observation",
        "non-blocking warning",
        "非阻断观察",
        "非阻断警告",
    ]
    save(path, manifest)


def rfq() -> None:
    path, manifest = load("intelligent-sourcing-rfq")
    steps = by_id(manifest)
    rfq_filter = [filt("RequestForQuotation", "{{input.rfq}}")]
    steps["read_rfq_status"]["request"]["plan"] = direct(
        "API_RFQ_PROCESS_SRV", "A_RequestForQuotation", ["RequestForQuotation", "PurchasingOrganization", "DocumentCurrency"], rfq_filter,
        "Re-read the exact RFQ using only fields exposed by live metadata; quotation lifecycle is read from quotation headers.",
    )
    quote_items = step(
        "quotation_items", "API_QTN_PROCESS_SRV", "A_SupplierQuotationItem",
        ["SupplierQuotation", "SupplierQuotationItem", "RequestForQuotation", "RequestForQuotationItem", "Material", "Plant", "ScheduleLineDeliveryDate", "ScheduleLineOrderQuantity", "OrderQuantityUnit", "NetPriceAmount", "NetPriceQuantity", "DocumentCurrency"], filters=rfq_filter,
    )
    quote_headers = step(
        "quotation_headers", "API_QTN_PROCESS_SRV", "A_SupplierQuotation",
        ["SupplierQuotation", "Supplier", "PurchasingOrganization", "DocumentCurrency", "RequestForQuotation", "QuotationSubmissionDate", "BindingPeriodValidityEndDate", "QTNLifecycleStatus"],
        bindings=[bind("SupplierQuotation", "quotation_items", "SupplierQuotation")],
    )
    supplier_status = step(
        "supplier_status", "API_BUSINESS_PARTNER", "A_SupplierPurchasingOrg",
        ["Supplier", "PurchasingOrganization", "PurchasingIsBlockedForSupplier", "DeletionIndicator"],
        filters=[filt("PurchasingOrganization", "{{input.purchasing_organization}}")],
        bindings=[bind("Supplier", "quotation_headers", "Supplier")],
    )
    steps["read_quotation"]["request"]["plan"] = multi(
        "API_QTN_PROCESS_SRV", "A_SupplierQuotationItem", [quote_items, quote_headers, supplier_status],
        "Read quotation items, join quotation headers for supplier/lifecycle data, and read purchasing-organization supplier status.",
    )
    steps["read_quotation_comparison"]["request"]["plan"] = direct(
        "API_QTN_PROCESS_SRV", "A_SupplierQuotation",
        ["SupplierQuotation", "Supplier", "PurchasingOrganization", "DocumentCurrency", "RequestForQuotation", "QuotationSubmissionDate", "BindingPeriodValidityEndDate", "QTNLifecycleStatus"],
        rfq_filter, "Read complete quotation-header comparison fields for the exact RFQ.",
    )
    steps["read_supplier_source"]["request"]["plan"] = direct(
        "API_INFORECORD_PROCESS_SRV", "A_PurgInfoRecdOrgPlantData",
        ["PurchasingInfoRecord", "PurchasingInfoRecordCategory", "PurchasingOrganization", "Plant", "Supplier", "Material", "PurgDocOrderQuantityUnit", "NetPriceQuantityUnit", "NetPriceAmount", "MaterialPriceUnitQty", "IsMarkedForDeletion", "IsRelevantForAutomSrcg"],
        [filt("PurchasingOrganization", "{{input.purchasing_organization}}")],
        "Read purchasing-organization source records using the live organization/plant entity.",
    )
    assess = steps["assess"]["inputMapping"]
    assess["checks"]["supplier"] = "{{steps.read_quotation.output}}"
    evaluate = next(item for item in manifest["execution"]["steps"] if item.get("executor") == "rule" and item.get("operation") == "evaluate_business_agent")
    evaluate["inputMapping"]["evidence"]["supplier"] = "{{steps.read_quotation.output}}"
    save(path, manifest)


def supplier() -> None:
    path, manifest = load("supplier-performance-risk")
    steps = by_id(manifest)
    po_filters = [
        filt("Supplier", "{{input.supplier}}"), filt("PurchasingOrganization", "{{input.purchasing_organization}}"),
        filt("PurchaseOrderDate", "{{input.date_from}}", operator="ge", value_type="date_start"),
        filt("PurchaseOrderDate", "{{input.date_to}}", operator="le", value_type="date_end"),
    ]
    po_header = step("schedule_po_headers", "API_PURCHASEORDER_PROCESS_SRV", "A_PurchaseOrder", ["PurchaseOrder", "Supplier", "PurchasingOrganization", "PurchaseOrderDate"], filters=po_filters)
    po_items = step("schedule_po_items", "API_PURCHASEORDER_PROCESS_SRV", "A_PurchaseOrderItem", ["PurchaseOrder", "PurchaseOrderItem", "Plant", "Material"], bindings=[bind("PurchaseOrder", "schedule_po_headers", "PurchaseOrder")])
    schedules = step("po_schedules", "API_PURCHASEORDER_PROCESS_SRV", "A_PurchaseOrderScheduleLine", ["PurchasingDocument", "PurchasingDocumentItem", "ScheduleLine", "ScheduleLineDeliveryDate", "ScheduleLineOrderQuantity", "ScheduleLineCommittedQuantity", "PurchaseOrderQuantityUnit"], bindings=[bind("PurchasingDocument", "schedule_po_items", "PurchaseOrder"), bind("PurchasingDocumentItem", "schedule_po_items", "PurchaseOrderItem")])
    steps["read_po_schedule"]["request"]["plan"] = multi("API_PURCHASEORDER_PROCESS_SRV", "A_PurchaseOrder", [po_header, po_items, schedules], "Join supplier purchase orders and live PurchasingDocument schedule-line keys within the bounded horizon.")

    date_po = step("date_scope_po", "API_PURCHASEORDER_PROCESS_SRV", "A_PurchaseOrder", ["PurchaseOrder"], filters=po_filters)
    receipt_items = step("receipt_items", "API_MATERIAL_DOCUMENT_SRV", "A_MaterialDocumentItem", ["MaterialDocumentYear", "MaterialDocument", "MaterialDocumentItem", "PurchaseOrder", "PurchaseOrderItem", "GoodsMovementType", "QuantityInEntryUnit", "EntryUnit", "DebitCreditCode", "GoodsMovementIsCancelled"], bindings=[bind("PurchaseOrder", "date_scope_po", "PurchaseOrder")])
    receipt_headers = step("receipt_headers", "API_MATERIAL_DOCUMENT_SRV", "A_MaterialDocumentHeader", ["MaterialDocumentYear", "MaterialDocument", "PostingDate"], bindings=[bind("MaterialDocumentYear", "receipt_items", "MaterialDocumentYear"), bind("MaterialDocument", "receipt_items", "MaterialDocument")])
    steps["read_receipt_dates"]["request"]["plan"] = multi("API_PURCHASEORDER_PROCESS_SRV", "A_PurchaseOrder", [date_po, receipt_items, receipt_headers], "Join receipt-item keys to material-document headers to obtain authoritative posting dates.")
    save(path, manifest)


def relationships() -> None:
    path = ROOT / "config" / "business-relationships.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    semantics = value["field_semantics"]
    additions = [
        ("API_PURCHASEORDER_PROCESS_SRV", "A_PurchaseOrderScheduleLine", "PurchasingDocument", "purchase_order_id"),
        ("API_PURCHASEORDER_PROCESS_SRV", "A_PurchaseOrderScheduleLine", "PurchasingDocumentItem", "purchase_order_item_id"),
        ("API_QTN_PROCESS_SRV", "A_SupplierQuotationItem", "SupplierQuotation", "supplier_quotation_id"),
        ("API_QTN_PROCESS_SRV", "A_SupplierQuotation", "SupplierQuotation", "supplier_quotation_id"),
        ("API_QTN_PROCESS_SRV", "A_SupplierQuotation", "Supplier", "supplier_id"),
        ("API_BUSINESS_PARTNER", "A_SupplierPurchasingOrg", "Supplier", "supplier_id"),
    ]
    existing = {(item["service_name"], item["entity_set"], item["field"]) for item in semantics}
    for service, entity, field, semantic in additions:
        if (service, entity, field) not in existing:
            semantics.append({"service_name": service, "odata_version": "2.0", "entity_set": entity, "field": field, "semantic": semantic})
    relations = value["relationships"]
    relation_additions = [
        {
            "id": "mm-po-item-schedule-document",
            "modes": ["binding"],
            "source": {"service_name": "API_PURCHASEORDER_PROCESS_SRV", "odata_version": "2.0", "entity_set": "A_PurchaseOrderItem", "field": "PurchaseOrder"},
            "target": {"service_name": "API_PURCHASEORDER_PROCESS_SRV", "odata_version": "2.0", "entity_set": "A_PurchaseOrderScheduleLine", "field": "PurchasingDocument"},
        },
        {
            "id": "mm-po-item-schedule-item",
            "modes": ["binding"],
            "source": {"service_name": "API_PURCHASEORDER_PROCESS_SRV", "odata_version": "2.0", "entity_set": "A_PurchaseOrderItem", "field": "PurchaseOrderItem"},
            "target": {"service_name": "API_PURCHASEORDER_PROCESS_SRV", "odata_version": "2.0", "entity_set": "A_PurchaseOrderScheduleLine", "field": "PurchasingDocumentItem"},
        },
        {
            "id": "mm-quotation-item-header",
            "modes": ["binding"],
            "source": {"service_name": "API_QTN_PROCESS_SRV", "odata_version": "2.0", "entity_set": "A_SupplierQuotationItem", "field": "SupplierQuotation"},
            "target": {"service_name": "API_QTN_PROCESS_SRV", "odata_version": "2.0", "entity_set": "A_SupplierQuotation", "field": "SupplierQuotation"},
        },
        {
            "id": "mm-quotation-supplier-status",
            "modes": ["binding"],
            "source": {"service_name": "API_QTN_PROCESS_SRV", "odata_version": "2.0", "entity_set": "A_SupplierQuotation", "field": "Supplier"},
            "target": {"service_name": "API_BUSINESS_PARTNER", "odata_version": "2.0", "entity_set": "A_SupplierPurchasingOrg", "field": "Supplier"},
        },
    ]
    existing_ids = {str(item.get("id") or "") for item in relations}
    relations.extend(item for item in relation_additions if item["id"] not in existing_ids)
    for item in relations:
        if item.get("id") in {
            "p2p-purchase-order-items",
            "p2p-purchase-order-material-document",
        }:
            item["modes"] = list(dict.fromkeys([*(item.get("modes") or []), "binding"]))
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    relationships()
    inventory()
    shortage()
    rfq()
    supplier()
    print(json.dumps({"updated": 4}))


if __name__ == "__main__":
    main()
