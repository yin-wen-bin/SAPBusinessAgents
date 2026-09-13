from __future__ import annotations

from copy import deepcopy


def _title(zh: str, en: str) -> dict[str, str]:
    return {"zh": zh, "en": en}


def _binding(source_step: str, field: str, source_field: str) -> dict:
    return {
        "source_step_id": source_step,
        "field": field,
        "source_field": source_field,
        "fanout": True,
        "fetch_all_for_binding": True,
    }


def _step(step_id: str, service: str, entity: str, *, filters=None, bindings=None, select=None, order=None) -> dict:
    return {
        "step_id": step_id,
        "service_name": service,
        "odata_version": "2.0",
        "entity_set": entity,
        "http_method": "GET",
        "filters": filters or [],
        **({"filter_from_previous": bindings} if bindings else {}),
        "select_fields": select or [],
        "order_by": order or [],
        "response_summary_fields": select or [],
    }


def build_manifest(base: dict, managed_rule_digest: str) -> dict:
    manifest = deepcopy(base)
    manifest.update(
        title=_title("采购订单入库状态检查", "Purchase order receipt status check"),
        summary=_title(
            "按计划行交货日期核对采购订单的实际入库数量、未收货数量及入库状态。",
            "Check actual receipts, open quantity, and receipt status for purchase orders due on a schedule-line delivery date.",
        ),
        inputs={
            "zh": ["计划行交货日期", "采购组织"],
            "en": ["Schedule-line delivery date", "Purchasing organization"],
        },
        outputs={
            "zh": ["业务状态", "查询源完整性", "业务证据完整性", "目标计划行数量", "全部入库数量", "部分入库数量", "未入库数量", "无法确认数量", "逐计划行明细", "业务报告"],
            "en": ["Business status", "Query-source completeness", "Business-evidence completeness", "Target schedule-line count", "Fully received count", "Partially received count", "Not received count", "Inconclusive count", "Schedule-line details", "Business report"],
        },
        guardrails={
            "zh": ["严格只读；仅执行SAP OData GET。", "不执行单位换算；单位不可直接比较时返回无法确认。", "SAP交货完成标识不替代物料凭证收货证据。"],
            "en": ["Strictly read-only; only SAP OData GET is used.", "No unit conversion is performed; non-comparable units produce an inconclusive result.", "The SAP delivery-complete flag does not replace material-document receipt evidence."],
        },
        managedRule={"entrypoint": "evaluate", "sha256": managed_rule_digest},
    )
    po_service = "API_PURCHASEORDER_PROCESS_SRV"
    material_service = "API_MATERIAL_DOCUMENT_SRV"
    date_filter = [{"field": "ScheduleLineDeliveryDate", "operator": "eq", "value": "{{input.schedule_line_delivery_date}}"}]
    item_bindings = [
        _binding("target_schedule_lines", "PurchaseOrder", "PurchasingDocument"),
        _binding("target_schedule_lines", "PurchaseOrderItem", "PurchasingDocumentItem"),
    ]
    all_item_bindings = [
        _binding("purchase_order_items", "PurchasingDocument", "PurchaseOrder"),
        _binding("purchase_order_items", "PurchasingDocumentItem", "PurchaseOrderItem"),
    ]
    receipt_item_bindings = [
        _binding("purchase_order_items", "PurchaseOrder", "PurchaseOrder"),
        _binding("purchase_order_items", "PurchaseOrderItem", "PurchaseOrderItem"),
    ]
    plan = {
        "schema_version": "1.0",
        "plan_kind": "multi_step",
        "steps": [
            _step(
                "target_schedule_lines", po_service, "A_PurchaseOrderScheduleLine",
                filters=date_filter,
                select=["PurchasingDocument", "PurchasingDocumentItem", "ScheduleLine", "ScheduleLineDeliveryDate", "ScheduleLineOrderQuantity", "PurchaseOrderQuantityUnit"],
                order=["PurchasingDocument", "PurchasingDocumentItem", "ScheduleLine"],
            ),
            _step(
                "purchase_order_items", po_service, "A_PurchaseOrderItem",
                bindings=item_bindings,
                select=["PurchaseOrder", "PurchaseOrderItem", "Material", "PurchaseOrderItemText", "Plant", "StorageLocation", "OrderQuantity", "PurchaseOrderQuantityUnit", "PurchasingDocumentDeletionCode", "PurchaseOrderItemCategory", "GoodsReceiptIsExpected", "GoodsReceiptIsNonValuated", "IsCompletelyDelivered"],
                order=["PurchaseOrder", "PurchaseOrderItem"],
            ),
            _step(
                "purchase_order_headers", po_service, "A_PurchaseOrder",
                filters=[{"field": "PurchasingOrganization", "operator": "eq", "value": "{{input.purchasing_organization}}", "omitIfEmpty": "{{input.purchasing_organization?}}"}],
                bindings=[_binding("purchase_order_items", "PurchaseOrder", "PurchaseOrder")],
                select=["PurchaseOrder", "Supplier", "PurchasingOrganization", "PurchasingGroup", "PurchaseOrderDate"],
                order=["PurchaseOrder"],
            ),
            _step(
                "all_schedule_lines", po_service, "A_PurchaseOrderScheduleLine",
                bindings=all_item_bindings,
                select=["PurchasingDocument", "PurchasingDocumentItem", "ScheduleLine", "ScheduleLineDeliveryDate", "ScheduleLineOrderQuantity", "PurchaseOrderQuantityUnit"],
                order=["PurchasingDocument", "PurchasingDocumentItem", "ScheduleLine"],
            ),
            _step(
                "material_document_items", material_service, "A_MaterialDocumentItem",
                bindings=receipt_item_bindings,
                select=["MaterialDocumentYear", "MaterialDocument", "MaterialDocumentItem", "PurchaseOrder", "PurchaseOrderItem", "Material", "Plant", "StorageLocation", "GoodsMovementType", "DebitCreditCode", "GoodsMovementIsCancelled", "QuantityInEntryUnit", "EntryUnit", "QuantityInBaseUnit", "MaterialBaseUnit", "ReversedMaterialDocumentYear", "ReversedMaterialDocument", "ReversedMaterialDocumentItem"],
                order=["MaterialDocumentYear", "MaterialDocument", "MaterialDocumentItem"],
            ),
            _step(
                "material_document_headers", material_service, "A_MaterialDocumentHeader",
                bindings=[
                    _binding("material_document_items", "MaterialDocumentYear", "MaterialDocumentYear"),
                    _binding("material_document_items", "MaterialDocument", "MaterialDocument"),
                ],
                select=["MaterialDocumentYear", "MaterialDocument", "PostingDate", "DocumentDate", "CreationDate", "CreationTime", "GoodsMovementCode", "InventoryTransactionType"],
                order=["MaterialDocumentYear", "MaterialDocument"],
            ),
        ],
    }
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["schedule_line_delivery_date"],
        "properties": {
            "schedule_line_delivery_date": {"type": "string", "format": "date", "minLength": 1, "title": _title("计划行交货日期", "Schedule-line delivery date")},
            "purchasing_organization": {"type": "string", "minLength": 1, "title": _title("采购组织", "Purchasing organization")},
        },
    }
    nullable_text = {"type": ["string", "null"]}
    record_properties = {
        "purchase_order": {"type": "string"},
        "purchase_order_item": {"type": "string"},
        "schedule_line": {"type": "string"},
        "schedule_line_delivery_date": {"type": "string", "format": "date"},
        "supplier": nullable_text,
        "purchasing_organization": nullable_text,
        "purchasing_group": nullable_text,
        "plant": nullable_text,
        "material": nullable_text,
        "scheduled_quantity": {"type": ["string", "null"], "pattern": "^-?[0-9]+(?:\\.[0-9]+)?$"},
        "received_quantity": {"type": ["string", "null"], "pattern": "^-?[0-9]+(?:\\.[0-9]+)?$"},
        "open_quantity": {"type": ["string", "null"], "pattern": "^-?[0-9]+(?:\\.[0-9]+)?$"},
        "unit": nullable_text,
        "latest_goods_receipt_posting_date": {"type": ["string", "null"], "format": "date"},
        "delivery_complete": {"type": "boolean"},
        "receipt_status": {
            "type": "string",
            "enum": ["fully_received", "partially_received", "not_received", "inconclusive"],
            "x-sapba-display": {
                "format": "enum",
                "labels": {
                    "fully_received": _title("全部入库", "Fully received"),
                    "partially_received": _title("部分入库", "Partially received"),
                    "not_received": _title("未入库", "Not received"),
                    "inconclusive": _title("无法确认", "Inconclusive"),
                },
            },
        },
    }
    field_titles = {
        "business_status": _title("业务状态", "Business status"),
        "source_complete": _title("查询源完整性", "Query-source completeness"),
        "evidence_complete": _title("业务证据完整性", "Business-evidence completeness"),
        "schedule_line_count": _title("目标计划行数量", "Target schedule-line count"),
        "fully_received_count": _title("全部入库数量", "Fully received count"),
        "partially_received_count": _title("部分入库数量", "Partially received count"),
        "not_received_count": _title("未入库数量", "Not received count"),
        "inconclusive_count": _title("无法确认数量", "Inconclusive count"),
        "records": _title("逐计划行明细", "Schedule-line details"),
        "business_report": _title("业务报告", "Business report"),
    }
    output_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": list(field_titles),
        "properties": {
            "business_status": {
                "type": "string", "enum": ["normal", "attention", "inconclusive"], "title": field_titles["business_status"],
                "x-sapba-display": {"format": "enum", "labels": {
                    "normal": _title("正常", "Normal"),
                    "attention": _title("需要关注", "Needs attention"),
                    "inconclusive": _title("无法确认", "Inconclusive"),
                }},
            },
            "source_complete": {"type": "boolean", "title": field_titles["source_complete"]},
            "evidence_complete": {"type": "boolean", "title": field_titles["evidence_complete"]},
            **{name: {"type": "integer", "minimum": 0, "title": field_titles[name]} for name in ("schedule_line_count", "fully_received_count", "partially_received_count", "not_received_count", "inconclusive_count")},
            "records": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": list(record_properties), "properties": record_properties}, "title": field_titles["records"]},
            "business_report": {"type": "object", "title": field_titles["business_report"]},
        },
    }
    output_mapping = {
        name: "{{steps.evaluate_receipt_status.output.workflow_output." + name + "}}"
        for name in field_titles
    }
    manifest["workflow"] = [
        {
            "id": "read-po-receipt-evidence",
            "title": _title("读取采购订单与入库证据", "Read purchase-order and receipt evidence"),
            "description": _title("按目标日期读取计划行、采购订单项目与抬头、全部计划行以及物料凭证项目与抬头。", "Read target and complete schedule lines, purchase-order items and headers, and material-document items and headers."),
            "tools": [{"name": "sap_read_execute_plan", "kind": "SAP Read Provider", "purpose": _title("执行确定性的OData GET计划。", "Execute the deterministic OData GET plan.")}],
            "executionStepIds": ["collect_po_receipt_evidence"],
        },
        {
            "id": "assess-po-receipt-status",
            "title": _title("计算计划行入库状态", "Calculate schedule-line receipt status"),
            "description": _title("按稳定业务键去重，计算收货与冲销净额，并按计划行顺序分配后形成业务结果。", "Deduplicate stable business keys, calculate net receipts and reversals, and allocate them by schedule-line order to produce the business result."),
            "tools": [{"name": "managed_agent_rule", "kind": "Managed deterministic rule", "purpose": _title("生成入库指标、明细与建议。", "Produce receipt metrics, details, and recommendations.")}],
            "executionStepIds": ["evaluate_receipt_status"],
        },
    ]
    manifest["execution"] = {
        "mode": "deterministic",
        "relationshipPolicy": "advisory",
        "inputSchema": input_schema,
        "steps": [
            {"id": "collect_po_receipt_evidence", "executor": "sap_read", "operation": "execute_plan", "readOnly": True, "request": {"plan": plan}},
            {"id": "evaluate_receipt_status", "executor": "rule", "operation": "managed_agent_rule", "inputMapping": {"run_input": {"schedule_line_delivery_date": "{{input.schedule_line_delivery_date}}", "purchasing_organization": "{{input.purchasing_organization?}}"}, "evidence": "{{steps.collect_po_receipt_evidence.output}}"}},
        ],
        "outputSchema": output_schema,
        "outputMapping": output_mapping,
        "acceptance": {
            "schemaVersion": "1.0",
            "comparisonMode": "business_semantic",
            "businessKeys": ["purchase_order", "purchase_order_item", "schedule_line"],
            "facts": ["receipt_status", "delivery_complete", "source_complete", "evidence_complete"],
            "metrics": ["schedule_line_count", "fully_received_count", "partially_received_count", "not_received_count", "inconclusive_count"],
            "currencyAndUnitPolicy": "compare_only_when_same_or_conversion_validated",
            "requiredLimitations": ["no_unit_conversion", "delivery_complete_is_not_receipt_evidence"],
        },
    }
    return manifest
