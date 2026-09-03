from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from .agent_rules import evaluate_business_agent
from .month_end import (
    evaluate_month_end_closing,
    prepare_month_end_scope,
    resolve_month_end_skill_requirements,
)


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
    if operation == "prepare_month_end_scope":
        return prepare_month_end_scope(inputs)
    if operation == "evaluate_month_end_closing":
        return evaluate_month_end_closing(inputs)
    if operation == "resolve_month_end_skill_requirements":
        return resolve_month_end_skill_requirements(inputs)
    if operation == "resolve_mrp_analysis_context":
        return resolve_mrp_analysis_context(inputs)
    if operation == "resolve_demand_forecast_context":
        return resolve_demand_forecast_context(inputs)
    if operation == "resolve_new_sales_demand_context":
        return resolve_new_sales_demand_context(inputs)
    if operation == "resolve_production_cost_scope":
        return resolve_production_cost_scope(inputs)
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
    if operation == "prepare_ap_input":
        return prepare_ap_input(inputs)
    if operation == "classify_control_object":
        return classify_control_object(inputs)
    if operation == "prepare_control_object_lookup":
        return prepare_control_object_lookup(inputs)
    if operation == "resolve_control_object_master":
        return resolve_control_object_master(inputs)
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


def prepare_ap_input(inputs: dict[str, Any]) -> dict[str, Any]:
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    inferred_mode = "p2p_evidence" if run_input.get("ap_payment_scopes") else "direct"
    mode = str(run_input.get("query_mode") or inferred_mode).strip()
    if mode not in {"direct", "p2p_evidence"}:
        raise ValueError("query_mode must be direct or p2p_evidence")
    return {
        "rule_id": "ap_input_mode_v1",
        "status": "complete",
        "query_mode": mode,
        "query_direct": mode == "direct",
    }


def resolve_mrp_analysis_context(inputs: dict[str, Any]) -> dict[str, Any]:
    """Capture the local business date once, before any MRP evidence is read."""

    run_input = inputs.get("run_input")
    if not isinstance(run_input, dict):
        raise ValueError("resolve_mrp_analysis_context requires run_input")
    return {
        "rule_id": "mrp_analysis_context_v1",
        "status": "complete",
        "analysis_date": date.today().isoformat(),
    }


