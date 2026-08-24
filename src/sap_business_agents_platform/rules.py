from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .agent_rules import evaluate_business_agent


P2P_PAYMENT_DOCUMENT_TYPES = frozenset({"KZ", "ZP"})
O2C_CUSTOMER_PAYMENT_DOCUMENT_TYPES = frozenset({"DZ"})

# Only authoritative schema/capability findings may open the ADT fallback path.
# Transport, authentication, timeout, truncation, and generic execution failures
# remain explicit operational gaps so the primary API failure is never hidden.
API_CAPABILITY_GAP_CODES = frozenset(
    {
        "schema_drift_entity_unavailable",
        "schema_drift_field_unavailable",
        "schema_drift_relationship_unavailable",
        "relationship_mapping_missing",
        "field_not_filterable",
        "field_not_selectable",
        "field_not_sortable",
    }
)


def evaluate(operation: str, inputs: dict[str, Any]) -> dict[str, Any]:
    if operation == "resolve_inventory_health_window":
        return resolve_inventory_health_window(inputs)
    if operation == "assess_inventory_batch_expiry":
        return assess_inventory_batch_expiry(inputs)
    if operation == "assess_api_evidence":
        return assess_api_evidence(inputs)
    if operation == "assess_adt_preflight":
        return assess_adt_preflight(inputs)
    if operation == "assess_billing_block_incompletion":
        return assess_billing_block_incompletion(inputs)
    if operation == "prepare_billing_block_code_text_lookups":
        return prepare_billing_block_code_text_lookups(inputs)
    if operation == "classify_control_object":
        return classify_control_object(inputs)
    if operation == "assess_o2c_document_flow":
        return assess_o2c_document_flow(inputs)
    if operation == "evidence_summary":
        return evidence_summary(inputs)
    if operation == "extract_bounded_values":
        return extract_bounded_values(inputs)
    if operation == "evaluate_business_agent":
        return evaluate_business_agent(inputs)
    if operation == "evaluate_p2p_status":
        return evaluate_p2p_status(inputs)
    if operation == "evaluate_o2c_status":
        return evaluate_o2c_status(inputs)
    raise ValueError(f"Unknown deterministic rule operation: {operation}")


def resolve_inventory_health_window(inputs: dict[str, Any]) -> dict[str, Any]:
    """Resolve optional inventory checks into a deterministic current-date window."""

    run_input = inputs.get("run_input")
    if not isinstance(run_input, dict):
        raise ValueError("resolve_inventory_health_window requires run_input")

    def optional_days(name: str) -> int | None:
        value = run_input.get(name)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 365:
            raise ValueError(f"{name} must be an integer between 1 and 365")
        return value

    slow_days = optional_days("slow_moving_days")
    obsolete_days = optional_days("obsolete_days")
    expiry_days = optional_days("expiry_days")
    if slow_days is not None and obsolete_days is not None and slow_days >= obsolete_days:
        raise ValueError("slow_moving_days must be less than obsolete_days")

    snapshot = date.today()
    movement_values = [value for value in (slow_days, obsolete_days) if value is not None]
    lookback = max(movement_values) if movement_values else None
    selected_checks = [
        name
        for name, enabled in (
            ("slow_moving", slow_days is not None),
            ("obsolete", obsolete_days is not None),
            ("expiry", expiry_days is not None),
        )
        if enabled
    ]
    return {
        "rule_id": "inventory_health_window_v1",
        "status": "complete",
        "snapshot_date": snapshot.isoformat(),
        "check_slow_moving": slow_days is not None,
        "check_obsolete": obsolete_days is not None,
        "check_expiry": expiry_days is not None,
        "movement_check_requested": bool(movement_values),
        "movement_history_required": bool(movement_values),
        "movement_lookback_days": lookback,
        # FIFO quantity aging needs the complete material-movement history.  The
        # thresholds classify the remaining layers; they must not bound the
        # source query or a recent receipt would reset the entire stock age.
        "movement_date_from": None,
        "movement_year_from": None,
        "movement_year_to": str(snapshot.year),
        "movement_years": [],
        "movement_history_to": snapshot.isoformat(),
        "selected_checks": selected_checks,
    }