def resolve_demand_forecast_context(inputs: dict[str, Any]) -> dict[str, Any]:
    """Resolve the shared horizon for a bounded multi-material planning review."""

    run_input = inputs.get("run_input")
    if not isinstance(run_input, dict):
        raise ValueError("resolve_demand_forecast_context requires run_input")

    def required_date(name: str) -> date:
        value = str(run_input.get(name) or "").strip()
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{name} must use YYYY-MM-DD") from exc

    date_from = required_date("date_from")
    date_to = required_date("date_to")
    if date_from > date_to:
        raise ValueError("date_from must not be after date_to")
    if (date_to - date_from).days > 366:
        raise ValueError("the analysis date range must not exceed 366 days")

    threshold_value = run_input.get("deviation_threshold_percent", 20)
    try:
        threshold = Decimal(str(20 if threshold_value in {None, ""} else threshold_value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("deviation_threshold_percent must be a number") from exc
    if not threshold.is_finite() or threshold < 0 or threshold > 100:
        raise ValueError("deviation_threshold_percent must be between 0 and 100")

    materials = run_input.get("materials")
    if not isinstance(materials, list) or not 1 <= len(materials) <= 50:
        raise ValueError("materials must contain between 1 and 50 items")
    normalized_materials = [str(item or "").strip().upper() for item in materials]
    if any(not material for material in normalized_materials):
        raise ValueError("materials must not contain empty items")
    if len(normalized_materials) != len(set(normalized_materials)):
        raise ValueError("materials must not contain duplicates")

    mrp_area = str(run_input.get("mrp_area") or run_input.get("plant") or "").strip()
    pir_version = str(run_input.get("pir_version") or "00").strip()
    if not re.fullmatch(r"[0-9A-Za-z]{1,2}", pir_version):
        raise ValueError("pir_version must contain one or two letters or digits")

    return {
        "rule_id": "demand_forecast_context_v3",
        "status": "complete",
        "analysis_date": date.today().isoformat(),
        "materials": normalized_materials,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        # Monthly and weekly PIR periods can begin before an arbitrary analysis
        # start date.  The deterministic rule clips them back to the user range.
        "pir_query_from": (date_from - timedelta(days=31)).isoformat(),
        "pir_version": pir_version,
        "pir_requirement_type": str(run_input.get("pir_requirement_type") or "").strip(),
        "mrp_area": mrp_area,
        "requirement_plan": str(run_input.get("requirement_plan") or "").strip(),
        "requirement_segment": str(run_input.get("requirement_segment") or "").strip(),
        "deviation_threshold_percent": format(threshold, "f"),
    }


def resolve_new_sales_demand_context(inputs: dict[str, Any]) -> dict[str, Any]:
    """Validate one bounded external-demand simulation row per material."""

    run_input = inputs.get("run_input")
    if not isinstance(run_input, dict):
        raise ValueError("resolve_new_sales_demand_context requires run_input")
    raw_items = run_input.get("demand_items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 50:
        raise ValueError("demand_items must contain between 1 and 50 items")
    horizon_value = run_input.get("horizon_days", 90)
    if isinstance(horizon_value, bool):
        raise ValueError("horizon_days must be an integer")
    try:
        horizon_days = int(str(horizon_value))
    except ValueError as exc:
        raise ValueError("horizon_days must be an integer") from exc
    if not 1 <= horizon_days <= 366:
        raise ValueError("horizon_days must be between 1 and 366")

    analysis_date = date.today()
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(f"demand_items[{index}] must be an object")
        material = str(raw.get("material") or "").strip().upper()
        if not material:
            raise ValueError(f"demand_items[{index}].material is required")
        if material in seen:
            raise ValueError("demand_items must not contain duplicate materials")
        seen.add(material)
        try:
            quantity = Decimal(str(raw.get("quantity")))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"demand_items[{index}].quantity must be a number") from exc
        if not quantity.is_finite() or quantity <= 0:
            raise ValueError(f"demand_items[{index}].quantity must be greater than zero")
        try:
            demand_date = date.fromisoformat(str(raw.get("demand_date") or "").strip())
        except ValueError as exc:
            raise ValueError(
                f"demand_items[{index}].demand_date must use YYYY-MM-DD"
            ) from exc
        if demand_date < analysis_date:
            raise ValueError(f"demand_items[{index}].demand_date must not be in the past")
        normalized.append(
            {
                "material": material,
                "quantity": format(quantity, "f"),
                "demand_date": demand_date.isoformat(),
                "unit": str(raw.get("unit") or "").strip().upper() or None,
                "horizon_end_date": (demand_date + timedelta(days=horizon_days)).isoformat(),
            }
        )

    plant = str(run_input.get("plant") or "").strip().upper()
    mrp_area = str(run_input.get("mrp_area") or plant).strip().upper()
    return {
        "rule_id": "new_sales_demand_context_v1",
        "status": "complete",
        "analysis_date": analysis_date.isoformat(),
        "plant": plant,
        "mrp_area": mrp_area,
        "horizon_days": horizon_days,
        "materials": [item["material"] for item in normalized],
        "demand_items": normalized,
        "horizon_end_date": max(item["horizon_end_date"] for item in normalized),
    }


def resolve_production_cost_scope(inputs: dict[str, Any]) -> dict[str, Any]:
    """Resolve the fixed production-cost period from user input or live postings."""

    run_input = inputs.get("run_input")
    if not isinstance(run_input, dict):
        raise ValueError("resolve_production_cost_scope requires run_input")
    fiscal_year = str(run_input.get("fiscal_year") or "").strip()
    period_value = run_input.get("period")
    period = int(period_value) if period_value not in {None, ""} else None
    if period is not None and not fiscal_year:
        raise ValueError("fiscal_year is required when period is supplied")
    if fiscal_year and not re.fullmatch(r"[0-9]{4}", fiscal_year):
        raise ValueError("fiscal_year must use YYYY")
    if period is not None and not 1 <= period <= 16:
        raise ValueError("period must be between 1 and 16")

    actual_payload = inputs.get("actual_cost")
    rows: list[dict[str, Any]] = []
    for step in _step_results(actual_payload).values():
        rows.extend(
            dict(row) for row in step.get("results") or [] if isinstance(row, dict)
        )
    observed: list[tuple[int, int]] = []
    scope_values: dict[str, set[str]] = {
        "company_code": set(),
        "controlling_area": set(),
        "currency": set(),
    }
    invalid_rows = 0
    for row in rows:
        row_year = str(row.get("FiscalYear") or "").strip()
        row_period = str(row.get("FiscalPeriod") or "").strip()
        if not row_year.isdigit() or len(row_year) != 4 or not row_period.isdigit():
            invalid_rows += 1
        else:
            parsed_period = int(row_period)
            if 1 <= parsed_period <= 16:
                observed.append((int(row_year), parsed_period))
            else:
                invalid_rows += 1
        for target, field in (
            ("company_code", "CompanyCode"),
            ("controlling_area", "ControllingArea"),
            ("currency", "CompanyCodeCurrency"),
        ):
            value = str(row.get(field) or "").strip()
            if value:
                scope_values[target].add(value)

    if fiscal_year and period is not None:
        period_from = period_to = f"{fiscal_year}{period:03d}"
        scope_source = "user_period"
    elif fiscal_year:
        period_from = f"{fiscal_year}001"
        period_to = f"{fiscal_year}016"
        scope_source = "user_fiscal_year"
    elif observed and _source_complete(actual_payload) and invalid_rows == 0:
        low, high = min(observed), max(observed)
        period_from = f"{low[0]:04d}{low[1]:03d}"
        period_to = f"{high[0]:04d}{high[1]:03d}"
        scope_source = "actual_cost_postings"
    else:
        period_from = period_to = ""
        scope_source = "unresolved"

    conflicts = [name for name, values in scope_values.items() if len(values) > 1]
    source_complete = _source_complete(actual_payload)
    resolved = bool(period_from and period_to and not conflicts)
    return {
        "rule_id": "production_cost_scope_v1",
        "status": "complete" if resolved else "inconclusive",
        "scope_resolved": resolved,
        "scope_source": scope_source,
        "analysis_period_from": period_from,
        "analysis_period_to": period_to,
        "company_code": next(iter(scope_values["company_code"]), ""),
        "controlling_area": next(iter(scope_values["controlling_area"]), ""),
        "currency": next(iter(scope_values["currency"]), ""),
        "actual_cost_source_complete": source_complete,
        "actual_cost_row_count": len(rows),
        "validation_issues": [
            *(
                [{"code": "actual_cost_period_invalid"}]
                if invalid_rows
                else []
            ),
            *(
                [{"code": "actual_cost_scope_conflict", "fields": conflicts}]
                if conflicts
                else []
            ),
            *(
                [{"code": "analysis_period_not_derivable"}]
                if not period_from
                else []
            ),
        ],
    }


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


def prepare_control_object_lookup(inputs: dict[str, Any]) -> dict[str, Any]:
    """Normalize one public control-object identifier into its bounded master lookup."""

    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else inputs
    object_type = str(run_input.get("object_type") or "").strip().upper()
    object_id = str(run_input.get("object_id") or "").strip()
    planning_category = str(run_input.get("planning_category") or "").strip().upper()
    if object_type not in {"INTERNAL_ORDER", "WBS"}:
        raise ValueError("prepare_control_object_lookup requires INTERNAL_ORDER or WBS")
    if not object_id:
        raise ValueError("prepare_control_object_lookup requires object_id")
    if object_type == "INTERNAL_ORDER":
        object_id = object_id.upper()
    lookup_id = object_id.zfill(12) if object_type == "INTERNAL_ORDER" and object_id.isdigit() else object_id
    return {
        "rule_id": "control_object_lookup_v3",
        "status": "complete",
        "object_type": object_type,
        "external_object_id": object_id,
        "lookup_id": lookup_id,
        "planning_category": planning_category,
        "has_planning_category": bool(planning_category),
        "discover_planning_category": not bool(planning_category),
        "is_internal_order": object_type == "INTERNAL_ORDER",
        "is_wbs": object_type == "WBS",
    }


def resolve_control_object_master(inputs: dict[str, Any]) -> dict[str, Any]:
    """Validate AUFK or resolver evidence and expose authoritative downstream keys."""

    lookup = inputs.get("lookup")
    if not isinstance(lookup, dict):
        raise ValueError("resolve_control_object_master requires lookup")
    object_type = str(lookup.get("object_type") or "").strip().upper()
    company_code = str(inputs.get("company_code") or "").strip().upper()
    wbs_resolver = inputs.get("wbs_resolver")
    using_wbs_resolver = object_type == "WBS" and isinstance(wbs_resolver, dict)
    payload = inputs.get("order_master") if object_type == "INTERNAL_ORDER" else inputs.get("wbs_master")
    rows: list[dict[str, Any]] = []
    for group in _collect_values(payload, "rows"):
        if isinstance(group, list):
            rows.extend(dict(row) for row in group if isinstance(row, dict))
    source_flags = _collect_source_complete(payload)
    source_complete = bool(source_flags) and all(source_flags)
    issues: list[str] = []
    candidates: list[dict[str, Any]] = []

    if object_type == "INTERNAL_ORDER":
        lookup_id = str(lookup.get("lookup_id") or "").strip().upper()
        for row in rows:
            order = str(row.get("AUFNR") or "").strip().upper()
            if order == lookup_id:
                candidates.append(row)
        if candidates and any(str(row.get("AUTYP") or "").strip() != "01" for row in candidates):
            issues.append("object_type_mismatch")
            candidates = []
        company_field = "BUKRS"
        internal_field = "AUFNR"
        external_id = str(lookup.get("external_object_id") or "")
        controlling_fields = ("KOKRS",)
    elif object_type == "WBS":
        external_id = str(lookup.get("external_object_id") or "").strip()
        if using_wbs_resolver:
            completeness = (
                wbs_resolver.get("completeness")
                if isinstance(wbs_resolver.get("completeness"), dict)
                else {}
            )
            source_complete = bool(
                wbs_resolver.get("status") == "complete"
                and wbs_resolver.get("resolution_status") == "resolved"
                and wbs_resolver.get("read_only") is True
                and wbs_resolver.get("validated") is True
                and completeness.get("source_complete") is True
                and completeness.get("paging_complete") is True
                and completeness.get("evidence_complete") is True
            )
            resolved = (
                wbs_resolver.get("resolved_object")
                if isinstance(wbs_resolver.get("resolved_object"), dict)
                else {}
            )
            candidates = [dict(resolved)] if resolved else []
            if resolved and str(resolved.get("object_type") or "").upper() != "WBS":
                issues.append("object_type_mismatch")
            company_field = "company_code"
            internal_field = "internal_id"
            controlling_fields = ("controlling_area",)
            if not source_complete:
                for issue in wbs_resolver.get("validation_issues") or []:
                    if isinstance(issue, dict) and issue.get("code"):
                        issues.append(str(issue["code"]))
        else:
            candidates = [
                row
                for row in rows
                if str(row.get("POSID") or "").strip() == external_id
            ]
            company_field = "PBUKR"
            internal_field = "PSPNR"
            controlling_fields = ("PKOKR", "KOKRS")
    else:
        raise ValueError("resolve_control_object_master requires INTERNAL_ORDER or WBS")

    if not source_complete:
        issues.append("master_source_incomplete")
    if len(candidates) != 1:
        issues.append("control_object_not_found" if not candidates else "control_object_ambiguous")
    row = candidates[0] if len(candidates) == 1 else {}
    observed_company = str(row.get(company_field) or row.get("BUKRS") or "").strip().upper()
    if row and (not observed_company or observed_company != company_code):
        issues.append("company_code_mismatch")
    internal_id = str(row.get(internal_field) or "").strip().upper()
    object_number = str(row.get("object_number") or row.get("OBJNR") or "").strip().upper()
    controlling_area = next(
        (str(row.get(field) or "").strip().upper() for field in controlling_fields if row.get(field)),
        "",
    )
    if row and (not internal_id or not object_number or not controlling_area):
        issues.append("master_key_incomplete")
    if object_type == "WBS" and row:
        observed_external = str(row.get("external_id") or row.get("POSID") or "").strip()
        if observed_external != external_id:
            issues.append("wbs_external_id_mismatch")
        external_id = observed_external

    ready = bool(row) and source_complete and not issues
    has_planning_category = bool(str(lookup.get("planning_category") or "").strip())
    return {
        "rule_id": "control_object_master_resolution_v3",
        "status": "complete" if ready else ("blocked" if not source_complete else "inconclusive"),
        "ready": ready,
        "source_complete": source_complete,
        "object_type": object_type,
        "is_internal_order": object_type == "INTERNAL_ORDER",
        "is_wbs": object_type == "WBS",
        "can_read_order": ready and object_type == "INTERNAL_ORDER",
        "can_read_wbs": ready and object_type == "WBS",
        "can_read_order_plan": ready and object_type == "INTERNAL_ORDER" and has_planning_category,
        "can_discover_order_plan": ready and object_type == "INTERNAL_ORDER" and not has_planning_category,
        "can_read_wbs_plan": ready and object_type == "WBS" and has_planning_category,
        "can_discover_wbs_plan": ready and object_type == "WBS" and not has_planning_category,
        "planning_category": str(lookup.get("planning_category") or "").strip().upper(),
        "external_id": external_id,
        "internal_id": internal_id,
        "object_number": object_number,
        "company_code": observed_company,
        "controlling_area": controlling_area,
        "order_category": str(row.get("AUTYP") or "").strip(),
        "order_type": str(row.get("AUART") or "").strip(),
        "project_internal_id": str(row.get("project_internal_id") or "").strip(),
        "project_external_id": str(row.get("project_external_id") or "").strip(),
        "issues": sorted(set(issues)),
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


def _p2p_item_quantity_evidence(
    order_rows: list[dict[str, Any]],
    material_rows: list[dict[str, Any]],
    invoice_rows: list[dict[str, Any]],
    invoice_header_rows: list[dict[str, Any]],
    *,
    header_step_present: bool,
) -> tuple[dict[str, dict[str, Any]], bool, bool]:
    """Calculate PO-unit receipt and invoice quantities without guessing conversions."""

    summaries: dict[str, dict[str, Any]] = {}
    for row in order_rows:
        item = str(row.get("PurchaseOrderItem") or "").strip()
        if not item:
            continue
        unit = str(row.get("PurchaseOrderQuantityUnit") or "").strip()
        summaries[item] = {
            "purchase_order_item": item,
            "order_quantity": _decimal_or_none(row.get("OrderQuantity")),
            "unit": unit,
            "net_received_quantity": Decimal("0"),
            "net_invoiced_quantity": Decimal("0"),
            "receipt_record_count": 0,
            "invoice_record_count": 0,
            "unit_conflict": False,
            "invoice_header_missing": False,
        }

    positive_movements = {"101", "103", "105", "107", "109", "121", "123", "162"}
    negative_movements = {"102", "104", "106", "108", "110", "122", "124", "161"}
    for row in material_rows:
        item = str(row.get("PurchaseOrderItem") or "").strip()
        summary = summaries.get(item)
        if summary is None:
            continue
        unit = summary["unit"]
        entry_unit = str(row.get("EntryUnit") or "").strip()
        base_unit = str(row.get("MaterialBaseUnit") or "").strip()
        quantity: Decimal | None = None
        if unit and entry_unit == unit:
            quantity = _decimal_or_none(row.get("QuantityInEntryUnit"))
        elif unit and base_unit == unit:
            quantity = _decimal_or_none(row.get("QuantityInBaseUnit"))
        if quantity is None:
            summary["unit_conflict"] = True
            continue
        movement = str(row.get("GoodsMovementType") or "").strip()
        if movement in positive_movements:
            sign = Decimal("1")
        elif movement in negative_movements:
            sign = Decimal("-1")
        else:
            debit_credit = str(row.get("DebitCreditCode") or "").strip().upper()
            if debit_credit in {"S", "D"}:
                sign = Decimal("1")
            elif debit_credit in {"H", "C"}:
                sign = Decimal("-1")
            else:
                continue
        summary["net_received_quantity"] += abs(quantity) * sign
        summary["receipt_record_count"] += 1

    headers = {
        (
            str(row.get("FiscalYear") or "").strip(),
            str(row.get("SupplierInvoice") or "").strip(),
        ): row
        for row in invoice_header_rows
        if row.get("FiscalYear") and row.get("SupplierInvoice")
    }
    for row in invoice_rows:
        item = str(row.get("PurchaseOrderItem") or "").strip()
        summary = summaries.get(item)
        if summary is None:
            continue
        key = (
            str(row.get("FiscalYear") or "").strip(),
            str(row.get("SupplierInvoice") or "").strip(),
        )
        header = headers.get(key)
        if header_step_present and header is None:
            summary["invoice_header_missing"] = True
            continue
        if header is not None:
            status = str(header.get("SupplierInvoiceStatus") or "").strip().upper()
            posted = status in {"5", "P", "POSTED", "POSTED_SUCCESSFULLY"}
            reversed_invoice = (
                _is_true(header.get("IsReversal"))
                or _is_true(header.get("IsReversed"))
                or bool(str(header.get("ReverseDocument") or "").strip())
            )
            if not posted or reversed_invoice:
                continue
        unit = summary["unit"]
        invoice_unit = str(row.get("PurchaseOrderQuantityUnit") or "").strip()
        quantity = _decimal_or_none(row.get("QuantityInPurchaseOrderUnit"))
        if quantity is None or not unit or invoice_unit != unit:
            summary["unit_conflict"] = True
            continue
        credit_memo = bool(header and _is_true(header.get("SupplierInvoiceIsCreditMemo")))
        subsequent = str(row.get("IsSubsequentDebitCredit") or "").strip().upper()
        if subsequent in {"H", "C", "CREDIT", "-"}:
            credit_memo = True
        summary["net_invoiced_quantity"] += abs(quantity) * (
            Decimal("-1") if credit_memo else Decimal("1")
        )
        summary["invoice_record_count"] += 1

    unit_conflict = any(bool(item["unit_conflict"]) for item in summaries.values())
    invoice_header_gap = any(bool(item["invoice_header_missing"]) for item in summaries.values())
    return summaries, unit_conflict, invoice_header_gap


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
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    requested = run_input.get("purchase_orders")
    if not isinstance(requested, list):
        # Internal compatibility for existing single-PO rule fixtures. The public
        # Agent contract no longer accepts purchase_order after v0.3.0.
        return _evaluate_single_p2p_status(inputs)
    purchase_orders = [str(value).strip() for value in requested]
    steps = _step_results(inputs)
    per_po: list[dict[str, Any]] = []
    scope_inputs: list[dict[str, Any]] = []
    for purchase_order in purchase_orders:
        scoped_steps = _scope_p2p_steps(steps, purchase_order)
        scoped_input = {
            **inputs,
            "run_input": {**run_input, "purchase_order": purchase_order},
            "sap_read": {
                "case_id": next(iter(_case_ids(inputs)), ""),
                "source_complete": _source_complete(inputs),
                "step_results": scoped_steps,
            },
        }
        result = _evaluate_single_p2p_status(scoped_input)
        workflow_output = result["workflow_output"]
        batch_status = _p2p_batch_status(result)
        po_result = {
            "purchase_order": purchase_order,
            "company_code": str(workflow_output.get("company_code") or ""),
            "supplier": str(workflow_output.get("supplier") or ""),
            "business_status": batch_status,
            "source_complete": bool(result.get("source_complete")),
            "evidence_complete": bool(result.get("evidence_complete")),
            "gr_ir_match_status": str(workflow_output.get("gr_ir_match_status") or "unknown"),
            "ap_clearing_status": str(workflow_output.get("ap_clearing_status") or "unknown"),
            "payment_document_status": str(workflow_output.get("payment_document_status") or "unknown"),
            "bank_settlement_status": "not_assessed",
            "business_report": workflow_output.get("business_report") or {},
        }
        per_po.append(po_result)
        scope_inputs.extend(
            _p2p_ap_scope_items(
                purchase_order=purchase_order,
                result=result,
                steps=scoped_steps,
                all_steps=steps,
                company_code=po_result["company_code"],
                supplier=po_result["supplier"],
            )
        )

    status_precedence = {
        "complete": 0,
        "in_progress": 1,
        "blocked": 2,
        "not_found": 3,
        "inconclusive": 4,
    }
    business_status = max(
        (item["business_status"] for item in per_po),
        key=lambda value: status_precedence.get(value, 4),
        default="inconclusive",
    )
    source_complete = bool(per_po) and all(item["source_complete"] for item in per_po)
    evidence_complete = bool(per_po) and all(item["evidence_complete"] for item in per_po)
    company_codes = list(
        dict.fromkeys(item["company_code"] for item in per_po if item["company_code"])
    )
    suppliers = list(dict.fromkeys(item["supplier"] for item in per_po if item["supplier"]))
    ap_payment_scopes = _group_p2p_ap_scopes(scope_inputs, per_po)
    incomplete_scope_orders = {
        po
        for scope in ap_payment_scopes
        if not scope.get("evidence_complete")
        for po in scope.get("purchase_orders") or []
    }
    if incomplete_scope_orders:
        for item in per_po:
            if item["purchase_order"] in incomplete_scope_orders:
                item["evidence_complete"] = False
                item["business_status"] = "inconclusive"
        evidence_complete = all(item["evidence_complete"] for item in per_po)
        business_status = max(
            (item["business_status"] for item in per_po),
            key=lambda value: status_precedence.get(value, 4),
            default="inconclusive",
        )
    report = _p2p_batch_report(per_po, business_status, source_complete, evidence_complete)
    return {
        "rule_id": "p2p_deterministic_status_v3",
        "status": "complete" if source_complete and evidence_complete else "inconclusive",
        "business_status": business_status,
        "score": round(
            sum(int(item.get("business_report", {}).get("score") or 0) for item in per_po)
            / len(per_po)
        ) if per_po else 0,
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "po_results": per_po,
        "ap_payment_scopes": ap_payment_scopes,
        "business_report": report,
        "reason": report["overview"],
        "summary": report["summary"],
        "evidence_refs": _case_ids(inputs),
        "workflow_output": {
            "purchase_orders": purchase_orders,
            "company_codes": company_codes,
            "suppliers": suppliers,
            "po_results": per_po,
            "ap_payment_scopes": ap_payment_scopes,
            "business_status": business_status,
            "source_complete": source_complete,
            "evidence_complete": evidence_complete,
            "business_report": report,
        },
    }


def _scope_p2p_steps(
    steps: dict[str, dict[str, Any]], purchase_order: str
) -> dict[str, dict[str, Any]]:
    direct_fields = {
        "purchase_order": "PurchaseOrder",
        "purchase_order_items": "PurchaseOrder",
        "material_documents": "PurchaseOrder",
        "supplier_invoice_items": "PurchaseOrder",
        "accounting_items": "PurchasingDocument",
    }
    scoped: dict[str, dict[str, Any]] = {}
    for step_id, payload in steps.items():
        rows = _step_rows(steps, step_id)
        field = direct_fields.get(step_id)
        selected = (
            [row for row in rows if str(row.get(field) or "").strip() == purchase_order]
            if field
            else rows
        )
        scoped[step_id] = {**payload, "results": selected}

    material_keys = {
        (
            str(row.get("MaterialDocumentYear") or "").strip(),
            str(row.get("MaterialDocument") or "").strip(),
        )
        for row in _step_rows(scoped, "material_documents")
        if row.get("MaterialDocumentYear") and row.get("MaterialDocument")
    }
    if "material_document_headers" in steps:
        scoped["material_document_headers"] = {
            **(steps.get("material_document_headers") or {}),
            "results": [
                row
                for row in _step_rows(steps, "material_document_headers")
                if (
                    str(row.get("MaterialDocumentYear") or "").strip(),
                    str(row.get("MaterialDocument") or "").strip(),
                )
                in material_keys
            ],
        }
    invoice_keys = {
        (
            str(row.get("FiscalYear") or "").strip(),
            str(row.get("SupplierInvoice") or "").strip(),
        )
        for row in _step_rows(scoped, "supplier_invoice_items")
        if row.get("FiscalYear") and row.get("SupplierInvoice")
    }
    if "supplier_invoice_headers" in steps:
        scoped["supplier_invoice_headers"] = {
            **(steps.get("supplier_invoice_headers") or {}),
            "results": [
                row
                for row in _step_rows(steps, "supplier_invoice_headers")
                if (
                    str(row.get("FiscalYear") or "").strip(),
                    str(row.get("SupplierInvoice") or "").strip(),
                )
                in invoice_keys
            ],
        }

    linked_keys = {
        key
        for row in _step_rows(scoped, "accounting_items")
        if (key := _accounting_document_key(row))
    }
    full_rows = [
        row
        for row in _step_rows(steps, "full_accounting_documents")
        if _accounting_document_key(row) in linked_keys
    ]
    scoped["full_accounting_documents"] = {
        **(steps.get("full_accounting_documents") or {}),
        "results": full_rows,
    }
    clearing_keys = {
        (
            str(row.get("CompanyCode") or "").strip(),
            str(row.get("ClearingDocFiscalYear") or "").strip(),
            str(row.get("ClearingAccountingDocument") or "").strip(),
        )
        for row in full_rows
        if str(row.get("ClearingAccountingDocument") or "").strip()
    }
    scoped["clearing_documents"] = {
        **(steps.get("clearing_documents") or {}),
        "results": [
            row
            for row in _step_rows(steps, "clearing_documents")
            if _accounting_document_key(row) in clearing_keys
        ],
    }
    return scoped


def _p2p_batch_status(result: dict[str, Any]) -> str:
    if not result.get("source_complete") or not result.get("evidence_complete"):
        return "inconclusive"
    stages = result.get("stages") if isinstance(result.get("stages"), dict) else {}
    if (stages.get("purchase_order") or {}).get("state") != "confirmed":
        return "not_found"
    if result.get("business_status") == "complete":
        return "complete"
    if (stages.get("gr_ir_match") or {}).get("state") in {"not_confirmed", "partial"}:
        return "blocked"
    vendor_rows = (stages.get("ap_clearing") or {}).get("supplier_item_count") or 0
    if vendor_rows and (stages.get("ap_clearing") or {}).get("state") in {"not_confirmed", "partial"}:
        return "blocked"
    return "in_progress"


def _p2p_ap_scope_items(
    *,
    purchase_order: str,
    result: dict[str, Any],
    steps: dict[str, dict[str, Any]],
    all_steps: dict[str, dict[str, Any]],
    company_code: str,
    supplier: str,
) -> list[dict[str, Any]]:
    rows = [
        row
        for row in _step_rows(steps, "full_accounting_documents")
        if str(row.get("FinancialAccountType") or "").strip().upper() == "K"
    ]
    invoice_groups: dict[tuple[str, str], list[str]] = {}
    for row in _step_rows(steps, "supplier_invoice_items"):
        key = (
            str(row.get("SupplierInvoice") or "").strip(),
            str(row.get("FiscalYear") or "").strip(),
        )
        item = str(row.get("SupplierInvoiceItem") or "").strip()
        if all(key):
            invoice_groups.setdefault(key, [])
            if item and item not in invoice_groups[key]:
                invoice_groups[key].append(item)
    supplier_invoice_keys = [
        {
            "supplier_invoice": key[0],
            "fiscal_year": key[1],
            "supplier_invoice_items": values,
        }
        for key, values in sorted(invoice_groups.items())
    ]
    clearing_rows = _step_rows(steps, "clearing_documents")
    all_linked_rows = _step_rows(all_steps, "accounting_items")
    allowed_payment_types = set(
        (result.get("stages", {}).get("payment_document", {}) or {}).get(
            "required_document_types", []
        )
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        document_key = _accounting_document_key(row)
        linked_document_rows = [
            candidate
            for candidate in all_linked_rows
            if _accounting_document_key(candidate) == document_key
            and str(candidate.get("PurchasingDocument") or "").strip()
        ]
        linked_purchase_orders = {
            str(candidate.get("PurchasingDocument") or "").strip()
            for candidate in linked_document_rows
        }
        allocation_ratio = Decimal("1")
        attribution_status = "direct"
        if len(linked_purchase_orders) > 1:
            currencies = {
                str(
                    candidate.get("TransactionCurrency")
                    or candidate.get("CompanyCodeCurrency")
                    or ""
                ).strip()
                for candidate in linked_document_rows
            }
            amounts_by_po: dict[str, Decimal] = {}
            valid_amounts = True
            for candidate in linked_document_rows:
                amount = _decimal_or_none(
                    candidate.get("AmountInTransactionCurrency")
                    or candidate.get("AmountInCompanyCodeCurrency")
                )
                if amount is None:
                    valid_amounts = False
                    break
                po = str(candidate.get("PurchasingDocument") or "").strip()
                amounts_by_po[po] = amounts_by_po.get(po, Decimal("0")) + abs(amount)
            total = sum(amounts_by_po.values(), Decimal("0"))
            if valid_amounts and len(currencies - {""}) == 1 and total > 0:
                allocation_ratio = amounts_by_po.get(purchase_order, Decimal("0")) / total
                attribution_status = "allocated_by_invoice_fi_amount"
            else:
                allocation_ratio = Decimal("0")
                attribution_status = "unknown_shared_document"
        clearing_key = (
            str(row.get("CompanyCode") or company_code).strip(),
            str(row.get("ClearingDocFiscalYear") or "").strip(),
            str(row.get("ClearingAccountingDocument") or "").strip(),
        )
        matched_clearing_rows = [
            candidate
            for candidate in clearing_rows
            if _accounting_document_key(candidate) == clearing_key
        ] if all(clearing_key) else []
        clearing_document_type = _first_non_empty(
            matched_clearing_rows, "AccountingDocumentType"
        ).upper()
        payment_document_status = (
            "confirmed"
            if clearing_document_type in allowed_payment_types
            else "unknown"
            if clearing_key[2] and not matched_clearing_rows
            else "not_confirmed"
        )
        source_amount = _decimal_or_none(
            row.get("AmountInTransactionCurrency")
            or row.get("AmountInCompanyCodeCurrency")
        )
        allocated_amount = (
            source_amount * allocation_ratio
            if source_amount is not None and attribution_status != "unknown_shared_document"
            else None
        )
        items.append(
            {
                "purchase_order": purchase_order,
                "purchase_order_item": str(row.get("PurchasingDocumentItem") or ""),
                "company_code": str(row.get("CompanyCode") or company_code),
                "supplier": str(row.get("Supplier") or supplier),
                "fiscal_year": str(row.get("FiscalYear") or ""),
                "accounting_document": str(row.get("AccountingDocument") or ""),
                "accounting_document_item": str(row.get("AccountingDocumentItem") or ""),
                "original_reference_document": str(row.get("OriginalReferenceDocument") or ""),
                "posting_date": _sap_date_text(row.get("PostingDate")),
                "net_due_date": _sap_date_text(row.get("NetDueDate")),
                "cash_discount_due_date": _sap_date_text(row.get("CashDiscount1DueDate")),
                "payment_blocking_reason": str(row.get("PaymentBlockingReason") or ""),
                "amount": str(allocated_amount) if allocated_amount is not None else "",
                "currency": str(row.get("TransactionCurrency") or row.get("CompanyCodeCurrency") or ""),
                "amount_allocation_ratio": str(allocation_ratio),
                "amount_attribution_status": attribution_status,
                "is_cleared": _is_true(row.get("IsCleared")),
                "clearing_document": str(row.get("ClearingAccountingDocument") or ""),
                "clearing_fiscal_year": str(row.get("ClearingDocFiscalYear") or ""),
                "clearing_date": _sap_date_text(row.get("ClearingDate")),
                "payment_method": str(row.get("PaymentMethod") or ""),
                "clearing_document_type": clearing_document_type,
                "payment_document_status": payment_document_status,
                "supplier_invoice_keys": supplier_invoice_keys,
            }
        )
    if not items:
        items.append(
            {
                "purchase_order": purchase_order,
                "purchase_order_item": "",
                "company_code": company_code,
                "supplier": supplier,
                "fiscal_year": "",
                "accounting_document": "",
                "accounting_document_item": "",
                "original_reference_document": "",
                "posting_date": "",
                "net_due_date": "",
                "cash_discount_due_date": "",
                "payment_blocking_reason": "",
                "amount": "",
                "currency": "",
                "amount_allocation_ratio": "",
                "amount_attribution_status": "not_applicable",
                "is_cleared": False,
                "clearing_document": "",
                "clearing_fiscal_year": "",
                "clearing_date": "",
                "payment_method": "",
                "clearing_document_type": "",
                "payment_document_status": "not_confirmed",
                "supplier_invoice_keys": supplier_invoice_keys,
            }
        )
    for item in items:
        item["source_complete"] = bool(result.get("source_complete"))
        item["evidence_complete"] = bool(result.get("evidence_complete"))
        item["evidence_refs"] = list(result.get("evidence_refs") or [])
    return items


def _group_p2p_ap_scopes(
    items: list[dict[str, Any]], po_results: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        company_code = str(item.get("company_code") or "")
        supplier = str(item.get("supplier") or "")
        if not company_code or not supplier:
            continue
        key = (company_code, supplier)
        group = groups.setdefault(
            key,
            {
                "scope_id": f"{company_code}:{supplier}",
                "company_code": company_code,
                "supplier": supplier,
                "purchase_orders": [],
                "supplier_invoices": [],
                "fi_supplier_items": [],
                "source_complete": True,
                "evidence_complete": True,
                "payment_run_evidence_complete": False,
                "bank_master_evidence_complete": False,
                "bank_settlement_evidence_complete": False,
                "evidence_refs": [],
            },
        )
        purchase_order = str(item.get("purchase_order") or "")
        if purchase_order and purchase_order not in group["purchase_orders"]:
            group["purchase_orders"].append(purchase_order)
        for invoice in item.get("supplier_invoice_keys") or []:
            if invoice not in group["supplier_invoices"]:
                group["supplier_invoices"].append(invoice)
        if item.get("accounting_document"):
            group["fi_supplier_items"].append(
                {
                    field: value
                    for field, value in item.items()
                    if field
                    not in {
                        "source_complete",
                        "evidence_complete",
                        "evidence_refs",
                        "supplier_invoice_keys",
                    }
                }
            )
        group["source_complete"] = group["source_complete"] and bool(item.get("source_complete"))
        group["evidence_complete"] = group["evidence_complete"] and bool(item.get("evidence_complete"))
        if item.get("amount_attribution_status") == "unknown_shared_document":
            group["evidence_complete"] = False
        group["evidence_refs"] = list(
            dict.fromkeys([*group["evidence_refs"], *(item.get("evidence_refs") or [])])
        )
    # A requested PO with an identified supplier remains in the handoff even if
    # no FI vendor row exists; AP must report missing evidence rather than omit it.
    for po in po_results:
        key = (str(po.get("company_code") or ""), str(po.get("supplier") or ""))
        if not all(key):
            continue
        group = groups.get(key)
        if group and po["purchase_order"] not in group["purchase_orders"]:
            group["purchase_orders"].append(po["purchase_order"])
    return list(groups.values())


def _p2p_batch_report(
    po_results: list[dict[str, Any]],
    business_status: str,
    source_complete: bool,
    evidence_complete: bool,
) -> dict[str, Any]:
    counts = {status: 0 for status in ("complete", "in_progress", "blocked", "not_found", "inconclusive")}
    for item in po_results:
        counts[item["business_status"]] = counts.get(item["business_status"], 0) + 1
    return {
        "headline": {
            "zh": f"已逐张核验 {len(po_results)} 张采购订单，批次状态为 {business_status}",
            "en": f"Reviewed {len(po_results)} purchase order(s); batch status is {business_status}",
        },
        "overview": {
            "zh": "批次结论按最差逐单状态汇总；查询源完整性、业务证据完整性和银行扣款核验保持独立。",
            "en": "The batch verdict uses the worst per-PO status; source completeness, business-evidence completeness, and bank-debit verification remain separate.",
        },
        "summary": {
            "zh": f"完整 {counts['complete']}，处理中 {counts['in_progress']}，阻塞 {counts['blocked']}，未找到 {counts['not_found']}，无法确认 {counts['inconclusive']}。",
            "en": f"Complete {counts['complete']}; in progress {counts['in_progress']}; blocked {counts['blocked']}; not found {counts['not_found']}; inconclusive {counts['inconclusive']}.",
        },
        "tone": "warning" if business_status != "complete" else "success",
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "metrics": [
            {"id": status, "value": count}
            for status, count in counts.items()
        ],
        "next_actions": {
            "zh": [
                "按每张采购订单的首个未完成阶段核对对应业务凭证和上游引用。",
                "将按公司代码和供应商分组的 AP 证据交给应付账款复核，不重复查询 PO 到 FI 主链。",
                "SAP 清账或付款凭证不代表银行实际扣款；需要时另取付款运行、银行结算或对账证据。",
            ],
            "en": [
                "For each purchase order, review the first incomplete stage and its upstream document references.",
                "Pass the AP evidence grouped by company code and supplier to Accounts Payable without re-querying the PO-to-FI chain.",
                "SAP clearing or payment documents do not prove an actual bank debit; obtain payment-run, bank-settlement, or reconciliation evidence when required.",
            ],
        },
        "records": po_results,
        "record_columns": [
            {"key": "purchase_order", "label": {"zh": "采购订单", "en": "Purchase order"}},
            {"key": "company_code", "label": {"zh": "公司代码", "en": "Company code"}},
            {"key": "supplier", "label": {"zh": "供应商", "en": "Supplier"}},
            {"key": "business_status", "label": {"zh": "业务状态", "en": "Business status"}, "format": "status"},
            {"key": "gr_ir_match_status", "label": {"zh": "GR/IR", "en": "GR/IR"}, "format": "status"},
            {"key": "ap_clearing_status", "label": {"zh": "应付清账", "en": "AP clearing"}, "format": "status"},
            {"key": "payment_document_status", "label": {"zh": "SAP付款凭证", "en": "SAP payment document"}, "format": "status"},
            {"key": "source_complete", "label": {"zh": "查询完整", "en": "Source complete"}, "format": "boolean"},
            {"key": "evidence_complete", "label": {"zh": "证据完整", "en": "Evidence complete"}, "format": "boolean"},
        ],
    }


def _evaluate_single_p2p_status(inputs: dict[str, Any]) -> dict[str, Any]:
    steps = _step_results(inputs)
    order_rows = _step_rows(steps, "purchase_order")
    item_rows = _step_rows(steps, "purchase_order_items")
    material_rows = _step_rows(steps, "material_documents")
    invoice_rows = _step_rows(steps, "supplier_invoice_items")
    invoice_header_rows = _step_rows(steps, "supplier_invoice_headers")
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
    quantity_evidence, quantity_unit_conflict, invoice_header_gap = (
        _p2p_item_quantity_evidence(
            item_rows,
            material_rows,
            invoice_rows,
            invoice_header_rows,
            header_step_present="supplier_invoice_headers" in steps,
        )
    )
    quantified_receipt = any(
        item["net_received_quantity"] > 0 for item in quantity_evidence.values()
    )
    has_quantified_receipt_records = any(
        item["receipt_record_count"] > 0 for item in quantity_evidence.values()
    )
    quantified_invoice = any(
        item["net_invoiced_quantity"] > 0 for item in quantity_evidence.values()
    )
    has_quantified_invoice_records = any(
        item["invoice_record_count"] > 0 for item in quantity_evidence.values()
    )

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
        # SAP payment-document evidence is established by the configured
        # accounting-document type.  Payment method and house-bank fields are
        # useful operational context, but they are neither universally filled
        # nor evidence of the actual bank debit.
        qualifies = bool(rows and document_type in payment_document_types)
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
        and not quantity_unit_conflict
        and not invoice_header_gap
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
            "state": (
                "unknown"
                if quantity_unit_conflict
                else "confirmed"
                if quantified_receipt or (not has_quantified_receipt_records and active_receipts > 0)
                else "not_confirmed"
            ),
            "receipt_evidence_count": len(receipt_rows),
            "reversal_evidence_count": len(reversal_rows),
            "net_active_receipt_count": active_receipts,
        },
        "supplier_invoice": {
            "state": (
                "unknown"
                if quantity_unit_conflict or invoice_header_gap
                else "confirmed"
                if quantified_invoice or (not has_quantified_invoice_records and bool(invoice_rows))
                else "not_found"
                if not invoice_rows
                else "not_confirmed"
            ),
            "evidence_count": len(invoice_rows),
            "header_evidence_count": len(invoice_header_rows),
        },
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
            "requires_payment_method_and_house_bank": False,
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
    for record in business_report["records"]:
        quantity = quantity_evidence.get(record["purchase_order_item"])
        if not quantity:
            continue
        record.update(
            {
                "net_received_quantity": str(quantity["net_received_quantity"]),
                "net_invoiced_quantity": str(quantity["net_invoiced_quantity"]),
                "quantity_evidence_status": (
                    "inconclusive"
                    if quantity["unit_conflict"] or quantity["invoice_header_missing"]
                    else "complete"
                ),
            }
        )
    business_report["record_columns"] = [
        {"key": "purchase_order", "label": {"zh": "采购订单", "en": "Purchase order"}},
        {"key": "purchase_order_item", "label": {"zh": "项目", "en": "Item"}},
        {"key": "material", "label": {"zh": "物料", "en": "Material"}},
        {"key": "plant", "label": {"zh": "工厂", "en": "Plant"}},
        {"key": "order_quantity", "label": {"zh": "订单数量", "en": "Order quantity"}, "format": "decimal"},
        {"key": "unit", "label": {"zh": "单位", "en": "Unit"}},
        {"key": "net_received_quantity", "label": {"zh": "净收货数量", "en": "Net received quantity"}, "format": "decimal"},
        {"key": "net_invoiced_quantity", "label": {"zh": "净发票数量", "en": "Net invoiced quantity"}, "format": "decimal"},
        {"key": "quantity_evidence_status", "label": {"zh": "数量证据", "en": "Quantity evidence"}, "format": "status"},
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
            "zh": "应付清账证据已经返回，但清账凭证类型不属于配置的SAP付款凭证类型。",
            "en": "AP clearing evidence was returned, but the clearing-document type is not one of the configured SAP payment-document types.",
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
                f"找到 {counts['payment_documents']} 张允许类型的SAP付款凭证；付款方式和开户行仅作上下文，不证明银行实际扣款。"
                if counts["payment_documents"]
                else "未找到允许类型的SAP付款凭证；清账编号本身不等同于付款。"
            ),
            (
                f"Found {counts['payment_documents']} SAP payment document(s) with an allowed type; payment-method and house-bank fields are context, not proof of bank debit."
                if counts["payment_documents"]
                else "No SAP payment document had an allowed type; a clearing reference alone is not payment."
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
        actions_zh.append("核对付款运行是否生成了配置允许的SAP付款凭证类型；银行实际扣款必须另取银行或对账证据。")
        actions_en.append("Check whether the payment run created an allowed SAP payment-document type; actual debit requires separate bank or reconciliation evidence.")
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
    movement = str(row.get("GoodsMovementType") or "").strip()
    if movement in {"102", "104", "106", "108", "110", "122", "124", "161"}:
        return "reversal"
    if movement in {"101", "103", "105", "107", "109", "121", "123", "162"}:
        return "receipt"
    if _is_true(row.get("GoodsMovementIsCancelled")) or bool(
        str(row.get("ReversedMaterialDocument") or "").strip()
    ):
        return "reversal"
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