def assess_inventory_batch_expiry(inputs: dict[str, Any]) -> dict[str, Any]:
    """Open the MCHA fallback only when positive-stock batch expiry is unresolved."""

    if inputs.get("requested") is not True:
        return {
            "rule_id": "inventory_batch_expiry_gap_assessment_v1",
            "status": "complete",
            "source_complete": True,
            "positive_batch_count": 0,
            "matched_batch_count": 0,
            "unresolved_batches": [],
            "conflicting_batches": [],
            "needs_adt": {"batch_expiry": False},
        }

    run_input = inputs.get("run_input")
    run_input = run_input if isinstance(run_input, dict) else {}
    stock_value = inputs.get("stock")
    stock_value = stock_value if isinstance(stock_value, dict) else {}
    confirmation = stock_value.get("confirmation")
    initial = stock_value.get("initial")

    def plan_rows(value: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for step in _step_results(value).values():
            rows.extend(
                dict(row) for row in step.get("results") or [] if isinstance(row, dict)
            )
        if rows:
            return rows
        if isinstance(value, dict):
            data = value.get("data")
            if isinstance(data, dict):
                rows.extend(
                    dict(row) for row in data.get("results") or [] if isinstance(row, dict)
                )
        return rows

    stock_rows = plan_rows(confirmation) or plan_rows(initial)
    material_input = str(run_input.get("material") or "").strip()
    plant_input = str(run_input.get("plant") or "").strip()
    positive_keys: set[tuple[str, str, str]] = set()
    for row in stock_rows:
        if str(row.get("InventoryStockType") or "").strip() not in {"", "01"}:
            continue
        if str(row.get("InventorySpecialStockType") or "").strip():
            continue
        quantity = _decimal_or_none(
            row.get("MatlWrhsStkQtyInMatlBaseUnit") or row.get("LABST")
        )
        batch = str(row.get("Batch") or row.get("CHARG") or "").strip()
        if quantity is None or quantity <= 0 or not batch:
            continue
        positive_keys.add(
            (
                str(row.get("Material") or row.get("MATNR") or material_input).strip(),
                str(row.get("Plant") or row.get("WERKS") or plant_input).strip(),
                batch,
            )
        )

    batch_payload = inputs.get("batch_expiry")
    batch_rows = plan_rows(batch_payload)
    source_complete = _source_complete(batch_payload)
    matched = 0
    unresolved: list[str] = []
    conflicts: list[str] = []
    for material, plant, batch in sorted(positive_keys):
        candidates = [
            row
            for row in batch_rows
            if str(row.get("Material") or row.get("MATNR") or "").strip() == material
            and str(row.get("Batch") or row.get("CHARG") or "").strip() == batch
        ]
        exact = [
            row
            for row in candidates
            if str(
                row.get("BatchIdentifyingPlant")
                or row.get("Plant")
                or row.get("WERKS")
                or ""
            ).strip()
            == plant
        ]
        material_level = [
            row
            for row in candidates
            if not str(
                row.get("BatchIdentifyingPlant")
                or row.get("Plant")
                or row.get("WERKS")
                or ""
            ).strip()
        ]
        usable = exact or material_level
        if not usable:
            unresolved.append(batch)
            continue
        matched += 1
        parseable_dates: set[str] = set()
        for row in usable:
            value = _sap_date_text(row.get("ShelfLifeExpirationDate") or row.get("VFDAT"))
            if not value:
                continue
            try:
                parseable_dates.add(date.fromisoformat(value[:10]).isoformat())
            except ValueError:
                continue
        if len(parseable_dates) > 1:
            conflicts.append(batch)
        elif not parseable_dates:
            unresolved.append(batch)

    needs_adt = not source_complete or bool(unresolved or conflicts)
    return {
        "rule_id": "inventory_batch_expiry_gap_assessment_v1",
        "status": "fallback_required" if needs_adt else "complete",
        "source_complete": source_complete,
        "positive_batch_count": len(positive_keys),
        "matched_batch_count": matched,
        "unresolved_batches": sorted(set(unresolved)),
        "conflicting_batches": sorted(set(conflicts)),
        "needs_adt": {"batch_expiry": needs_adt},
    }


def classify_control_object(inputs: dict[str, Any]) -> dict[str, Any]:
    object_type = str(inputs.get("object_type") or "").strip().upper()
    if object_type not in {"INTERNAL_ORDER", "WBS"}:
        raise ValueError("classify_control_object requires INTERNAL_ORDER or WBS")
    return {
        "rule_id": "classify_control_object_v1",
        "status": "complete",
        "object_type": object_type,
        "is_internal_order": object_type == "INTERNAL_ORDER",
        "is_wbs": object_type == "WBS",
    }


def assess_o2c_document_flow(inputs: dict[str, Any]) -> dict[str, Any]:
    """Request VBFA only when the complete OData result cannot prove a follow-on link."""

    payload = inputs.get("sap_read")
    steps = _step_results(payload)
    order_rows = _step_rows(steps, "sales_order")
    delivery_rows = _step_rows(steps, "delivery_items")
    billing_rows = _rows_for_prefixes(steps, ("billing_items",))
    source_complete = _source_complete(payload)
    relationship_proven = bool(delivery_rows or billing_rows)
    needs_adt = bool(order_rows) and source_complete and not relationship_proven
    return {
        "rule_id": "o2c_document_flow_gap_assessment_v1",
        "status": "fallback_required" if needs_adt else "complete",
        "source_complete": source_complete,
        "relationship_proven": relationship_proven,
        "needs_adt": {"document_flow": needs_adt},
    }


def _internal_sd_key(value: Any, width: int) -> str:
    text = str(value or "").strip()
    if text.isdigit() and len(text) <= width:
        return text.zfill(width)
    return text


def assess_billing_block_incompletion(inputs: dict[str, Any]) -> dict[str, Any]:
    """Request item-incompletion evidence only after a complete Embedded read."""

    payload = inputs.get("sap_read")
    run_input = inputs.get("run_input")
    run_input = run_input if isinstance(run_input, dict) else {}
    steps = _step_results(payload)
    order_rows = _step_rows(steps, "sales_orders")
    item_rows = _step_rows(steps, "sales_order_items")
    source_complete = _source_complete(payload)
    status_fields = ("UVALL", "UVVLK", "UVFAK", "UVPRS")
    embedded_status_complete = bool(item_rows) and all(
        all(field in row for field in status_fields) for row in item_rows
    )
    needs_adt = bool(order_rows and item_rows) and source_complete and not embedded_status_complete
    ordered_items = sorted(
        (
            _internal_sd_key(row.get("SalesOrderItem"), 6)
            for row in item_rows
            if str(row.get("SalesOrderItem") or "").strip()
        )
    )
    sales_order = (
        str(order_rows[0].get("SalesOrder") or "").strip()
        if order_rows
        else str(run_input.get("sales_order") or "").strip()
    )
    status = (
        "fallback_required"
        if needs_adt
        else "inconclusive"
        if not source_complete
        else "complete"
    )
    return {
        "rule_id": "billing_block_incompletion_gap_assessment_v1",
        "status": status,
        "source_complete": source_complete,
        "order_found": bool(order_rows),
        "item_count": len(item_rows),
        "embedded_status_complete": embedded_status_complete,
        "needs_adt": {"item_incompletion": needs_adt},
        "adt_sales_order": _internal_sd_key(sales_order, 10),
        "adt_preflight_item": ordered_items[0] if ordered_items else "",
    }


def prepare_billing_block_code_text_lookups(inputs: dict[str, Any]) -> dict[str, Any]:
    """Extract only observed block/status codes for bounded ADT text lookups."""

    steps = _step_results(inputs.get("sap_read"))
    orders = _step_rows(steps, "sales_orders")
    items = _step_rows(steps, "sales_order_items")
    delivery_headers = _step_rows(steps, "delivery_headers")
    delivery_items = _step_rows(steps, "delivery_items")

    def distinct(rows: list[dict[str, Any]], *fields: str) -> list[str]:
        return sorted(
            {
                str(row.get(field) or "").strip().upper()
                for row in rows
                for field in fields
                if str(row.get(field) or "").strip()
            }
        )

    billing_codes = distinct(
        orders + delivery_headers,
        "HeaderBillingBlockReason",
    )
    billing_codes = sorted(
        set(billing_codes).union(distinct(items + delivery_items, "ItemBillingBlockReason"))
    )
    delivery_codes = distinct(orders + delivery_headers, "DeliveryBlockReason")
    credit_codes = [
        value
        for value in distinct(orders + delivery_headers, "TotalCreditCheckStatus")
        if value != "C"
    ]

    fallback = inputs.get("item_incompletion")
    fallback = fallback if isinstance(fallback, dict) else {}
    incompletion_pairs = sorted(
        {
            (
                str(row.get("TBNAM") or "").strip().upper(),
                str(row.get("FDNAM") or "").strip().upper(),
            )
            for row in _rows_from_nested_payload(fallback)
            if str(row.get("TBNAM") or "").strip()
            and str(row.get("FDNAM") or "").strip()
        }
    )
    tables = sorted({table for table, _field in incompletion_pairs})
    fields = sorted({field for _table, field in incompletion_pairs})
    return {
        "rule_id": "billing_block_code_text_lookup_v1",
        "status": "complete",
        "billing_block_codes": billing_codes,
        "delivery_block_codes": delivery_codes,
        "credit_status_codes": credit_codes,
        "incompletion_tables": tables,
        "incompletion_fields": fields,
        "has_billing_block_codes": bool(billing_codes),
        "has_delivery_block_codes": bool(delivery_codes),
        "has_credit_status_codes": bool(credit_codes),
        "has_incompletion_fields": bool(incompletion_pairs),
        "first_billing_block_code": billing_codes[0] if billing_codes else "",
        "first_delivery_block_code": delivery_codes[0] if delivery_codes else "",
        "first_credit_status_code": credit_codes[0] if credit_codes else "",
        "first_incompletion_table": tables[0] if tables else "",
        "first_incompletion_field": fields[0] if fields else "",
    }


def assess_api_evidence(inputs: dict[str, Any]) -> dict[str, Any]:
    checks = inputs.get("checks")
    if not isinstance(checks, dict) or not checks:
        raise ValueError("assess_api_evidence requires named checks")
    declared = {
        str(item)
        for item in inputs.get("capability_gaps") or []
        if str(item).strip()
    }
    fallback_on_incomplete = {
        str(item)
        for item in inputs.get("fallback_on_incomplete") or []
        if str(item).strip()
    }
    requested = inputs.get("requested")
    requested = requested if isinstance(requested, dict) else {}
    needs_adt: dict[str, bool] = {}
    api_complete: dict[str, bool] = {}
    missing: list[str] = []
    capability_gaps: list[str] = []
    operational_gaps: list[str] = []
    for name, payload in checks.items():
        key = str(name)
        if requested.get(key) is False:
            api_complete[key] = True
            needs_adt[key] = False
            continue
        flags = _collect_source_complete(payload)
        ok_values = [value for value in _collect_values(payload, "ok") if isinstance(value, bool)]
        complete = bool(flags) and all(flags) and (not ok_values or all(ok_values))
        codes = {
            str(value)
            for value in _collect_values(payload, "code")
            if str(value).strip()
        }
        capability_gap = not complete and (
            key in declared
            or key in fallback_on_incomplete
            or bool(codes.intersection(API_CAPABILITY_GAP_CODES))
        )
        api_complete[key] = complete
        needs_adt[key] = capability_gap
        if not complete:
            missing.append(key)
            (capability_gaps if capability_gap else operational_gaps).append(key)
    for key in sorted(declared.difference(api_complete)):
        api_complete[key] = False
        needs_adt[key] = True
        missing.append(key)
        capability_gaps.append(key)
    status = (
        "fallback_required"
        if capability_gaps
        else "inconclusive"
        if operational_gaps
        else "complete"
    )
    return {
        "rule_id": "api_evidence_gap_assessment_v2",
        "status": status,
        "api_complete": api_complete,
        "needs_adt": needs_adt,
        "missing_evidence": sorted(set(missing)),
        "capability_gaps": sorted(set(capability_gaps)),
        "operational_gaps": sorted(set(operational_gaps)),
        "summary": {
            "zh": (
                "标准 API 证据完整。"
                if not missing
                else "实时 schema 确认 API 能力缺口，将调用 ADT 并以实时 DDIC 校验对象和字段。"
                if capability_gaps
                else "标准 API 执行或完整性不足；保持不确定且不触发 ADT。"
            ),
            "en": (
                "Standard API evidence is complete."
                if not missing
                else "Live schema confirms an API capability gap; ADT fallback will validate the object and fields against live DDIC metadata."
                if capability_gaps
                else "Standard API execution or completeness is insufficient; remain inconclusive without ADT fallback."
            ),
        },
    }


def assess_adt_preflight(inputs: dict[str, Any]) -> dict[str, Any]:
    payload = inputs.get("payload")
    completeness = payload.get("completeness") if isinstance(payload, dict) else None
    proceed = bool(
        isinstance(payload, dict)
        and payload.get("status") == "complete"
        and payload.get("read_only") is True
        and payload.get("validated") is True
        and isinstance(completeness, dict)
        and completeness.get("source_complete") is True
        and completeness.get("paging_complete") is True
        and not payload.get("validation_issues")
    )
    return {
        "rule_id": "adt_preflight_assessment_v1",
        "status": "complete" if proceed else "record_gap",
        "proceed": proceed,
        "source_complete": proceed,
    }


def extract_bounded_values(inputs: dict[str, Any]) -> dict[str, Any]:
    """Extract one static column for a bounded downstream typed IN filter."""

    payload = inputs.get("payload")
    field = str(inputs.get("field") or "")
    maximum = inputs.get("max_values", 100)
    if not field or not field.replace("_", "").isalnum() or not field[0].isalpha():
        raise ValueError("extract_bounded_values requires one static field identifier")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 100:
        raise ValueError("extract_bounded_values max_values must be between 1 and 100")
    values: list[Any] = []
    for group in _collect_values(payload, "rows"):
        if not isinstance(group, list):
            continue
        for row in group:
            if isinstance(row, dict) and row.get(field) not in {None, ""} and row[field] not in values:
                values.append(row[field])
    truncated = len(values) > maximum
    values = values[:maximum]
    flags = _collect_source_complete(payload)
    complete = bool(flags) and all(flags) and not truncated
    return {
        "rule_id": "extract_bounded_values_v1",
        "status": "complete" if complete else "inconclusive",
        "field": field,
        "values": values,
        "value_count": len(values),
        "has_values": bool(values),
        "first_value": values[0] if values else "",
        "source_complete": complete,
        "truncated": truncated,
    }


def evidence_summary(inputs: dict[str, Any]) -> dict[str, Any]:
    complete_flags = _collect_source_complete(inputs)
    case_ids = [str(value) for value in _collect_values(inputs, "case_id") if value]
    successful = sum(1 for value in _collect_values(inputs, "ok") if value is True)
    source_complete = bool(complete_flags) and all(complete_flags)
    if not complete_flags:
        reason = {
            "zh": "SAP 只读 Provider 没有返回数据范围完整性声明。",
            "en": "The SAP read Provider did not provide a source-completeness assertion.",
        }
    elif source_complete:
        reason = {
            "zh": "所有 SAP 只读证据来源都确认当前查询范围已完整返回。",
            "en": "Every SAP read evidence source reports source_complete=true.",
        }
    else:
        reason = {
            "zh": "至少一个 SAP 只读证据来源存在数量限制或数据范围不完整。",
            "en": "At least one SAP read evidence source is bounded or incomplete.",
        }
    return {
        "rule_id": "evidence_completeness",
        "status": "complete" if source_complete else "inconclusive",
        "score": 100 if source_complete else 0,
        "reason": reason,
        "evidence_refs": case_ids,
        "source_complete": source_complete,
        "successful_source_count": successful,
    }


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _accounting_document_key(row: dict[str, Any]) -> tuple[str, str, str] | None:
    key = tuple(
        str(row.get(field) or "").strip()
        for field in ("CompanyCode", "FiscalYear", "AccountingDocument")
    )
    return key if all(key) else None


def _sap_date_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("/Date(") and text.endswith(")/"):
        milliseconds = text[6:-2].split("+", 1)[0].split("-", 1)[0]
        try:
            return datetime.fromtimestamp(int(milliseconds) / 1000, tz=timezone.utc).date().isoformat()
        except (ValueError, OverflowError, OSError):
            return text
    return text


def _p2p_rule_config(inputs: dict[str, Any]) -> tuple[set[str], set[str], set[str], Decimal]:
    config = inputs.get("rule_config") if isinstance(inputs.get("rule_config"), dict) else {}

    def document_types(name: str, default: set[str]) -> set[str]:
        configured = {
            str(value).strip().upper()
            for value in config.get(name) or []
            if str(value).strip()
        }
        return configured or set(default)

    tolerance = _decimal_or_none(config.get("gr_ir_amount_tolerance", "0.01"))
    if tolerance is None or tolerance < 0:
        raise ValueError("gr_ir_amount_tolerance must be a non-negative decimal")
    return (
        document_types("goods_receipt_document_types", {"WE"}),
        document_types("supplier_invoice_document_types", {"RE"}),
        document_types("payment_document_types", set(P2P_PAYMENT_DOCUMENT_TYPES)),
        tolerance,
    )


def _p2p_grir_groups(
    rows: list[dict[str, Any]],
    *,
    purchase_order: str,
    receipt_document_types: set[str],
    invoice_document_types: set[str],
    tolerance: Decimal,
) -> tuple[list[dict[str, Any]], bool]:
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    invalid_required_field = False
    for row in rows:
        document_type = str(row.get("AccountingDocumentType") or "").strip().upper()
        side = (
            "receipt"
            if document_type in receipt_document_types
            else "invoice"
            if document_type in invoice_document_types
            else ""
        )
        if not side or str(row.get("FinancialAccountType") or "").strip().upper() != "S":
            continue
        if purchase_order and str(row.get("PurchasingDocument") or "").strip() != purchase_order:
            continue
        item = str(row.get("PurchasingDocumentItem") or "").strip()
        account = str(row.get("GLAccount") or "").strip()
        currency = str(row.get("CompanyCodeCurrency") or "").strip()
        amount = _decimal_or_none(row.get("AmountInCompanyCodeCurrency"))
        if not item or not account or not currency or amount is None:
            invalid_required_field = True
            continue
        key = (purchase_order or str(row.get("PurchasingDocument") or "").strip(), item, account, currency)
        group = grouped.setdefault(
            key,
            {
                "purchase_order": key[0],
                "purchase_order_item": item,
                "gl_account": account,
                "currency": currency,
                "receipt_amount": Decimal("0"),
                "invoice_amount": Decimal("0"),
                "receipt_documents": set(),
                "invoice_documents": set(),
            },
        )
        group[f"{side}_amount"] += amount
        document_key = _accounting_document_key(row)
        if document_key:
            group[f"{side}_documents"].add("/".join(document_key[1:]))

    results: list[dict[str, Any]] = []
    for group in grouped.values():
        if not group["receipt_documents"] or not group["invoice_documents"]:
            continue
        net_amount = group["receipt_amount"] + group["invoice_amount"]
        status = "matched" if abs(net_amount) <= tolerance else "open"
        results.append(
            {
                "purchase_order": group["purchase_order"],
                "purchase_order_item": group["purchase_order_item"],
                "gl_account": group["gl_account"],
                "currency": group["currency"],
                "receipt_amount": str(group["receipt_amount"]),
                "invoice_amount": str(group["invoice_amount"]),
                "net_amount": str(net_amount),
                "status": status,
                "status_label": {
                    "zh": "已匹配" if status == "matched" else "存在差额",
                    "en": "Matched" if status == "matched" else "Difference remains",
                },
            }
        )
    results.sort(
        key=lambda item: (
            item["purchase_order"],
            item["purchase_order_item"],
            item["gl_account"],
            item["currency"],
        )
    )
    return results, invalid_required_field


def evaluate_p2p_status(inputs: dict[str, Any]) -> dict[str, Any]:
    steps = _step_results(inputs)
    order_rows = _step_rows(steps, "purchase_order")
    item_rows = _step_rows(steps, "purchase_order_items")
    material_rows = _step_rows(steps, "material_documents")
    invoice_rows = _step_rows(steps, "supplier_invoice_items")
    po_linked_accounting_rows = _step_rows(steps, "accounting_items")
    full_accounting_rows = _step_rows(steps, "full_accounting_documents")
    clearing_document_rows = _step_rows(steps, "clearing_documents")
    receipt_document_types, invoice_document_types, payment_document_types, tolerance = (
        _p2p_rule_config(inputs)
    )
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    purchase_order = str(run_input.get("purchase_order") or "").strip()

    receipt_rows = [row for row in material_rows if _movement_kind(row) == "receipt"]
    reversal_rows = [row for row in material_rows if _movement_kind(row) == "reversal"]
    active_receipts = max(0, len(receipt_rows) - len(reversal_rows))

    discovered_document_keys = {
        key for row in po_linked_accounting_rows if (key := _accounting_document_key(row))
    }
    expanded_document_keys = {
        key for row in full_accounting_rows if (key := _accounting_document_key(row))
    }
    document_expansion_complete = discovered_document_keys.issubset(expanded_document_keys)

    invoice_document_keys = {
        key
        for row in po_linked_accounting_rows
        if str(row.get("AccountingDocumentType") or "").strip().upper()
        in invoice_document_types
        and (key := _accounting_document_key(row))
    }
    vendor_rows_by_document: dict[tuple[str, str, str], list[dict[str, Any]]] = {
        key: [] for key in invoice_document_keys
    }
    for row in full_accounting_rows:
        key = _accounting_document_key(row)
        if key in vendor_rows_by_document and str(row.get("FinancialAccountType") or "").strip().upper() == "K":
            vendor_rows_by_document[key].append(row)
    vendor_coverage_complete = all(vendor_rows_by_document.values()) if invoice_document_keys else True
    vendor_rows = [row for rows in vendor_rows_by_document.values() for row in rows]
    cleared_vendor_rows = [
        row
        for row in vendor_rows
        if _is_true(row.get("IsCleared"))
        and bool(str(row.get("ClearingAccountingDocument") or "").strip())
    ]

    clearing_references: set[tuple[str, str, str]] = set()
    invalid_clearing_reference = False
    for row in cleared_vendor_rows:
        reference = (
            str(row.get("CompanyCode") or "").strip(),
            str(row.get("ClearingDocFiscalYear") or "").strip(),
            str(row.get("ClearingAccountingDocument") or "").strip(),
        )
        if all(reference) and reference[1] != "0":
            clearing_references.add(reference)
        else:
            invalid_clearing_reference = True
    returned_clearing_keys = {
        key for row in clearing_document_rows if (key := _accounting_document_key(row))
    }
    clearing_expansion_complete = (
        not invalid_clearing_reference
        and clearing_references.issubset(returned_clearing_keys)
    )

    payment_documents: list[dict[str, Any]] = []
    for reference in sorted(clearing_references):
        rows = [row for row in clearing_document_rows if _accounting_document_key(row) == reference]
        document_type = _first_non_empty(rows, "AccountingDocumentType").upper()
        payment_method = _first_non_empty(rows, "PaymentMethod")
        house_bank = _first_non_empty(rows, "HouseBank")
        house_bank_account = _first_non_empty(rows, "HouseBankAccount")
        qualifies = bool(
            rows
            and document_type in payment_document_types
            and payment_method
            and (house_bank or house_bank_account)
        )
        payment_documents.append(
            {
                "company_code": reference[0],
                "fiscal_year": reference[1],
                "accounting_document": reference[2],
                "document_type": document_type,
                "payment_method": payment_method,
                "house_bank": house_bank,
                "house_bank_account": house_bank_account,
                "qualifies": qualifies,
            }
        )

    grir_groups, invalid_grir_fields = _p2p_grir_groups(
        po_linked_accounting_rows,
        purchase_order=purchase_order,
        receipt_document_types=receipt_document_types,
        invoice_document_types=invoice_document_types,
        tolerance=tolerance,
    )
    matched_grir_groups = [group for group in grir_groups if group["status"] == "matched"]
    open_grir_groups = [group for group in grir_groups if group["status"] == "open"]

    source_complete = _source_complete(inputs)
    evidence_complete = bool(
        source_complete
        and document_expansion_complete
        and vendor_coverage_complete
        and clearing_expansion_complete
        and not invalid_grir_fields
    )

    if not source_complete or not document_expansion_complete or invalid_grir_fields:
        grir_state = "unknown"
    elif not invoice_rows:
        grir_state = "not_confirmed"
    elif not grir_groups:
        grir_state = "unknown"
    elif open_grir_groups and matched_grir_groups:
        grir_state = "partial"
    elif open_grir_groups:
        grir_state = "not_confirmed"
    else:
        grir_state = "confirmed"

    if not source_complete or not document_expansion_complete or not vendor_coverage_complete:
        ap_clearing_state = "unknown"
    elif not vendor_rows:
        ap_clearing_state = "not_confirmed"
    elif len(cleared_vendor_rows) == len(vendor_rows):
        ap_clearing_state = "confirmed"
    elif cleared_vendor_rows:
        ap_clearing_state = "partial"
    else:
        ap_clearing_state = "not_confirmed"

    qualifying_payment_documents = [row for row in payment_documents if row["qualifies"]]
    if not source_complete or not clearing_expansion_complete or ap_clearing_state == "unknown":
        payment_document_state = "unknown"
    elif ap_clearing_state == "not_confirmed" or not clearing_references:
        payment_document_state = "not_confirmed"
    elif (
        ap_clearing_state == "confirmed"
        and len(qualifying_payment_documents) == len(clearing_references)
    ):
        payment_document_state = "confirmed"
    elif qualifying_payment_documents:
        payment_document_state = "partial"
    else:
        payment_document_state = "not_confirmed"

    stages = {
        "purchase_order": _presence_stage(order_rows),
        "items": _presence_stage(item_rows),
        "goods_receipt": {
            "state": "confirmed" if active_receipts > 0 else "not_confirmed",
            "receipt_evidence_count": len(receipt_rows),
            "reversal_evidence_count": len(reversal_rows),
            "net_active_receipt_count": active_receipts,
        },
        "supplier_invoice": _presence_stage(invoice_rows),
        "gr_ir_match": {
            "state": grir_state,
            "matched_group_count": len(matched_grir_groups),
            "open_group_count": len(open_grir_groups),
            "amount_tolerance": str(tolerance),
        },
        "ap_clearing": {
            "state": ap_clearing_state,
            "evidence_count": len(cleared_vendor_rows),
            "supplier_item_count": len(vendor_rows),
        },
        "payment_document": {
            "state": payment_document_state,
            "evidence_count": len(qualifying_payment_documents),
            "required_document_types": sorted(payment_document_types),
            "requires_payment_method_and_house_bank": True,
        },
        "bank_settlement": {
            "state": "not_assessed",
            "evidence_count": 0,
            "non_blocking": True,
        },
    }
    required = (
        "purchase_order",
        "items",
        "goods_receipt",
        "supplier_invoice",
        "gr_ir_match",
        "ap_clearing",
        "payment_document",
    )
    business_complete = evidence_complete and all(
        stages[name]["state"] == "confirmed" for name in required
    )
    business_status = "complete" if business_complete else "partial"
    counts = {
        "purchase_orders": len(order_rows),
        "purchase_order_items": len(item_rows),
        "material_document_items": len(material_rows),
        "supplier_invoice_items": len(invoice_rows),
        "po_linked_accounting_items": len(po_linked_accounting_rows),
        "accounting_documents": len(discovered_document_keys),
        "full_accounting_items": len(full_accounting_rows),
        "clearing_document_items": len(clearing_document_rows),
        "gr_ir_matched_groups": len(matched_grir_groups),
        "gr_ir_open_groups": len(open_grir_groups),
        "cleared_supplier_items": len(cleared_vendor_rows),
        "payment_documents": len(qualifying_payment_documents),
        # Temporary compatibility aliases. The presentation never uses these labels.
        "accounting_items": len(full_accounting_rows),
        "cleared_items": len(cleared_vendor_rows),
        "payment_evidence": len(qualifying_payment_documents),
    }
    business_report = _p2p_business_report(
        stages=stages,
        counts=counts,
        receipt_count=len(receipt_rows),
        reversal_count=len(reversal_rows),
        active_receipt_count=active_receipts,
        grir_groups=grir_groups,
        po_linked_accounting_rows=po_linked_accounting_rows,
        full_accounting_rows=full_accounting_rows,
        vendor_rows=vendor_rows,
        payment_documents=payment_documents,
        evidence_complete=evidence_complete,
    )
    business_report["records"] = [
        {
            "purchase_order": str(row.get("PurchaseOrder") or purchase_order),
            "purchase_order_item": str(row.get("PurchaseOrderItem") or ""),
            "material": str(row.get("Material") or ""),
            "plant": str(row.get("Plant") or ""),
            "order_quantity": str(row.get("OrderQuantity") or ""),
            "unit": str(row.get("PurchaseOrderQuantityUnit") or ""),
            "business_status": business_status,
            "gr_ir_match_status": grir_state,
            "ap_clearing_status": ap_clearing_state,
            "payment_document_status": payment_document_state,
            "bank_settlement_status": "not_assessed",
        }
        for row in item_rows
        if str(row.get("PurchaseOrder") or purchase_order).strip()
        and str(row.get("PurchaseOrderItem") or "").strip()
    ]
    business_report["record_columns"] = [
        {"key": "purchase_order", "label": {"zh": "采购订单", "en": "Purchase order"}},
        {"key": "purchase_order_item", "label": {"zh": "项目", "en": "Item"}},
        {"key": "material", "label": {"zh": "物料", "en": "Material"}},
        {"key": "plant", "label": {"zh": "工厂", "en": "Plant"}},
        {"key": "order_quantity", "label": {"zh": "订单数量", "en": "Order quantity"}, "format": "decimal"},
        {"key": "unit", "label": {"zh": "单位", "en": "Unit"}},
        {"key": "gr_ir_match_status", "label": {"zh": "GR/IR", "en": "GR/IR"}, "format": "status"},
        {"key": "ap_clearing_status", "label": {"zh": "应付清账", "en": "AP clearing"}, "format": "status"},
        {"key": "payment_document_status", "label": {"zh": "SAP付款凭证", "en": "SAP payment document"}, "format": "status"},
    ]
    metric_labels = {
        "purchase_orders": {"zh": "采购订单", "en": "Purchase orders"},
        "purchase_order_items": {"zh": "采购订单项目", "en": "Purchase-order items"},
        "material_document_items": {"zh": "物料凭证项目", "en": "Material-document items"},
        "supplier_invoice_items": {"zh": "供应商发票项目", "en": "Supplier-invoice items"},
        "po_linked_accounting_items": {"zh": "PO关联财务行", "en": "PO-linked accounting items"},
        "accounting_documents": {"zh": "发现的财务凭证", "en": "Discovered accounting documents"},
        "full_accounting_items": {"zh": "完整财务凭证行", "en": "Full accounting-document items"},
        "clearing_document_items": {"zh": "清账凭证行", "en": "Clearing-document items"},
        "gr_ir_matched_groups": {"zh": "GR/IR已匹配组", "en": "Matched GR/IR groups"},
        "gr_ir_open_groups": {"zh": "GR/IR差异组", "en": "Open GR/IR groups"},
        "cleared_supplier_items": {"zh": "已清账供应商行", "en": "Cleared supplier items"},
        "payment_documents": {"zh": "合格SAP付款凭证", "en": "Qualifying SAP payment documents"},
    }
    business_report["metrics"] = [
        {"id": key, "label": metric_labels[key], "value": counts[key]}
        for key in metric_labels
    ] + [
        {"id": "active_receipts", "label": {"zh": "当前有效收货记录", "en": "Active receipt records"}, "value": active_receipts},
    ]
    company_code = _first_non_empty(order_rows + full_accounting_rows, "CompanyCode")
    supplier = _first_non_empty(order_rows + full_accounting_rows, "Supplier")
    return {
        "rule_id": "p2p_deterministic_status_v2",
        "status": "complete" if source_complete and business_complete else "inconclusive",
        "business_status": business_status,
        "score": round(
            100
            * sum(stages[name]["state"] == "confirmed" for name in required)
            / len(required)
        ),
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "stages": stages,
        "counts": counts,
        "business_report": business_report,
        "reason": business_report["overview"],
        "summary": business_report["summary"],
        "evidence_refs": _case_ids(inputs),
        "workflow_output": {
            "purchase_order": str(run_input.get("purchase_order") or ""),
            "company_code": company_code,
            "supplier": supplier,
            "business_status": business_status,
            "source_complete": source_complete,
            "evidence_complete": evidence_complete,
            "gr_ir_match_status": grir_state,
            "ap_clearing_status": ap_clearing_state,
            "payment_document_status": payment_document_state,
            "bank_settlement_status": "not_assessed",
            "business_report": business_report,
        },
    }


def evaluate_o2c_status(inputs: dict[str, Any]) -> dict[str, Any]:
    steps = _step_results(inputs)
    order_rows = _step_rows(steps, "sales_order")
    item_rows = _step_rows(steps, "sales_order_items")
    delivery_rows = _step_rows(steps, "delivery_items")
    delivery_header_rows = _step_rows(steps, "delivery_headers")
    billing_rows = _rows_for_prefixes(steps, ("billing_items",))
    billing_header_rows = _rows_for_prefixes(steps, ("billing_headers",))
    accounting_rows = _rows_for_prefixes(
        steps,
        ("accounting_items", "accounting_by_", "clearing_documents"),
    )
    document_flow_rows = [
        dict(row)
        for group in _collect_values(inputs.get("document_flow"), "rows")
        if isinstance(group, list)
        for row in group
        if isinstance(row, dict)
    ]

    # A delivery item is the business grain used for the PGI metric.  Header
    # status is useful context but must not be added as a second PGI record.
    pgi_rows = [
        row
        for row in delivery_rows
        if _completed_status(row.get("GoodsMovementStatus"))
    ]
    cleared_rows = [
        row
        for row in accounting_rows
        if _is_true(row.get("IsCleared"))
        and bool(str(row.get("ClearingAccountingDocument") or "").strip())
    ]
    bank_rows = [
        row
        for row in accounting_rows
        if str(row.get("AccountingDocumentType") or "").upper()
        in O2C_CUSTOMER_PAYMENT_DOCUMENT_TYPES
        and bool(
            str(row.get("HouseBank") or row.get("HouseBankAccount") or "").strip()
        )
    ]
    stages = {
        "sales_order": _presence_stage(order_rows),
        "items": _presence_stage(item_rows),
        "delivery": _presence_stage(delivery_rows),
        "pgi": {"state": "confirmed" if pgi_rows else "not_confirmed", "evidence_count": len(pgi_rows)},
        "billing": {
            "state": "confirmed" if billing_rows else "not_confirmed",
            "item_evidence_count": len(billing_rows),
            "header_evidence_count": len(billing_header_rows),
        },
        "ar_clearing": {
            "state": "confirmed" if cleared_rows else "not_confirmed",
            "evidence_count": len(cleared_rows),
        },
        "bank_receipt": {
            "state": "confirmed" if bank_rows else "unknown",
            "evidence_count": len(bank_rows),
            "required_document_types": sorted(O2C_CUSTOMER_PAYMENT_DOCUMENT_TYPES),
            "requires_house_bank": True,
        },
        "document_flow_fallback": {
            "state": "confirmed" if document_flow_rows else "not_required_or_not_found",
            "evidence_count": len(document_flow_rows),
            "supplemental_only": True,
        },
    }
    source_complete = _source_complete(inputs)
    process_stages = ("sales_order", "items", "delivery", "pgi", "billing", "ar_clearing")
    process_complete = all(stages[name]["state"] == "confirmed" for name in process_stages)
    business_status = "complete_to_ar_clearing" if process_complete else "partial"
    counts = {
        "sales_orders": len(order_rows),
        "sales_order_items": len(item_rows),
        "delivery_items": len(delivery_rows),
        "billing_items": len(billing_rows),
        "billing_headers": len(billing_header_rows),
        "accounting_items": len(accounting_rows),
        "document_flow_rows": len(document_flow_rows),
    }
    business_report = _o2c_business_report(
        stages=stages,
        counts=counts,
        pgi_count=len(pgi_rows),
        clearing_count=len(cleared_rows),
        bank_count=len(bank_rows),
    )
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    sales_order = str(run_input.get("sales_order") or "")
    business_report["records"] = [
        {
            "sales_order": str(row.get("SalesOrder") or sales_order),
            "sales_order_item": str(row.get("SalesOrderItem") or ""),
            "material": str(row.get("Material") or ""),
            "requested_quantity": str(row.get("RequestedQuantity") or ""),
            "unit": str(row.get("RequestedQuantityUnit") or row.get("OrderQuantityUnit") or ""),
            "business_status": business_status,
        }
        for row in item_rows
        if str(row.get("SalesOrder") or sales_order).strip()
        and str(row.get("SalesOrderItem") or "").strip()
    ]
    business_report["metrics"] = [
        {"id": key, "value": value} for key, value in counts.items()
    ] + [
        {"id": "pgi_items", "value": len(pgi_rows)},
        {"id": "cleared_items", "value": len(cleared_rows)},
        {"id": "bank_receipt_evidence", "value": len(bank_rows)},
    ]
    limitations: list[str] = []
    if not bank_rows:
        limitations.append("bank_settlement_not_proven")

    def _amount_total(rows: list[dict[str, Any]], field: str) -> Decimal:
        total = Decimal("0")
        for row in rows:
            raw = row.get(field)
            if raw in {None, ""}:
                continue
            try:
                total += Decimal(str(raw))
            except (InvalidOperation, ValueError):
                continue
        return total

    if (
        billing_rows
        and billing_header_rows
        and _amount_total(billing_rows, "NetAmount")
        != _amount_total(billing_header_rows, "TotalNetAmount")
    ):
        limitations.append("shared_document_amount_attribution")
    business_report["limitations"] = limitations
    company_code = _first_non_empty(accounting_rows, "CompanyCode")
    customer = _first_non_empty(accounting_rows, "Customer") or _first_non_empty(
        order_rows, "SoldToParty", "Customer"
    )
    return {
        "rule_id": "o2c_deterministic_status_v1",
        "status": "complete" if source_complete and process_complete else "inconclusive",
        "business_status": business_status,
        "score": round(
            100
            * sum(stages[name]["state"] == "confirmed" for name in process_stages)
            / len(process_stages)
        ),
        "source_complete": source_complete,
        "stages": stages,
        "counts": counts,
        "business_report": business_report,
        "reason": business_report["overview"],
        "summary": business_report["summary"],
        "evidence_refs": _case_ids(inputs),
        "workflow_output": {
            "sales_order": str(run_input.get("sales_order") or ""),
            "company_code": company_code,
            "customer": customer,
            "business_status": business_status,
            "source_complete": source_complete,
            "business_report": business_report,
        },
    }


def _p2p_business_report(
    *,
    stages: dict[str, dict[str, Any]],
    counts: dict[str, int],
    receipt_count: int,
    reversal_count: int,
    active_receipt_count: int,
    grir_groups: list[dict[str, Any]],
    po_linked_accounting_rows: list[dict[str, Any]],
    full_accounting_rows: list[dict[str, Any]],
    vendor_rows: list[dict[str, Any]],
    payment_documents: list[dict[str, Any]],
    evidence_complete: bool,
) -> dict[str, Any]:
    process_order = (
        "purchase_order",
        "items",
        "goods_receipt",
        "supplier_invoice",
        "gr_ir_match",
        "ap_clearing",
        "payment_document",
    )
    first_gap = next(
        (stage_id for stage_id in process_order if stages[stage_id]["state"] != "confirmed"),
        None,
    )
    headlines = {
        None: {
            "zh": "SAP付款流程已完成，银行实际扣款未单独核验",
            "en": "The SAP payment process is complete; the actual bank debit was not independently verified",
        },
        "purchase_order": {
            "zh": "未找到这张采购订单",
            "en": "The purchase order was not found",
        },
        "items": {
            "zh": "已找到采购订单，但未找到订单项目",
            "en": "The purchase order was found, but no items were returned",
        },
        "goods_receipt": {
            "zh": "采购订单已创建，尚未找到有效收货记录",
            "en": "The purchase order exists, but no active goods receipt was found",
        },
        "supplier_invoice": {
            "zh": "已找到采购订单和收货记录，尚未找到供应商发票",
            "en": "The purchase order and receipt were found, but no supplier invoice was found",
        },
        "gr_ir_match": {
            "zh": "收货和发票已找到，但GR/IR匹配尚未完整确认",
            "en": "Receipt and invoice evidence was found, but GR/IR matching is not fully confirmed",
        },
        "ap_clearing": {
            "zh": "GR/IR已核对，供应商应付清账尚未完整确认",
            "en": "GR/IR was checked, but supplier-payable clearing is not fully confirmed",
        },
        "payment_document": {
            "zh": "供应商应付已清账，尚未确认合格的SAP付款凭证",
            "en": "Supplier payables were cleared, but a qualifying SAP payment document was not confirmed",
        },
    }
    overviews = {
        None: {
            "zh": "系统已分别核验GR/IR匹配、供应商应付清账和SAP付款凭证。付款凭证不等同于银行流水，因此银行实际扣款仍显示为未单独核验。",
            "en": "GR/IR matching, supplier-payable clearing and the SAP payment document were verified separately. A payment document is not a bank statement, so the actual bank debit remains independently unverified.",
        },
        "purchase_order": {
            "zh": "当前查询范围内没有返回该采购订单，请先核对订单号和访问权限。",
            "en": "The order was not returned in the current query scope. Check the order number and access rights.",
        },
        "items": {
            "zh": "订单抬头已经返回，但没有可用于继续核验的采购订单项目。",
            "en": "The order header was returned, but no purchase-order items were available for downstream checks.",
        },
        "goods_receipt": {
            "zh": "系统没有找到未被冲销的收货记录，因此不能确认货物已经入库。",
            "en": "No non-reversed goods-receipt record was found, so receipt cannot be confirmed.",
        },
        "supplier_invoice": {
            "zh": "系统确认订单已产生收货记录，但没有找到引用该订单的供应商发票，因此无法继续确认清账和付款。",
            "en": "Receipt evidence exists, but no supplier invoice referencing the order was found, so clearing and payment cannot be confirmed.",
        },
        "gr_ir_match": {
            "zh": "系统不会使用IsCleared判断GR/IR；当前按采购订单项目、总账科目和本位币汇总收货与发票金额，但存在差额或证据字段不足。",
            "en": "IsCleared is not used for GR/IR. Receipt and invoice amounts were grouped by PO item, G/L account and company-code currency, but a difference or an evidence-field gap remains.",
        },
        "ap_clearing": {
            "zh": "完整发票凭证已经展开，但并非所有供应商行都具有有效清账状态和清账凭证引用。",
            "en": "Full invoice documents were expanded, but not every supplier item has a valid cleared status and clearing-document reference.",
        },
        "payment_document": {
            "zh": "应付清账证据已经返回，但清账凭证尚未同时满足允许的付款凭证类型、付款方式和开户行条件。",
            "en": "AP clearing evidence was returned, but the clearing document did not meet the allowed payment-document type, payment-method and house-bank requirements.",
        },
    }
    stage_rows = [
        _business_stage(
            "purchase_order",
            "采购订单",
            "Purchase order",
            stages["purchase_order"]["state"],
            f"找到 {counts['purchase_orders']} 张采购订单。" if counts["purchase_orders"] else "未找到采购订单。",
            f"Found {counts['purchase_orders']} purchase order(s)." if counts["purchase_orders"] else "No purchase order was found.",
        ),
        _business_stage(
            "items",
            "订单项目",
            "Order items",
            stages["items"]["state"],
            f"找到 {counts['purchase_order_items']} 个采购订单项目。" if counts["purchase_order_items"] else "未找到采购订单项目。",
            f"Found {counts['purchase_order_items']} purchase-order item(s)." if counts["purchase_order_items"] else "No purchase-order items were found.",
        ),
        _business_stage(
            "goods_receipt",
            "收货",
            "Goods receipt",
            stages["goods_receipt"]["state"],
            (
                f"找到 {active_receipt_count} 条当前有效的收货记录，另有 {reversal_count} 条冲销记录。"
                "这里表示存在收货记录，不代表采购数量已经全部收齐。"
                if active_receipt_count
                else f"查询了 {receipt_count} 条收货和 {reversal_count} 条冲销记录，未确认当前有效收货。"
            ),
            (
                f"Found {active_receipt_count} active receipt record(s) and {reversal_count} reversal record(s). "
                "This confirms receipt evidence exists; it does not prove the full ordered quantity was received."
                if active_receipt_count
                else f"Checked {receipt_count} receipt and {reversal_count} reversal record(s); no active receipt was confirmed."
            ),
        ),
        _business_stage(
            "supplier_invoice",
            "供应商发票",
            "Supplier invoice",
            stages["supplier_invoice"]["state"],
            f"找到 {counts['supplier_invoice_items']} 条关联发票项目。" if counts["supplier_invoice_items"] else "未找到引用该采购订单的供应商发票项目。",
            f"Found {counts['supplier_invoice_items']} linked supplier-invoice item(s)." if counts["supplier_invoice_items"] else "No supplier-invoice item referencing the purchase order was found.",
        ),
        _business_stage(
            "gr_ir_match",
            "GR/IR匹配",
            "GR/IR matching",
            stages["gr_ir_match"]["state"],
            (
                f"发现 {counts['po_linked_accounting_items']} 条直接关联采购订单的财务行，"
                f"展开为 {counts['accounting_documents']} 张财务凭证、共 {counts['full_accounting_items']} 个完整行项目；"
                f"其中 {counts['gr_ir_matched_groups']} 组GR/IR已匹配，{counts['gr_ir_open_groups']} 组仍有差额。"
            ),
            (
                f"Discovered {counts['po_linked_accounting_items']} PO-linked accounting item(s) and expanded "
                f"{counts['accounting_documents']} accounting document(s) to {counts['full_accounting_items']} full item(s); "
                f"{counts['gr_ir_matched_groups']} GR/IR group(s) matched and {counts['gr_ir_open_groups']} remain open."
            ),
        ),
        _business_stage(
            "ap_clearing",
            "应付账款清账",
            "AP clearing",
            stages["ap_clearing"]["state"],
            (
                f"{len(vendor_rows)} 个供应商行中有 {counts['cleared_supplier_items']} 个已取得有效清账凭证引用。"
            ),
            (
                f"{counts['cleared_supplier_items']} of {len(vendor_rows)} supplier item(s) have a valid clearing-document reference."
            ),
        ),
        _business_stage(
            "payment_document",
            "SAP付款凭证",
            "SAP payment document",
            stages["payment_document"]["state"],
            (
                f"找到 {counts['payment_documents']} 张同时具备允许凭证类型、付款方式和开户行信息的SAP付款凭证。"
                if counts["payment_documents"]
                else "未找到同时具备允许凭证类型、付款方式和开户行信息的SAP付款凭证；清账编号本身不等同于付款。"
            ),
            (
                f"Found {counts['payment_documents']} SAP payment document(s) with an allowed type, payment method and house-bank information."
                if counts["payment_documents"]
                else "No SAP payment document had an allowed type, payment method and house-bank information; a clearing reference alone is not payment."
            ),
        ),
        _business_stage(
            "bank_settlement",
            "银行实际扣款",
            "Actual bank debit",
            "not_assessed",
            "当前数据源不包含银行流水或银行对账结果，因此未单独核验实际扣款；这不阻止SAP付款流程显示完成。",
            "The current sources do not include bank statements or reconciliation, so the actual debit was not independently verified; this does not block SAP payment-process completion.",
        ),
    ]
    actions_zh: list[str] = []
    actions_en: list[str] = []
    if first_gap in {"purchase_order", "items"}:
        actions_zh.append("核对采购订单号、订单是否已保存，以及当前用户是否有权读取该采购组织的数据。")
        actions_en.append("Check the purchase-order number, whether the order was saved, and access to the purchasing-organization data.")
    elif first_gap == "goods_receipt":
        actions_zh.append("确认仓库是否已经过账收货；如已收货，核对物料凭证是否引用该采购订单项目。")
        actions_en.append("Confirm whether goods receipt was posted and whether the material document references the purchase-order item.")
    elif first_gap == "supplier_invoice":
        actions_zh.extend(
            [
                "确认供应商发票是否已通过 MIRO 正式过账，而不是仍处于暂存或待处理状态。",
                "如业务上已经收到发票，核对发票项目是否正确引用这张采购订单。",
            ]
        )
        actions_en.extend(
            [
                "Confirm whether the supplier invoice was posted in MIRO rather than remaining parked or pending.",
                "If the invoice is expected, verify that its item correctly references this purchase order.",
            ]
        )
    elif first_gap == "gr_ir_match":
        actions_zh.append("按采购订单项目核对收货和发票对应的GR/IR科目及本位币差额。")
        actions_en.append("Review receipt and invoice GR/IR accounts and company-code-currency differences by PO item.")
    elif first_gap == "ap_clearing":
        actions_zh.append("请应付会计检查供应商未清项、付款冻结、到期日以及付款运行状态。")
        actions_en.append("Ask Accounts Payable to review the supplier open item, payment block, due date, and payment-run status.")
    elif first_gap == "payment_document":
        actions_zh.append("核对付款运行是否生成了允许的付款凭证类型，并确认付款方式和开户行信息已经写入凭证。")
        actions_en.append("Check whether the payment run created an allowed payment document type with payment method and house-bank information.")
    else:
        actions_zh.append("如需审计，可下载业务报告和阶段明细留档。")
        actions_en.append("For audit purposes, download the business report and stage details.")
    headline = headlines[first_gap]
    overview = overviews[first_gap]

    full_rows_by_document: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    linked_rows_by_document: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in full_accounting_rows:
        if key := _accounting_document_key(row):
            full_rows_by_document.setdefault(key, []).append(row)
    for row in po_linked_accounting_rows:
        if key := _accounting_document_key(row):
            linked_rows_by_document.setdefault(key, []).append(row)
    accounting_document_rows = []
    for key in sorted(set(full_rows_by_document) | set(linked_rows_by_document)):
        full_rows = full_rows_by_document.get(key, [])
        linked_rows = linked_rows_by_document.get(key, [])
        accounting_document_rows.append(
            {
                "company_code": key[0],
                "fiscal_year": key[1],
                "accounting_document": key[2],
                "document_type": _first_non_empty(full_rows + linked_rows, "AccountingDocumentType"),
                "po_linked_items": len(linked_rows),
                "full_items": len(full_rows),
            }
        )

    payment_by_key = {
        (row["company_code"], row["fiscal_year"], row["accounting_document"]): row
        for row in payment_documents
    }
    clearing_payment_rows = []
    for row in vendor_rows:
        clearing_key = (
            str(row.get("CompanyCode") or "").strip(),
            str(row.get("ClearingDocFiscalYear") or "").strip(),
            str(row.get("ClearingAccountingDocument") or "").strip(),
        )
        payment = payment_by_key.get(clearing_key, {})
        is_cleared = _is_true(row.get("IsCleared")) and bool(clearing_key[2])
        clearing_payment_rows.append(
            {
                "invoice_document": str(row.get("AccountingDocument") or ""),
                "invoice_item": str(row.get("AccountingDocumentItem") or ""),
                "ap_clearing_status": "confirmed" if is_cleared else "not_confirmed",
                "clearing_document": clearing_key[2],
                "clearing_fiscal_year": clearing_key[1] if clearing_key[1] != "0" else "",
                "clearing_date": _sap_date_text(row.get("ClearingDate")),
                "payment_document_type": str(payment.get("document_type") or ""),
                "payment_method": str(payment.get("payment_method") or ""),
                "house_bank": str(payment.get("house_bank") or payment.get("house_bank_account") or ""),
                "payment_document_status": "confirmed" if payment.get("qualifies") else "not_confirmed",
            }
        )

    evidence_tables = [
        {
            "id": "accounting_documents",
            "title": {"zh": "完整财务凭证", "en": "Full accounting documents"},
            "columns": [
                {"key": "company_code", "label": {"zh": "公司代码", "en": "Company code"}},
                {"key": "fiscal_year", "label": {"zh": "年度", "en": "Fiscal year"}},
                {"key": "accounting_document", "label": {"zh": "财务凭证", "en": "Accounting document"}},
                {"key": "document_type", "label": {"zh": "凭证类型", "en": "Document type"}},
                {"key": "po_linked_items", "label": {"zh": "PO关联行数", "en": "PO-linked items"}, "format": "integer"},
                {"key": "full_items", "label": {"zh": "完整行项目数", "en": "Full item count"}, "format": "integer"},
            ],
            "rows": accounting_document_rows,
        },
        {
            "id": "gr_ir_matching",
            "title": {"zh": "GR/IR收货与发票匹配", "en": "GR/IR receipt and invoice matching"},
            "columns": [
                {"key": "purchase_order_item", "label": {"zh": "PO项目", "en": "PO item"}},
                {"key": "gl_account", "label": {"zh": "GR/IR科目", "en": "GR/IR account"}},
                {"key": "receipt_amount", "label": {"zh": "收货金额", "en": "Receipt amount"}, "format": "decimal"},
                {"key": "invoice_amount", "label": {"zh": "发票金额", "en": "Invoice amount"}, "format": "decimal"},
                {"key": "net_amount", "label": {"zh": "净额", "en": "Net amount"}, "format": "decimal"},
                {"key": "currency", "label": {"zh": "币种", "en": "Currency"}},
                {"key": "status_label", "label": {"zh": "匹配状态", "en": "Matching status"}, "format": "status"},
            ],
            "rows": grir_groups,
        },
        {
            "id": "clearing_and_payment",
            "title": {"zh": "供应商清账与SAP付款凭证", "en": "Supplier clearing and SAP payment documents"},
            "columns": [
                {"key": "invoice_document", "label": {"zh": "发票财务凭证", "en": "Invoice accounting document"}},
                {"key": "invoice_item", "label": {"zh": "供应商行", "en": "Supplier item"}},
                {"key": "ap_clearing_status", "label": {"zh": "应付清账", "en": "AP clearing"}, "format": "status"},
                {"key": "clearing_document", "label": {"zh": "清账凭证", "en": "Clearing document"}},
                {"key": "clearing_fiscal_year", "label": {"zh": "清账年度", "en": "Clearing fiscal year"}},
                {"key": "clearing_date", "label": {"zh": "清账日期", "en": "Clearing date"}, "format": "date"},
                {"key": "payment_document_type", "label": {"zh": "付款凭证类型", "en": "Payment document type"}},
                {"key": "payment_method", "label": {"zh": "付款方式", "en": "Payment method"}},
                {"key": "house_bank", "label": {"zh": "开户行", "en": "House bank"}},
                {"key": "payment_document_status", "label": {"zh": "SAP付款凭证", "en": "SAP payment document"}, "format": "status"},
            ],
            "rows": clearing_payment_rows,
        },
    ]
    limitations = ["bank_settlement_not_independently_verified"]
    if not evidence_complete:
        limitations.append("accounting_document_or_clearing_reference_expansion_incomplete")
    return {
        "rule_version": "2.0.0",
        "tone": "success" if first_gap is None else "warning",
        "headline": headline,
        "overview": overview,
        "summary": {
            "zh": f"{headline['zh']}。{overview['zh']}",
            "en": f"{headline['en']}. {overview['en']}",
        },
        "stages": stage_rows,
        "evidence_tables": evidence_tables,
        "evidence_complete": evidence_complete,
        "missing_evidence": (
            [] if first_gap is None else [f"p2p_{first_gap}_evidence"]
        ),
        "limitations": limitations,
        "next_actions": {"zh": actions_zh, "en": actions_en},
    }


def _o2c_business_report(
    *,
    stages: dict[str, dict[str, Any]],
    counts: dict[str, int],
    pgi_count: int,
    clearing_count: int,
    bank_count: int,
) -> dict[str, Any]:
    process_order = ("sales_order", "items", "delivery", "pgi", "billing", "ar_clearing")
    first_gap = next(
        (stage_id for stage_id in process_order if stages[stage_id]["state"] != "confirmed"),
        None,
    )
    if first_gap is None and stages["bank_receipt"]["state"] != "confirmed":
        headline = {
            "zh": "订单已完成至应收清账，银行到账仍需单独确认",
            "en": "The order is complete through AR clearing; bank receipt still needs confirmation",
        }
        overview = {
            "zh": "销售订单、交货、发货过账、开票和应收清账均已找到证据；财务清账本身不能证明款项已经到达银行。",
            "en": "Evidence was found for order, delivery, PGI, billing, and AR clearing; FI clearing alone does not prove bank receipt.",
        }
        tone = "info"
    elif first_gap is None:
        headline = {
            "zh": "订单、交付、开票、清账和银行到账均已确认",
            "en": "Order, fulfillment, billing, clearing, and bank receipt are confirmed",
        }
        overview = {
            "zh": "系统在当前只读查询范围内找到了完整的订单到收款证据链。",
            "en": "The complete order-to-cash evidence chain was found within the current read-only query scope.",
        }
        tone = "success"
    else:
        gap_copy = {
            "sales_order": ("未找到这张销售订单", "The sales order was not found"),
            "items": ("已找到销售订单，但未找到订单项目", "The sales order was found, but no items were returned"),
            "delivery": ("销售订单已创建，尚未找到交货单", "The sales order exists, but no delivery was found"),
            "pgi": ("已找到交货单，尚未确认发货过账", "The delivery was found, but PGI is not confirmed"),
            "billing": ("交货已经发生，尚未找到开票凭证", "Delivery evidence exists, but no billing document was found"),
            "ar_clearing": ("已找到开票凭证，尚未确认应收清账", "The billing document was found, but AR clearing is not confirmed"),
        }
        zh, en = gap_copy[first_gap]
        headline = {"zh": zh, "en": en}
        overview = {
            "zh": "流程在上述阶段之后缺少可确认的业务证据，请根据阶段明细继续处理。",
            "en": "Confirmable business evidence is missing after this stage. Use the stage details to continue processing.",
        }
        tone = "warning"
    stage_rows = [
        _business_stage("sales_order", "销售订单", "Sales order", stages["sales_order"]["state"], f"找到 {counts['sales_orders']} 张销售订单。" if counts["sales_orders"] else "未找到销售订单。", f"Found {counts['sales_orders']} sales order(s)." if counts["sales_orders"] else "No sales order was found."),
        _business_stage("items", "订单项目", "Order items", stages["items"]["state"], f"找到 {counts['sales_order_items']} 个销售订单项目。" if counts["sales_order_items"] else "未找到销售订单项目。", f"Found {counts['sales_order_items']} sales-order item(s)." if counts["sales_order_items"] else "No sales-order items were found."),
        _business_stage("delivery", "交货", "Delivery", stages["delivery"]["state"], f"找到 {counts['delivery_items']} 条交货项目记录。" if counts["delivery_items"] else "未找到关联交货项目。", f"Found {counts['delivery_items']} delivery item record(s)." if counts["delivery_items"] else "No linked delivery item was found."),
        _business_stage("pgi", "发货过账", "Post goods issue", stages["pgi"]["state"], f"找到 {pgi_count} 条已完成发货过账的证据。" if pgi_count else "未找到已完成发货过账的证据。", f"Found {pgi_count} completed PGI record(s)." if pgi_count else "No completed PGI evidence was found."),
        _business_stage("billing", "开票", "Billing", stages["billing"]["state"], f"找到 {counts['billing_items']} 条开票项目和 {counts['billing_headers']} 张开票抬头。" if counts["billing_items"] else "未找到关联开票凭证。", f"Found {counts['billing_items']} billing item(s) and {counts['billing_headers']} billing header(s)." if counts["billing_items"] else "No linked billing document was found."),
        _business_stage("ar_clearing", "应收清账", "AR clearing", stages["ar_clearing"]["state"], f"找到 {clearing_count} 条应收清账证据。" if clearing_count else "未找到应收清账证据。", f"Found {clearing_count} AR-clearing record(s)." if clearing_count else "No AR-clearing evidence was found."),
        _business_stage("bank_receipt", "银行到账", "Bank receipt", stages["bank_receipt"]["state"], f"找到 {bank_count} 条带开户行信息的客户收款证据。" if bank_count else "尚未找到带开户行信息的客户收款证据；FI 清账不能单独证明银行到账。", f"Found {bank_count} customer-payment record(s) with house-bank information." if bank_count else "No customer-payment evidence with house-bank information was found; FI clearing alone does not prove bank receipt."),
    ]
    actions_zh = ["根据第一个未确认阶段，核对对应业务凭证是否已经正式过账并正确引用上游单据。"]
    actions_en = ["For the first unconfirmed stage, verify that the business document was posted and correctly references its upstream document."]
    if first_gap is None and not bank_count:
        actions_zh = ["如需确认实际到账，请继续核对银行对账单或带开户行信息的客户收款凭证。"]
        actions_en = ["To confirm actual receipt, check the bank statement or a customer-payment document containing house-bank information."]
    elif first_gap is None:
        actions_zh = ["如需审计，可下载业务报告和阶段明细留档。"]
        actions_en = ["For audit purposes, download the business report and stage details."]
    return {
        "tone": tone,
        "headline": headline,
        "overview": overview,
        "summary": {
            "zh": f"{headline['zh']}。{overview['zh']}",
            "en": f"{headline['en']}. {overview['en']}",
        },
        "stages": stage_rows,
        # An unconfirmed process stage is a business state when all planned
        # reads are complete; it is not an evidence-source failure.  Bank
        # settlement remains a separate evidentiary boundary.
        "missing_evidence": [] if bank_count else ["bank_settlement_not_proven"],
        "next_actions": {"zh": actions_zh, "en": actions_en},
    }


def _business_stage(
    stage_id: str,
    label_zh: str,
    label_en: str,
    state: str,
    detail_zh: str,
    detail_en: str,
) -> dict[str, Any]:
    state_labels = {
        "confirmed": {"zh": "已确认", "en": "Confirmed"},
        "partial": {"zh": "部分确认", "en": "Partially confirmed"},
        "not_found": {"zh": "未找到", "en": "Not found"},
        "not_confirmed": {"zh": "未确认", "en": "Not confirmed"},
        "unknown": {"zh": "尚不明确", "en": "Unknown"},
        "not_assessed": {"zh": "未单独核验", "en": "Not independently verified"},
    }
    return {
        "id": stage_id,
        "label": {"zh": label_zh, "en": label_en},
        "state": state,
        "state_label": state_labels.get(state, {"zh": state, "en": state}),
        "detail": {"zh": detail_zh, "en": detail_en},
    }


def _step_results(value: Any) -> dict[str, dict[str, Any]]:
    if isinstance(value, dict):
        step_results = value.get("step_results")
        if isinstance(step_results, dict):
            return {
                str(key): child
                for key, child in step_results.items()
                if isinstance(child, dict)
            }
        for child in value.values():
            found = _step_results(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _step_results(child)
            if found:
                return found
    return {}


def _step_rows(steps: dict[str, dict[str, Any]], step_id: str) -> list[dict[str, Any]]:
    step = steps.get(step_id) or {}
    return [dict(row) for row in (step.get("results") or []) if isinstance(row, dict)]


def _rows_from_nested_payload(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, dict):
        rows.extend(dict(row) for row in value.get("rows") or [] if isinstance(row, dict))
        for child in value.values():
            if isinstance(child, (dict, list)):
                rows.extend(_rows_from_nested_payload(child))
    elif isinstance(value, list):
        for child in value:
            rows.extend(_rows_from_nested_payload(child))
    return rows


def _rows_for_prefixes(
    steps: dict[str, dict[str, Any]], prefixes: tuple[str, ...]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for step_id, step in steps.items():
        if any(step_id.startswith(prefix) for prefix in prefixes):
            rows.extend(
                dict(row) for row in (step.get("results") or []) if isinstance(row, dict)
            )
    return rows


def _presence_stage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"state": "confirmed" if rows else "not_found", "evidence_count": len(rows)}


def _first_non_empty(rows: list[dict[str, Any]], *fields: str) -> str:
    for row in rows:
        for field in fields:
            value = str(row.get(field) or "").strip()
            if value:
                return value
    return ""


def _movement_kind(row: dict[str, Any]) -> str:
    if _is_true(row.get("GoodsMovementIsCancelled")) or bool(
        str(row.get("ReversedMaterialDocument") or "").strip()
    ):
        return "reversal"
    movement = str(row.get("GoodsMovementType") or "").strip()
    if movement in {"102", "106", "122", "124", "162"}:
        return "reversal"
    if movement in {"101", "103", "105", "107", "109", "121", "123", "161"}:
        return "receipt"
    return "other"


def _completed_status(value: Any) -> bool:
    return str(value or "").strip().upper() in {"C", "COMPLETE", "COMPLETED"}


def _is_true(value: Any) -> bool:
    return value is True or str(value or "").strip().lower() in {"true", "x", "1", "yes"}


def _source_complete(value: Any) -> bool:
    flags = _collect_source_complete(value)
    return bool(flags) and all(flags)


def _case_ids(value: Any) -> list[str]:
    return list(dict.fromkeys(str(item) for item in _collect_values(value, "case_id") if item))


def _collect_source_complete(value: Any) -> list[bool]:
    flags: list[bool] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "source_complete" and isinstance(child, bool):
                flags.append(child)
            else:
                flags.extend(_collect_source_complete(child))
    elif isinstance(value, list):
        for child in value:
            flags.extend(_collect_source_complete(child))
    return flags


def _collect_values(value: Any, target_key: str) -> list[Any]:
    values: list[Any] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == target_key:
                values.append(child)
            values.extend(_collect_values(child, target_key))
    elif isinstance(value, list):
        for child in value:
            values.extend(_collect_values(child, target_key))
    return values
