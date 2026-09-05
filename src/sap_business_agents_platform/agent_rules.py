from __future__ import annotations

import calendar
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Callable

from .grir import evaluate_odata_grir
from .month_end import evaluate_month_end_closing


JsonObject = dict[str, Any]
SAP_V2_DATE = re.compile(r"^/Date\((-?\d+)(?:[+-]\d{4})?\)/$")


def evaluate_business_agent(inputs: JsonObject) -> JsonObject:
    """Evaluate one registered fixed Agent from GET-only provider evidence.

    The SAP plans stay in the Agent manifests.  This registry owns only normalized,
    deterministic business interpretation and never performs I/O.
    """

    agent_id = str(inputs.get("agent_id") or "").strip()
    evaluator = _EVALUATORS.get(agent_id)
    if evaluator is None:
        raise ValueError(f"No deterministic business rule is registered for {agent_id!r}")
    return evaluator(inputs)


def _all_payloads(inputs: JsonObject) -> list[JsonObject]:
    evidence = inputs.get("evidence")
    if isinstance(evidence, dict):
        return [item for item in evidence.values() if isinstance(item, dict)]
    return []


def _rows(inputs: JsonObject, *step_ids: str) -> list[JsonObject]:
    wanted = set(step_ids)
    found: list[JsonObject] = []
    evidence = inputs.get("evidence")
    entries = evidence.items() if isinstance(evidence, dict) else []
    for evidence_name, payload in entries:
        if not isinstance(payload, dict):
            continue
        data = payload.get("data")
        step_results = payload.get("step_results")
        if not isinstance(step_results, dict):
            step_results = data.get("step_results") if isinstance(data, dict) else None
        matched_step_result = False
        if isinstance(step_results, dict):
            for step_id, result in step_results.items():
                if wanted and step_id not in wanted and str(evidence_name) not in wanted:
                    continue
                if not isinstance(result, dict):
                    continue
                matched_step_result = True
                for row in result.get("results") or []:
                    if isinstance(row, dict):
                        found.append(row)
        if not matched_step_result and str(evidence_name) in wanted:
            if isinstance(data, dict):
                for row in data.get("results") or []:
                    if isinstance(row, dict):
                        found.append(row)
            for row in payload.get("results") or []:
                if isinstance(row, dict):
                    found.append(row)
    return found


def _failed_filter_values(inputs: JsonObject, *step_ids: str) -> set[str]:
    wanted = set(step_ids)
    failed: set[str] = set()

    def visit(value: Any, current_step: str = "") -> None:
        if isinstance(value, dict):
            step = str(value.get("step_id") or current_step)
            if not wanted or step in wanted:
                for item in value.get("failed_filter_values") or []:
                    if isinstance(item, list):
                        failed.update(str(child).strip() for child in item if str(child).strip())
                    elif str(item).strip():
                        failed.add(str(item).strip())
            for key, child in value.items():
                visit(child, str(key) if str(key) in wanted else step)
        elif isinstance(value, list):
            for child in value:
                visit(child, current_step)

    visit(inputs.get("evidence"))
    return failed


def _optional_rows(inputs: JsonObject, *step_ids: str) -> list[JsonObject]:
    """Read advisory context without adding it to required evidence completeness."""

    optional_context = inputs.get("optional_context")
    if not isinstance(optional_context, dict):
        return []
    return _rows({"evidence": optional_context}, *step_ids)


def _source_complete(inputs: JsonObject) -> bool:
    flags: list[bool] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("source_complete"), bool):
                flags.append(bool(value["source_complete"]))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(inputs.get("evidence"))
    return bool(flags) and all(flags)


def _step_source_complete(inputs: JsonObject, step_id: str) -> bool:
    evidence = inputs.get("evidence")
    entries = evidence.values() if isinstance(evidence, dict) else []
    for payload in entries:
        if not isinstance(payload, dict):
            continue
        step_results = payload.get("step_results")
        data = payload.get("data")
        if not isinstance(step_results, dict) and isinstance(data, dict):
            step_results = data.get("step_results")
        result = step_results.get(step_id) if isinstance(step_results, dict) else None
        if isinstance(result, dict):
            return result.get("source_complete") is True and result.get("source_truncated") is not True
    return False


def _fallback(inputs: JsonObject, topic: str) -> JsonObject:
    fallbacks = inputs.get("fallbacks")
    value = fallbacks.get(topic) if isinstance(fallbacks, dict) else None
    return value if isinstance(value, dict) else {}


def _adt_rows(inputs: JsonObject, *topics: str) -> list[JsonObject]:
    rows: list[JsonObject] = []
    for topic in topics:
        rows.extend(_rows_in_adt_payload(_fallback(inputs, topic)))
    return rows


def _rows_in_adt_payload(value: Any) -> list[JsonObject]:
    if not isinstance(value, dict):
        return []
    rows = [row for row in value.get("rows") or [] if isinstance(row, dict)]
    for child in value.values():
        if isinstance(child, dict):
            rows.extend(_rows_in_adt_payload(child))
    return rows


def _adt_complete(value: JsonObject) -> bool:
    if value.get("status") == "skipped" and value.get("reason") == "condition_false":
        return value.get("required") is False
    if "status" not in value:
        components = [child for child in value.values() if isinstance(child, dict)]
        return bool(components) and all(_adt_complete(child) for child in components)
    completeness = value.get("completeness")
    return bool(
        value.get("status") == "complete"
        and value.get("read_only") is True
        and value.get("validated") is True
        and isinstance(completeness, dict)
        and completeness.get("source_complete") is True
        and completeness.get("paging_complete") is True
        and not value.get("validation_issues")
    )


def _topic_complete(inputs: JsonObject, topic: str) -> bool:
    assessment = inputs.get("assessment")
    api = assessment.get("api_complete") if isinstance(assessment, dict) else None
    if isinstance(api, dict) and api.get(topic) is True:
        return True
    needs_adt = assessment.get("needs_adt") if isinstance(assessment, dict) else None
    if (
        isinstance(api, dict)
        and api.get(topic) is False
        and isinstance(needs_adt, dict)
        and needs_adt.get(topic) is True
    ):
        fallback = _fallback(inputs, topic)
        if fallback.get("status") == "skipped" and fallback.get("reason") == "condition_false":
            return False
    # Some deterministic manifests execute a direct, exact OData read without
    # a separate assess_api_evidence step.  In that shape the evidence topic is
    # itself authoritative for query-source completeness.  Do not require an
    # ADT fallback merely because the optional assessment envelope is absent.
    evidence = inputs.get("evidence")
    direct_payload = evidence.get(topic) if isinstance(evidence, dict) else None
    if isinstance(direct_payload, dict) and not (
        isinstance(api, dict) and api.get(topic) is False
    ):
        flags: list[bool] = []

        def collect_flags(value: Any) -> None:
            if isinstance(value, dict):
                if isinstance(value.get("source_complete"), bool):
                    flags.append(bool(value["source_complete"]))
                for child in value.values():
                    collect_flags(child)
            elif isinstance(value, list):
                for child in value:
                    collect_flags(child)

        collect_flags(direct_payload)
        if flags:
            return all(flags) and direct_payload.get("ok") is not False
    return _adt_complete(_fallback(inputs, topic))


def _required_topics(inputs: JsonObject, *topics: str) -> tuple[bool, list[str]]:
    missing = [f"{topic}_evidence" for topic in topics if not _topic_complete(inputs, topic)]
    return not missing, missing


def _gaps(inputs: JsonObject, *extra: str) -> list[str]:
    gaps = {str(item) for item in inputs.get("known_gaps") or [] if str(item)}
    gaps.update(str(item) for item in extra if str(item))
    for payload in _all_payloads(inputs):
        if payload.get("status") == "capability_blocked" or payload.get("ok") is False:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            gaps.add(str(error.get("code") or payload.get("step_id") or "evidence_unavailable"))
    return sorted(gaps)


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


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _unit_key(value: Any) -> str:
    return str(value or "").strip().upper()


def _date(value: Any) -> date | None:
    if value in {None, ""}:
        return None
    text = str(value)
    match = SAP_V2_DATE.fullmatch(text)
    if match:
        try:
            return datetime.fromtimestamp(int(match.group(1)) / 1000).date()
        except (ValueError, OSError):
            return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {
        "1", "true", "x", "yes", "c", "complete", "completed", "fully_billed"
    }


def _text(row: JsonObject, *fields: str) -> str:
    for field in fields:
        value = row.get(field)
        if value not in {None, ""}:
            return str(value)
    return ""


def _canonical_sd_key(value: Any, width: int) -> str:
    text = str(value or "").strip()
    if text.isdigit() and len(text) <= width:
        return text.zfill(width)
    return text


def _embedded_billing_incompletion_summary(row: JsonObject | None) -> str:
    if not isinstance(row, dict):
        return "not_evidenced"
    labels = (
        ("UVFAK", "billing"),
        ("UVPRS", "pricing"),
        ("UVVLK", "delivery"),
        ("UVALL", "general"),
    )
    incomplete = [
        label
        for field, label in labels
        if str(row.get(field) or "").strip().upper() not in {"", "0", "C"}
    ]
    return ",".join(f"{label}_incomplete" for label in incomplete) or "complete_or_not_relevant"


def _vbuv_incompletion_summary(rows: list[JsonObject]) -> str:
    missing_fields = sorted(
        {
            ".".join(
                part
                for part in (
                    str(row.get("TBNAM") or "").strip().upper(),
                    str(row.get("FDNAM") or "").strip().upper(),
                )
                if part
            )
            for row in rows
        }
        - {""}
    )
    return (
        "incomplete:" + ",".join(missing_fields)
        if missing_fields
        else "incomplete:unspecified_field"
        if rows
        else "complete_or_not_relevant"
    )


def _adt_hash_verified(value: JsonObject) -> bool:
    return any(
        isinstance(artifact, dict)
        and artifact.get("type") == "output_manifest"
        and artifact.get("verified") is True
        for artifact in value.get("artifacts") or []
    )


def _sap_localized_texts(
    payload: JsonObject,
    *,
    key_fields: tuple[str, ...],
    text_field: str,
    language_field: str,
) -> dict[str, JsonObject]:
    """Build localized SAP text values without inventing translations."""

    grouped: dict[str, dict[str, str]] = {}
    for row in _rows_in_adt_payload(payload):
        key = ".".join(str(row.get(field) or "").strip().upper() for field in key_fields)
        text = str(row.get(text_field) or "").strip()
        language = str(row.get(language_field) or "").strip().upper()
        if not key or not text or not language:
            continue
        grouped.setdefault(key, {})[language] = text
    resolved: dict[str, JsonObject] = {}
    for key, languages in grouped.items():
        english = languages.get("E", "")
        chinese = languages.get("1", "") or languages.get("M", "")
        fallback_language, fallback_text = next(iter(sorted(languages.items())))
        resolved[key] = {
            "zh": chinese or (f"{english} [SAP EN]" if english else f"{fallback_text} [SAP {fallback_language}]"),
            "en": english or (f"{chinese} [SAP ZH]" if chinese else f"{fallback_text} [SAP {fallback_language}]"),
        }
    return resolved


def _finding_detail(
    *,
    field_zh: str,
    field_en: str,
    value: str,
    value_text: JsonObject,
    object_id: str,
    scope: str,
) -> JsonObject:
    scope_labels = {
        "header": ("销售订单抬头", "sales-order header"),
        "item": ("销售订单项目", "sales-order item"),
        "delivery_header": ("交货抬头", "delivery header"),
        "delivery_item": ("交货项目", "delivery item"),
    }
    zh_scope, en_scope = scope_labels.get(scope, (scope, scope))
    zh_text = str(value_text.get("zh") or value_text.get("en") or "未取得SAP文本")
    en_text = str(value_text.get("en") or value_text.get("zh") or "SAP text unavailable")
    return {
        "zh": f"{zh_scope} {object_id}：{field_zh} {value} — {zh_text}",
        "en": f"{en_scope} {object_id}: {field_en} {value} — {en_text}",
    }


def _date_text(row: JsonObject, *fields: str) -> str:
    parsed = _date(_text(row, *fields))
    return parsed.isoformat() if parsed is not None else ""


def _record_columns(records: list[JsonObject]) -> list[JsonObject]:
    keys: list[str] = []
    for record in records:
        for key in record:
            if key not in keys:
                keys.append(key)
    return [
        {
            "key": key,
            "label": {
                "zh": key.replace("_", " "),
                "en": key.replace("_", " ").title(),
            },
        }
        for key in keys
    ]


def _stage(
    stage_id: str,
    zh: str,
    en: str,
    count: int,
    *,
    detail_zh: str = "",
    detail_en: str = "",
    state: str | None = None,
) -> JsonObject:
    actual_state = state or ("confirmed" if count else "not_confirmed")
    labels = {
        "confirmed": {"zh": "已取得证据", "en": "Evidence found"},
        "not_confirmed": {"zh": "未取得证据", "en": "No evidence found"},
        "attention": {"zh": "需要关注", "en": "Attention required"},
        "unknown": {"zh": "无法确认", "en": "Unknown"},
        "not_requested": {"zh": "未启用", "en": "Not enabled"},
    }
    return {
        "id": stage_id,
        "label": {"zh": zh, "en": en},
        "state": actual_state,
        "state_label": labels.get(actual_state, labels["unknown"]),
        "detail": {
            "zh": detail_zh or f"返回 {count} 条相关证据。",
            "en": detail_en or f"Returned {count} related evidence row(s).",
        },
        "evidence_count": count,
    }


def _result(
    inputs: JsonObject,
    *,
    business_status: str,
    headline_zh: str,
    headline_en: str,
    overview_zh: str,
    overview_en: str,
    stages: list[JsonObject],
    findings: list[JsonObject] | None = None,
    metrics: list[JsonObject] | None = None,
    gaps: list[str] | None = None,
    limitations: list[str] | None = None,
    actions_zh: list[str] | None = None,
    actions_en: list[str] | None = None,
    source_complete_override: bool | None = None,
    records: list[JsonObject] | None = None,
    record_columns: list[JsonObject] | None = None,
    allow_empty_records: bool = False,
    preserve_business_status_on_gap: bool = False,
) -> JsonObject:
    missing = sorted(set(gaps or []))
    source_complete = (
        _source_complete(inputs)
        if source_complete_override is None
        else source_complete_override
    )
    business_complete = source_complete and not missing
    conclusive_status = "complete" if business_complete else "inconclusive"
    tone = (
        "info"
        if missing
        else "warning"
        if business_status in {"attention", "partial", "blocked", "capability_blocked"}
        else "success"
    )
    report = {
        "tone": tone,
        "headline": {"zh": headline_zh, "en": headline_en},
        "overview": {"zh": overview_zh, "en": overview_en},
        "stages": stages,
        "findings": findings or [],
        "metrics": metrics or [],
        "missing_evidence": missing,
        "limitations": sorted(set(limitations or [])),
        "next_actions": {
            "zh": actions_zh or [],
            "en": actions_en or [],
        },
        "summary": {"zh": headline_zh, "en": headline_en},
        "records": [],
        "record_columns": record_columns or [],
    }
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    effective_status = (
        business_status
        if preserve_business_status_on_gap
        else "capability_blocked"
        if missing
        else business_status
    )
    normalized_records = [dict(item) for item in records or [] if isinstance(item, dict)]
    if not normalized_records and not allow_empty_records:
        normalized_records = [{**run_input, "business_status": effective_status}]
    else:
        for record in normalized_records:
            record.setdefault("business_status", effective_status)
    report["records"] = normalized_records
    if not report["record_columns"]:
        report["record_columns"] = _record_columns(normalized_records)
    workflow_output = {
        **{str(key): value for key, value in run_input.items()},
        "business_status": effective_status,
        "source_complete": source_complete,
        "business_report": report,
    }
    return {
        "rule_id": f"{str(inputs.get('agent_id') or '').replace('-', '_')}_deterministic_v1",
        "status": conclusive_status,
        "business_status": effective_status,
        "business_complete": business_complete,
        "source_complete": source_complete,
        "missing_evidence": missing,
        "findings": findings or [],
        "metrics": metrics or [],
        "business_report": report,
        "reason": report["overview"],
        "summary": report["summary"],
        "workflow_output": workflow_output,
    }


def _billing_block(inputs: JsonObject) -> JsonObject:
    orders = _rows(inputs, "sales_orders")
    items = _rows(inputs, "sales_order_items")
    delivery_headers = _rows(inputs, "delivery_headers")
    delivery_items = _rows(inputs, "delivery_items")
    deliveries = delivery_headers + delivery_items
    assessment = inputs.get("assessment")
    assessment = assessment if isinstance(assessment, dict) else {}
    needs_adt = bool(
        isinstance(assessment.get("needs_adt"), dict)
        and assessment["needs_adt"].get("item_incompletion") is True
    )
    fallback = _fallback(inputs, "sales_order_item_incompletion")
    embedded_status_complete = assessment.get("embedded_status_complete") is True
    status_rows = _adt_rows(inputs, "sales_order_item_incompletion") if needs_adt else []
    expected_keys = {
        (
            _canonical_sd_key(row.get("SalesOrder"), 10),
            _canonical_sd_key(row.get("SalesOrderItem"), 6),
        )
        for row in items
        if _text(row, "SalesOrder") and _text(row, "SalesOrderItem")
    }
    status_by_key: dict[tuple[str, str], list[JsonObject]] = {}
    scope_conflicts: list[JsonObject] = []
    for row in status_rows:
        key = (
            _canonical_sd_key(row.get("VBELN") or row.get("SalesOrder"), 10),
            _canonical_sd_key(row.get("POSNR") or row.get("SalesOrderItem"), 6),
        )
        if not key[0] or key[0] not in {order for order, _item in expected_keys}:
            scope_conflicts.append(row)
            continue
        # VBUV is a sparse incompletion log: an empty result is positive evidence
        # that no missing field is logged in the exact, complete order scope.
        if key[1] not in {"", "000000"} and key not in expected_keys:
            scope_conflicts.append(row)
            continue
        status_by_key.setdefault(key, []).append(row)
    status_evidence_complete = bool(
        expected_keys
        and not scope_conflicts
        and (
            embedded_status_complete
            or (
                needs_adt
                and _adt_complete(fallback)
                and _adt_hash_verified(fallback)
            )
        )
    )
    billing_text_payload = _fallback(inputs, "billing_block_code_texts")
    delivery_text_payload = _fallback(inputs, "delivery_block_code_texts")
    credit_text_payload = _fallback(inputs, "credit_status_code_texts")
    incompletion_text_payload = _fallback(inputs, "incompletion_field_texts")
    incompletion_definition_payload = _fallback(inputs, "incompletion_field_definitions")
    data_element_text_payload = _fallback(inputs, "data_element_texts")
    billing_texts = _sap_localized_texts(
        billing_text_payload,
        key_fields=("FAKSP",),
        text_field="VTEXT",
        language_field="SPRAS",
    )
    delivery_texts = _sap_localized_texts(
        delivery_text_payload,
        key_fields=("LIFSP",),
        text_field="VTEXT",
        language_field="SPRAS",
    )
    credit_texts = _sap_localized_texts(
        credit_text_payload,
        key_fields=("DOMVALUE_L",),
        text_field="DDTEXT",
        language_field="DDLANGUAGE",
    )
    incompletion_texts = _sap_localized_texts(
        incompletion_text_payload,
        key_fields=("TABNAME", "FIELDNAME"),
        text_field="DDTEXT",
        language_field="DDLANGUAGE",
    )
    data_element_texts = _sap_localized_texts(
        data_element_text_payload,
        key_fields=("ROLLNAME",),
        text_field="DDTEXT",
        language_field="DDLANGUAGE",
    )
    incompletion_text_sources = {key: "DD03T" for key in incompletion_texts}
    for row in _rows_in_adt_payload(incompletion_definition_payload):
        field_key = ".".join(
            (
                str(row.get("TABNAME") or "").strip().upper(),
                str(row.get("FIELDNAME") or "").strip().upper(),
            )
        )
        rollname = str(row.get("ROLLNAME") or "").strip().upper()
        if field_key not in incompletion_texts and rollname in data_element_texts:
            incompletion_texts[field_key] = data_element_texts[rollname]
            incompletion_text_sources[field_key] = "DD04T via DD03L"

    findings: list[JsonObject] = []
    finding_keys: set[tuple[str, str, str, str]] = set()
    required_texts: list[tuple[str, str, JsonObject, JsonObject]] = []

    def add_code_finding(
        *,
        code: str,
        value: Any,
        object_id: Any,
        scope: str,
        text_map: dict[str, JsonObject],
        field_zh: str,
        field_en: str,
        normal_values: set[str] | None = None,
        severity: str = "high",
        source: str,
    ) -> None:
        normalized = str(value or "").strip().upper()
        if not normalized or normalized in (normal_values or set()):
            return
        object_text = str(object_id or "").strip()
        key = (code, object_text, scope, normalized)
        if key in finding_keys:
            return
        finding_keys.add(key)
        value_text = text_map.get(normalized, {})
        required_texts.append((normalized, source, value_text, _fallback(inputs, source)))
        findings.append(
            {
                "code": code,
                "severity": severity,
                "value": normalized,
                "value_text": value_text,
                "object": object_text,
                "scope": scope,
                "text_source": source,
                "detail": _finding_detail(
                    field_zh=field_zh,
                    field_en=field_en,
                    value=normalized,
                    value_text=value_text,
                    object_id=object_text,
                    scope=scope,
                ),
            }
        )

    for row in orders:
        object_id = row.get("SalesOrder")
        add_code_finding(
            code="HeaderBillingBlockReason", value=row.get("HeaderBillingBlockReason"),
            object_id=object_id, scope="header", text_map=billing_texts,
            field_zh="开票冻结原因", field_en="billing block reason",
            source="billing_block_code_texts",
        )
        add_code_finding(
            code="DeliveryBlockReason", value=row.get("DeliveryBlockReason"),
            object_id=object_id, scope="header", text_map=delivery_texts,
            field_zh="交货冻结原因", field_en="delivery block reason",
            source="delivery_block_code_texts",
        )
        add_code_finding(
            code="TotalCreditCheckStatus", value=row.get("TotalCreditCheckStatus"),
            object_id=object_id, scope="header", text_map=credit_texts,
            field_zh="信用检查状态", field_en="credit-check status",
            normal_values={"C"}, severity="medium", source="credit_status_code_texts",
        )
    for row in items:
        add_code_finding(
            code="ItemBillingBlockReason", value=row.get("ItemBillingBlockReason"),
            object_id=f"{_text(row, 'SalesOrder')}/{_text(row, 'SalesOrderItem')}",
            scope="item", text_map=billing_texts,
            field_zh="项目开票冻结原因", field_en="item billing block reason",
            source="billing_block_code_texts",
        )
    for row in delivery_headers:
        object_id = row.get("DeliveryDocument")
        add_code_finding(
            code="HeaderBillingBlockReason", value=row.get("HeaderBillingBlockReason"),
            object_id=object_id, scope="delivery_header", text_map=billing_texts,
            field_zh="开票冻结原因", field_en="billing block reason",
            source="billing_block_code_texts",
        )
        add_code_finding(
            code="DeliveryBlockReason", value=row.get("DeliveryBlockReason"),
            object_id=object_id, scope="delivery_header", text_map=delivery_texts,
            field_zh="交货冻结原因", field_en="delivery block reason",
            source="delivery_block_code_texts",
        )
        add_code_finding(
            code="TotalCreditCheckStatus", value=row.get("TotalCreditCheckStatus"),
            object_id=object_id, scope="delivery_header", text_map=credit_texts,
            field_zh="信用检查状态", field_en="credit-check status",
            normal_values={"C"}, severity="medium", source="credit_status_code_texts",
        )
    for row in delivery_items:
        add_code_finding(
            code="ItemBillingBlockReason", value=row.get("ItemBillingBlockReason"),
            object_id=f"{_text(row, 'DeliveryDocument')}/{_text(row, 'DeliveryDocumentItem')}",
            scope="delivery_item", text_map=billing_texts,
            field_zh="项目开票冻结原因", field_en="item billing block reason",
            source="billing_block_code_texts",
        )
    if status_evidence_complete and needs_adt:
        for key, rows in status_by_key.items():
            for row in rows:
                raw_field = ".".join(
                    part
                    for part in (
                        str(row.get("TBNAM") or "").strip().upper(),
                        str(row.get("FDNAM") or "").strip().upper(),
                    )
                    if part
                ) or "unspecified_field"
                value_text = incompletion_texts.get(raw_field, {})
                finding = {
                "code": "SalesDocumentIncompletionLog",
                "severity": "high" if str(row.get("STATG") or "").strip() else "medium",
                "value": raw_field,
                "value_text": value_text,
                "object": f"{key[0]}/{key[1] or 'HEADER'}",
                "scope": "item",
                "text_source": incompletion_text_sources.get(raw_field, "incompletion_field_texts"),
                "status_group": str(row.get("STATG") or ""),
                "incompletion_group": str(row.get("FEHGR") or ""),
                "detail": _finding_detail(
                    field_zh="不完整字段",
                    field_en="incomplete field",
                    value=raw_field,
                    value_text=value_text,
                    object_id=f"{key[0]}/{key[1] or 'HEADER'}",
                    scope="item",
                ),
            }
                finding_key = (finding["code"], finding["object"], finding["scope"], raw_field)
                if finding_key not in finding_keys:
                    finding_keys.add(finding_key)
                    findings.append(finding)
    elif status_evidence_complete and embedded_status_complete:
        incompletion_fields = (
            ("UVALL", "ItemGeneralIncompletion", "medium"),
            ("UVVLK", "ItemDeliveryIncompletion", "medium"),
            ("UVFAK", "ItemBillingIncompletion", "high"),
            ("UVPRS", "ItemPricingIncompletion", "high"),
        )
        findings.extend(
            {
                "code": code,
                "severity": severity,
                "value": str(row.get(field)),
                "object": (
                    f"{_canonical_sd_key(row.get('SalesOrder'), 10)}/"
                    f"{_canonical_sd_key(row.get('SalesOrderItem'), 6)}"
                ),
            }
            for row in items
            for field, code, severity in incompletion_fields
            if str(row.get(field) or "").strip().upper() not in {"", "0", "C"}
        )
    blocked = bool(findings)
    order_by_key = {
        _canonical_sd_key(row.get("SalesOrder"), 10): row
        for row in orders
        if _text(row, "SalesOrder")
    }
    delivery_documents_by_item: dict[tuple[str, str], list[str]] = {}
    for row in delivery_items:
        key = (
            _canonical_sd_key(row.get("ReferenceSDDocument"), 10),
            _canonical_sd_key(row.get("ReferenceSDDocumentItem"), 6),
        )
        document = _text(row, "DeliveryDocument")
        if all(key) and document:
            delivery_documents_by_item.setdefault(key, []).append(document)
    delivery_header_by_document = {
        _text(row, "DeliveryDocument"): row
        for row in delivery_headers
        if _text(row, "DeliveryDocument")
    }

    def item_reason(row: JsonObject, item_field: str, header_field: str) -> tuple[str, str]:
        item_value = _text(row, item_field)
        if item_value:
            return item_value.upper(), "item"
        order = order_by_key.get(_canonical_sd_key(row.get("SalesOrder"), 10), {})
        header_value = _text(order, header_field)
        if header_value:
            return header_value.upper(), "header"
        key = (
            _canonical_sd_key(row.get("SalesOrder"), 10),
            _canonical_sd_key(row.get("SalesOrderItem"), 6),
        )
        for document in delivery_documents_by_item.get(key, []):
            delivery = delivery_header_by_document.get(document, {})
            delivery_value = _text(delivery, header_field)
            if delivery_value:
                return delivery_value.upper(), "delivery_header"
        return "", "none"

    records = [
        {
            "sales_order": _text(row, "SalesOrder"),
            "sales_order_item": _text(row, "SalesOrderItem"),
            "billing_block_reason": (billing := item_reason(row, "ItemBillingBlockReason", "HeaderBillingBlockReason"))[0],
            "billing_block_reason_text": billing_texts.get(billing[0], {}),
            "billing_block_scope": billing[1],
            "delivery_block_reason": (delivery := item_reason(row, "", "DeliveryBlockReason"))[0],
            "delivery_block_reason_text": delivery_texts.get(delivery[0], {}),
            "delivery_block_scope": delivery[1],
            "credit_status": (credit := item_reason(row, "", "TotalCreditCheckStatus"))[0],
            "credit_status_text": credit_texts.get(credit[0], {}),
            "credit_status_scope": credit[1],
            "incompletion_status": (
                _vbuv_incompletion_summary(
                    status_by_key.get(
                        (
                            _canonical_sd_key(row.get("SalesOrder"), 10),
                            _canonical_sd_key(row.get("SalesOrderItem"), 6),
                        ),
                        [],
                    )
                )
                if needs_adt
                else _embedded_billing_incompletion_summary(row)
            ),
            "incompletion_field_text": {
                "zh": "；".join(
                    str(incompletion_texts.get(code, {}).get("zh") or "")
                    for code in sorted(
                        {
                            ".".join(
                                part
                                for part in (
                                    str(status.get("TBNAM") or "").strip().upper(),
                                    str(status.get("FDNAM") or "").strip().upper(),
                                )
                                if part
                            )
                            for status in status_by_key.get(
                                (
                                    _canonical_sd_key(row.get("SalesOrder"), 10),
                                    _canonical_sd_key(row.get("SalesOrderItem"), 6),
                                ),
                                [],
                            )
                        }
                        - {""}
                    )
                ),
                "en": "; ".join(
                    str(incompletion_texts.get(code, {}).get("en") or "")
                    for code in sorted(
                        {
                            ".".join(
                                part
                                for part in (
                                    str(status.get("TBNAM") or "").strip().upper(),
                                    str(status.get("FDNAM") or "").strip().upper(),
                                )
                                if part
                            )
                            for status in status_by_key.get(
                                (
                                    _canonical_sd_key(row.get("SalesOrder"), 10),
                                    _canonical_sd_key(row.get("SalesOrderItem"), 6),
                                ),
                                [],
                            )
                        }
                        - {""}
                    )
                ),
            },
            "incompletion_fields": [
                {
                    "code": code,
                    "text": incompletion_texts.get(code, {}),
                    "source": incompletion_text_sources.get(code, "unavailable"),
                }
                for code in sorted(
                    {
                        ".".join(
                            part
                            for part in (
                                str(status.get("TBNAM") or "").strip().upper(),
                                str(status.get("FDNAM") or "").strip().upper(),
                            )
                            if part
                        )
                        for status in status_by_key.get(
                            (
                                _canonical_sd_key(row.get("SalesOrder"), 10),
                                _canonical_sd_key(row.get("SalesOrderItem"), 6),
                            ),
                            [],
                        )
                    }
                    - {""}
                )
            ],
        }
        for row in items
        if _text(row, "SalesOrder") and _text(row, "SalesOrderItem")
    ]
    gaps = _gaps(inputs)
    if items and not status_evidence_complete:
        gaps = sorted(set(gaps) | {"sales_order_item_incompletion_evidence"})
    text_evidence_complete = all(
        bool(value_text)
        and _adt_complete(payload)
        and _adt_hash_verified(payload)
        for _value, _source, value_text, payload in required_texts
    )
    incompletion_codes = {
        str(finding.get("value") or "")
        for finding in findings
        if finding.get("code") == "SalesDocumentIncompletionLog"
    }
    for code in incompletion_codes:
        source = incompletion_text_sources.get(code)
        if source == "DD03T":
            complete = (
                _adt_complete(incompletion_text_payload)
                and _adt_hash_verified(incompletion_text_payload)
            )
        elif source == "DD04T via DD03L":
            complete = (
                _adt_complete(incompletion_text_payload)
                and _adt_hash_verified(incompletion_text_payload)
                and _adt_complete(incompletion_definition_payload)
                and _adt_hash_verified(incompletion_definition_payload)
                and _adt_complete(data_element_text_payload)
                and _adt_hash_verified(data_element_text_payload)
            )
        else:
            complete = False
        text_evidence_complete = text_evidence_complete and complete and bool(
            incompletion_texts.get(code)
        )
    code_text_required = bool(required_texts or incompletion_codes)
    if code_text_required and not text_evidence_complete:
        gaps = sorted(set(gaps) | {"code_text_evidence"})
    source_complete = (
        _source_complete(inputs)
        and (not items or status_evidence_complete)
        and (not code_text_required or text_evidence_complete)
    )
    categories = {
        "billing": any(item["code"] in {"HeaderBillingBlockReason", "ItemBillingBlockReason"} for item in findings),
        "delivery": any(item["code"] == "DeliveryBlockReason" for item in findings),
        "credit": any(item["code"] == "TotalCreditCheckStatus" for item in findings),
        "incompletion": any(item["code"] == "SalesDocumentIncompletionLog" or "Incompletion" in item["code"] for item in findings),
    }
    zh_parts = [label for key, label in (("billing", "开票冻结"), ("delivery", "交货冻结"), ("credit", "信用检查异常"), ("incompletion", "字段不完整")) if categories[key]]
    en_parts = [label for key, label in (("billing", "billing blocks"), ("delivery", "delivery blocks"), ("credit", "credit-check exceptions"), ("incompletion", "incomplete fields")) if categories[key]]
    return _result(
        inputs,
        business_status="blocked" if blocked else "normal",
        headline_zh=("发现" + "、".join(zh_parts)) if blocked else "未发现冻结、信用检查异常或字段不完整",
        headline_en=("Found " + ", ".join(en_parts)) if blocked else "No blocks, credit-check exceptions, or incomplete fields were found",
        overview_zh="系统按订单、项目和交货层级检查了冻结、信用及不完整状态。",
        overview_en="Order, item, delivery, credit, and incompletion statuses were checked.",
        stages=[
            _stage("sales_order", "销售订单", "Sales order", len(orders)),
            _stage("items", "订单项目", "Order items", len(items)),
            _stage(
                "item_incompletion",
                "项目不完整状态",
                "Item incompletion",
                len(status_rows),
                state="confirmed" if status_evidence_complete else "unknown",
                detail_zh=(
                    f"精确订单范围的不完整日志已完整验证，返回 {len(status_rows)} 条缺失字段记录。"
                    if status_evidence_complete and needs_adt
                    else f"Embedded项目状态已完整验证，共 {len(items)} 个项目。"
                    if status_evidence_complete
                    else "项目级不完整状态证据不完整。"
                ),
                detail_en=(
                    f"The exact-order incompletion log was verified completely and returned {len(status_rows)} missing-field row(s)."
                    if status_evidence_complete and needs_adt
                    else f"Embedded item status was verified for {len(items)} item(s)."
                    if status_evidence_complete
                    else "Item-level incompletion evidence is incomplete."
                ),
            ),
            _stage("delivery", "交货", "Delivery", len(deliveries)),
        ],
        findings=findings,
        metrics=[{
            "id": "blocked_findings",
            "label": {"zh": "冻结及异常发现数", "en": "Block and exception findings"},
            "value": len(findings),
        }],
        records=records,
        record_columns=[
            {"key": "sales_order", "label": {"zh": "销售订单", "en": "Sales order"}},
            {"key": "sales_order_item", "label": {"zh": "订单项目", "en": "Sales-order item"}},
            {"key": "billing_block_reason", "label": {"zh": "开票冻结原因代码", "en": "Billing block reason code"}},
            {"key": "billing_block_reason_text", "label": {"zh": "开票冻结原因文本", "en": "Billing block reason text"}},
            {"key": "billing_block_scope", "label": {"zh": "开票冻结来源", "en": "Billing block scope"}},
            {"key": "delivery_block_reason", "label": {"zh": "交货冻结原因代码", "en": "Delivery block reason code"}},
            {"key": "delivery_block_reason_text", "label": {"zh": "交货冻结原因文本", "en": "Delivery block reason text"}},
            {"key": "delivery_block_scope", "label": {"zh": "交货冻结来源", "en": "Delivery block scope"}},
            {"key": "credit_status", "label": {"zh": "信用状态代码", "en": "Credit status code"}},
            {"key": "credit_status_text", "label": {"zh": "信用状态文本", "en": "Credit status text"}},
            {"key": "credit_status_scope", "label": {"zh": "信用状态来源", "en": "Credit status scope"}},
            {"key": "incompletion_status", "label": {"zh": "不完整字段代码", "en": "Incompletion field code"}},
            {"key": "incompletion_field_text", "label": {"zh": "不完整字段文本", "en": "Incompletion field text"}},
            {"key": "business_status", "label": {"zh": "业务状态", "en": "Business status"}, "format": "status"},
        ],
        gaps=gaps,
        actions_zh=["按冻结所在层级交由销售、信用或主数据人员处理。"] if blocked else [],
        actions_en=["Route the block to sales, credit, or master-data owners."] if blocked else [],
        source_complete_override=source_complete,
        preserve_business_status_on_gap=(bool(gaps) and set(gaps) == {"code_text_evidence"}),
    )


def _billing_completeness(inputs: JsonObject) -> JsonObject:
    headers = _rows(inputs, "billing_headers")
    items = _rows(inputs, "billing_items")
    sources = _rows(inputs, "source_sales_items", "source_delivery_items")
    findings: list[JsonObject] = []
    for row in headers:
        if _truthy(row.get("BillingDocumentIsCancelled")):
            findings.append({"code": "CANCELLED_BILLING", "severity": "high"})
        if str(row.get("AccountingPostingStatus") or "") not in {"", "C"}:
            findings.append({"code": "ACCOUNTING_NOT_POSTED", "severity": "medium"})
    referenced = {(str(row.get("BillingDocument")), str(row.get("ReferenceSDDocument"))) for row in items}
    if len(referenced) < len(items):
        findings.append({"code": "DUPLICATE_REFERENCE", "severity": "medium"})
    attention = bool(findings) or not items
    header_by_document = {_text(row, "BillingDocument"): row for row in headers}
    records = [
        {
            "billing_document": _text(row, "BillingDocument"),
            "billing_document_item": _text(row, "BillingDocumentItem"),
            "reference_document": _text(row, "ReferenceSDDocument"),
            "reference_document_item": _text(row, "ReferenceSDDocumentItem"),
            "accounting_posting_status": _text(
                header_by_document.get(_text(row, "BillingDocument"), {}),
                "AccountingPostingStatus",
            ),
            "cancelled": _truthy(
                header_by_document.get(_text(row, "BillingDocument"), {}).get(
                    "BillingDocumentIsCancelled"
                )
            ),
        }
        for row in items
        if _text(row, "BillingDocument") and _text(row, "BillingDocumentItem")
    ]
    return _result(
        inputs,
        business_status="attention" if attention else "normal",
        headline_zh="开票完整性需要复核" if attention else "开票凭证基础完整性检查通过",
        headline_en="Billing completeness requires review" if attention else "Basic billing completeness checks passed",
        overview_zh="已核对开票状态、取消标志、来源引用及财务过账状态。",
        overview_en="Billing status, cancellation, source references, and accounting posting were checked.",
        stages=[_stage("billing", "开票凭证", "Billing document", len(headers) + len(items)), _stage("source", "来源订单或交货", "Source order or delivery", len(sources))],
        findings=findings,
        metrics=[
            {"id": "billing_items", "value": len(items)},
            {"id": "source_rows", "value": len(sources)},
            {"id": "finding_count", "value": len(findings)},
        ],
        records=records,
        actions_zh=["复核取消、重复引用或尚未过账的开票项目。"] if attention else [],
        actions_en=["Review cancelled, duplicate, or unposted billing items."] if attention else [],
    )


def _delivered_not_billed(inputs: JsonObject) -> JsonObject:
    deliveries = _rows(inputs, "delivery_headers")
    delivery_items = _rows(inputs, "delivery_items")
    billing = _rows(inputs, "billing_items")
    billed_refs = {str(row.get("ReferenceSDDocument") or "") for row in billing}
    candidates = [
        row for row in deliveries
        if (_truthy(row.get("OverallGoodsMovementStatus")) or row.get("ActualGoodsMovementDate"))
        and str(row.get("DeliveryDocument") or "") not in billed_refs
    ]
    findings = [{"code": "DELIVERED_NOT_BILLED", "severity": "high", "delivery": str(row.get("DeliveryDocument") or "")} for row in candidates]
    candidate_ids = {_text(row, "DeliveryDocument") for row in candidates}
    header_by_delivery = {_text(row, "DeliveryDocument"): row for row in deliveries}
    records = [
        {
            "delivery_document": _text(row, "DeliveryDocument"),
            "delivery_document_item": _text(row, "DeliveryDocumentItem"),
            "actual_goods_movement_date": _date_text(
                header_by_delivery.get(_text(row, "DeliveryDocument"), {}),
                "ActualGoodsMovementDate",
            ),
            "is_billed": _text(row, "DeliveryDocument") not in candidate_ids,
        }
        for row in delivery_items
        if _text(row, "DeliveryDocument") and _text(row, "DeliveryDocumentItem")
    ]
    return _result(
        inputs,
        business_status="attention" if candidates else "normal",
        headline_zh=f"发现 {len(candidates)} 张已发货未开票交货单" if candidates else "未发现已发货未开票交货单",
        headline_en=f"Found {len(candidates)} delivered-not-billed delivery document(s)" if candidates else "No delivered-not-billed delivery was found",
        overview_zh="仅把已完成发货过账且没有关联开票凭证的交货列为异常。",
        overview_en="Only deliveries with PGI evidence and no linked billing document are flagged.",
        stages=[_stage("delivery", "已发货交货", "PGI deliveries", len(deliveries) + len(delivery_items)), _stage("billing", "关联开票", "Linked billing", len(billing))],
        findings=findings,
        metrics=[{"id": "delivered_not_billed", "value": len(candidates)}],
        records=records,
        actions_zh=["检查开票到期清单、开票冻结和凭证完整性。"] if candidates else [],
        actions_en=["Review billing due lists, billing blocks, and document completeness."] if candidates else [],
    )


def _delivery_delay(inputs: JsonObject) -> JsonObject:
    orders = _rows(inputs, "sales_orders")
    schedules = _rows(inputs, "schedule_lines")
    delivery_items = _rows(inputs, "delivery_items")
    deliveries = _rows(inputs, "delivery_headers")
    run_input = inputs.get("run_input") or {}
    date_from = _date(run_input.get("date_from"))
    date_to = _date(run_input.get("date_to")) or date.today()
    schedules = [
        row
        for row in schedules
        if (
            (due := _date(row.get("ConfirmedDeliveryDate") or row.get("RequestedDeliveryDate")))
            and (date_from is None or due >= date_from)
            and due <= date_to
        )
    ]
    header_by_delivery = {
        _text(row, "DeliveryDocument"): row for row in deliveries
    }
    deliveries_by_order_item: dict[tuple[str, str], list[JsonObject]] = {}
    for row in delivery_items:
        key = (
            _text(row, "ReferenceSDDocument"),
            _text(row, "ReferenceSDDocumentItem").lstrip("0") or "0",
        )
        header = header_by_delivery.get(_text(row, "DeliveryDocument"), {})
        deliveries_by_order_item.setdefault(key, []).append(header)
    scores: list[int] = []
    for row in schedules:
        due = _date(row.get("ConfirmedDeliveryDate") or row.get("RequestedDeliveryDate"))
        key = (
            _text(row, "SalesOrder"),
            _text(row, "SalesOrderItem").lstrip("0") or "0",
        )
        actual_dates = [
            actual
            for header in deliveries_by_order_item.get(key, [])
            if (actual := _date(header.get("ActualGoodsMovementDate"))) is not None
        ]
        actual = max(actual_dates, default=None)
        if due and actual:
            delay_days = (actual - due).days
            score = 100 if delay_days > 30 else 70 if delay_days > 7 else 40 if delay_days > 0 else 0
        elif due and due <= date_to:
            score = 100
        elif due and (due - date_to).days <= 2:
            score = 20
        else:
            score = 0
        if row.get("DelivBlockReasonForSchedLine"):
            score = max(score, 70)
        scores.append(score)
    maximum = max(scores, default=0)
    evidence_discrepancy = any(
        _decimal(row.get("DeliveredQtyInOrderQtyUnit")) == 0
        and deliveries_by_order_item.get(
            (
                _text(row, "SalesOrder"),
                _text(row, "SalesOrderItem").lstrip("0") or "0",
            )
        )
        for row in schedules
    )
    findings = [{"code": "DELIVERY_DELAY_RISK", "severity": "high" if score >= 60 else "medium", "score": score} for score in scores if score]
    records = [
        {
            "sales_order": _text(row, "SalesOrder"),
            "sales_order_item": _text(row, "SalesOrderItem"),
            "schedule_line": _text(row, "ScheduleLine"),
            "due_date": _date_text(row, "ConfirmedDeliveryDate", "RequestedDeliveryDate"),
            "delivery_block_reason": _text(row, "DelivBlockReasonForSchedLine"),
            "risk_score": score,
        }
        for row, score in zip(schedules, scores)
        if _text(row, "SalesOrder") and _text(row, "SalesOrderItem") and _text(row, "ScheduleLine")
    ]
    return _result(
        inputs,
        business_status="attention" if maximum else "normal",
        headline_zh=f"最高交货延期风险评分为 {maximum}",
        headline_en=f"The highest delivery-delay risk score is {maximum}",
        overview_zh="评分仅使用到期日期、交货完成情况和冻结证据，不进行机器学习预测。",
        overview_en="The score uses due dates, delivery completion, and block evidence; it is not an ML forecast.",
        stages=[_stage("orders", "销售需求", "Sales demand", len(orders) + len(schedules)), _stage("delivery", "交货执行", "Delivery execution", len(delivery_items) + len(deliveries))],
        findings=findings,
        metrics=[{"id": "maximum_risk_score", "value": maximum}],
        limitations=["schedule_line_delivery_evidence_discrepancy"] if evidence_discrepancy else [],
        records=records,
        actions_zh=["优先复核高分订单的承诺日期和交货冻结。"] if maximum else [],
        actions_en=["Review commitment dates and delivery blocks for high-score orders."] if maximum else [],
    )


def _due_priority(inputs: JsonObject) -> JsonObject:
    schedules = _rows(inputs, "schedule_lines")
    items = _rows(inputs, "sales_order_items")
    stock = _rows(inputs, "material_stock")
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    date_from = _date(run_input.get("date_from"))
    date_to = _date(run_input.get("date_to"))
    schedules = [
        row
        for row in schedules
        if (
            (due_date := _date(row.get("ConfirmedDeliveryDate") or row.get("RequestedDeliveryDate")))
            and (date_from is None or due_date >= date_from)
            and (date_to is None or due_date <= date_to)
        )
    ]
    item_by_key = {
        (_text(row, "SalesOrder"), _text(row, "SalesOrderItem")): row
        for row in items
    }
    records = [
        {
            "sales_order": _text(row, "SalesOrder"),
            "sales_order_item": _text(row, "SalesOrderItem"),
            "schedule_line": _text(row, "ScheduleLine"),
            "material": _text(
                item_by_key.get((_text(row, "SalesOrder"), _text(row, "SalesOrderItem")), {}),
                "Material",
            ),
            "plant": _text(
                item_by_key.get((_text(row, "SalesOrder"), _text(row, "SalesOrderItem")), {}),
                "ProductionPlant",
                "Plant",
            ),
            "due_date": _date_text(row, "ConfirmedDeliveryDate", "RequestedDeliveryDate"),
            "requested_quantity": _text(row, "ScheduleLineOrderQuantity", "RequestedQuantity"),
            "confirmed_quantity": _text(row, "ConfdOrderQtyByMatlAvailCheck", "ConfdDelivQtyInOrderQtyUnit"),
            "unit": _text(
                item_by_key.get((_text(row, "SalesOrder"), _text(row, "SalesOrderItem")), {}),
                "RequestedQuantityUnit",
                "OrderQuantityUnit",
                "BaseUnit",
            ),
        }
        for row in schedules
        if _text(row, "SalesOrder") and _text(row, "SalesOrderItem") and _text(row, "ScheduleLine")
    ]
    return _result(
        inputs,
        business_status="attention" if schedules else "normal",
        headline_zh=f"已生成 {len(schedules)} 条到期交货优先级记录",
        headline_en=f"Generated {len(schedules)} due-delivery priority record(s)",
        overview_zh="排序依据为到期时间、订单交货优先级、冻结和当前库存证据；结果不会回写 SAP。",
        overview_en="Ranking uses due dates, delivery priority, blocks, and current stock evidence and is never written back to SAP.",
        stages=[_stage("demand", "到期需求", "Due demand", len(schedules) + len(items)), _stage("stock", "库存", "Stock", len(stock))],
        metrics=[{"id": "ranked_requirements", "value": len(schedules)}],
        records=records,
        limitations=["current_stock_not_historical_atp"] if stock else [],
        actions_zh=["由销售和仓库人员按优先级清单复核并执行。"] if schedules else [],
        actions_en=["Have sales and warehouse users review and act on the ranking."] if schedules else [],
    )


def _returns_credit(inputs: JsonObject) -> JsonObject:
    return_headers = _rows(inputs, "returns")
    return_items = _rows(inputs, "return_items")
    credits = _rows(inputs, "credit_requests", "credit_request_items")
    billing = _rows(inputs, "billing_documents")
    billing_by_return_item = {
        (_text(row, "SalesDocument"), _text(row, "SalesDocumentItem")): row
        for row in billing
        if _text(row, "SalesDocument") and _text(row, "SalesDocumentItem")
    }
    findings = []
    missing_receipt = False
    missing_refund_type = False
    for row in return_items:
        if not row.get("ReferenceSDDocument"):
            findings.append({"code": "RETURN_REFERENCE_MISSING", "severity": "medium"})
        receipt_value = row.get("ReturnsMaterialHasBeenReceived")
        refund_value = row.get("ReturnsRefundType")
        if receipt_value in {None, ""}:
            missing_receipt = True
        if refund_value in {None, ""}:
            missing_refund_type = True
        if refund_value not in {None, ""} and receipt_value not in {None, ""} and not _truthy(receipt_value):
            findings.append({"code": "REFUND_BEFORE_RECEIPT", "severity": "medium"})
    records = [
        {
            "customer_return": _text(row, "CustomerReturn"),
            "customer_return_item": _text(row, "CustomerReturnItem"),
            "reference_document": (
                "/".join(
                    item
                    for item in (
                        _text(
                            billing_by_return_item.get(
                                (
                                    _text(row, "CustomerReturn"),
                                    _text(row, "CustomerReturnItem"),
                                ),
                                {},
                            ),
                            "BillingDocument",
                        ),
                        _text(
                            billing_by_return_item.get(
                                (
                                    _text(row, "CustomerReturn"),
                                    _text(row, "CustomerReturnItem"),
                                ),
                                {},
                            ),
                            "BillingDocumentItem",
                        ),
                    )
                    if item
                )
                or _text(row, "ReferenceSDDocument")
            ),
            "material_received": (
                None
                if row.get("ReturnsMaterialHasBeenReceived") in {None, ""}
                else _truthy(row.get("ReturnsMaterialHasBeenReceived"))
            ),
            "refund_type": _text(row, "ReturnsRefundType"),
        }
        for row in return_items
        if _text(row, "CustomerReturn") and _text(row, "CustomerReturnItem")
    ]
    business_gaps = [
        *(["return_receipt_evidence"] if missing_receipt else []),
        *(["return_refund_type_evidence"] if missing_refund_type else []),
    ]
    return _result(
        inputs,
        business_status=(
            "capability_blocked"
            if business_gaps
            else "attention" if findings else "normal"
        ),
        headline_zh=f"退货与贷项检查发现 {len(findings)} 项需要复核",
        headline_en=f"Returns and credit checks found {len(findings)} item(s) requiring review",
        overview_zh="已检查退货引用、收货状态、退款处理和后续贷项凭证。",
        overview_en="Return references, receipt status, refund handling, and follow-on credit documents were checked.",
        stages=[_stage("returns", "客户退货", "Customer returns", len(return_headers) + len(return_items)), _stage("credits", "贷项请求", "Credit requests", len(credits)), _stage("billing", "后续开票", "Follow-on billing", len(billing))],
        findings=findings,
        metrics=[{"id": "finding_count", "value": len(findings)}],
        records=records,
        gaps=_gaps(inputs, *business_gaps),
        source_complete_override=_source_complete(inputs),
        actions_zh=["复核缺少原单引用或未收货已退款的业务单据。"] if findings else [],
        actions_en=["Review documents with missing source references or refunds before receipt."] if findings else [],
    )


def _shortage_allocation(inputs: JsonObject) -> JsonObject:
    items = _rows(inputs, "sales_order_items")
    schedules = _rows(inputs, "schedule_lines")
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    date_from = _date(run_input.get("date_from"))
    date_to = _date(run_input.get("date_to"))
    if date_from or date_to:
        schedules = [
            row
            for row in schedules
            if (due := _date(row.get("RequestedDeliveryDate") or row.get("ConfirmedDeliveryDate")))
            and (date_from is None or due >= date_from)
            and (date_to is None or due <= date_to)
        ]
    stock = _rows(inputs, "material_stock")
    atp = _rows(inputs, "atp_availability")
    available = sum(
        (_decimal(row.get("MatlWrhsStkQtyInMatlBaseUnit")) for row in stock),
        Decimal(0),
    )
    requested = sum(
        (_decimal(row.get("ScheduleLineOrderQuantity")) for row in schedules),
        Decimal(0),
    )
    confirmed = sum(
        (_decimal(row.get("ConfdOrderQtyByMatlAvailCheck")) for row in schedules),
        Decimal(0),
    )
    unconfirmed = max(requested - confirmed, Decimal(0))
    key_date_is_current = (
        (date_from is None or date_from <= date.today())
        and (date_to is None or date.today() <= date_to)
    )
    stock_metric = available if key_date_is_current else None
    atp_complete = bool(atp) and _source_complete(inputs)
    shortage = max(unconfirmed - available, Decimal(0)) if atp_complete else unconfirmed
    gaps = _gaps(inputs, *(() if atp_complete else ("atp_availability_evidence",)))
    item_by_key = {
        (_text(row, "SalesOrder"), _text(row, "SalesOrderItem")): row
        for row in items
    }
    records = [
        {
            "sales_order": _text(row, "SalesOrder"),
            "sales_order_item": _text(row, "SalesOrderItem"),
            "schedule_line": _text(row, "ScheduleLine"),
            "material": _text(
                item_by_key.get((_text(row, "SalesOrder"), _text(row, "SalesOrderItem")), {}),
                "Material",
            ),
            "plant": _text(
                item_by_key.get((_text(row, "SalesOrder"), _text(row, "SalesOrderItem")), {}),
                "ProductionPlant",
                "Plant",
            ),
            "requested_quantity": _text(row, "ScheduleLineOrderQuantity", "RequestedQuantity"),
            "confirmed_quantity": _text(row, "ConfdOrderQtyByMatlAvailCheck", "ConfdDelivQtyInOrderQtyUnit"),
            "unit": _text(
                item_by_key.get((_text(row, "SalesOrder"), _text(row, "SalesOrderItem")), {}),
                "RequestedQuantityUnit",
                "OrderQuantityUnit",
                "BaseUnit",
            ),
        }
        for row in schedules
        if _text(row, "SalesOrder") and _text(row, "SalesOrderItem") and _text(row, "ScheduleLine")
    ]
    return _result(
        inputs,
        business_status=("attention" if shortage else "normal") if atp_complete else "capability_blocked",
        headline_zh=(f"ATP 证据下的未覆盖需求为 {shortage}" if atp_complete else "缺少完整 ATP 证据，未覆盖需求量无法确定"),
        headline_en=(f"Uncovered demand under complete ATP evidence is {shortage}" if atp_complete else "Uncovered demand is inconclusive without complete ATP evidence"),
        overview_zh="优先使用 Released ATP API；MDKP/MDTB 只作为 MRP 上下文，绝不冒充 ATP 结果。",
        overview_en="The Released ATP API is authoritative; MDKP/MDTB may add MRP context but never substitute for ATP.",
        stages=[_stage("demand", "订单需求", "Order demand", len(items) + len(schedules)), _stage("stock", "库存快照", "Stock snapshot", len(stock)), _stage("atp", "ATP 可用量", "ATP availability", len(atp), state="confirmed" if atp_complete else "unknown")],
        metrics=[
            {"id": "requested", "value": str(requested)},
            {"id": "confirmed", "value": str(confirmed)},
            {"id": "stock", "value": str(stock_metric) if stock_metric is not None else None},
            # Requested minus confirmed is observable even without Released ATP.
            # It is not a recommended allocation quantity while the ATP gap remains.
            {"id": "uncovered", "value": str(shortage)},
        ],
        records=records,
        gaps=gaps,
        source_complete_override=_source_complete(inputs),
        actions_zh=["由计划人员在 SAP 中执行 ATP 复核后再决定分配。"],
        actions_en=["Have a planner run an ATP review in SAP before deciding allocations."],
    )


def _o2c_anomaly(inputs: JsonObject) -> JsonObject:
    orders = _rows(inputs, "sales_orders")
    order_items = _rows(inputs, "sales_order_items")
    deliveries = _rows(inputs, "delivery_headers")
    billing = _rows(inputs, "billing_headers")
    accounting_by_key: dict[tuple[str, str, str, str], JsonObject] = {}
    for row in _rows(inputs, "accounting_items", "accounting_items_by_billing"):
        key = (
            _text(row, "CompanyCode"),
            _text(row, "FiscalYear"),
            _text(row, "AccountingDocument"),
            _text(row, "AccountingDocumentItem"),
        )
        accounting_by_key[key] = row
    # O2C receivable anomalies belong to the customer subledger.  The
    # accounting document also contains GL lines, which must not be counted as
    # three separate open-receivable anomalies.
    accounting = [
        row
        for row in accounting_by_key.values()
        if _text(row, "FinancialAccountType") == "D"
    ]
    anomalies = sum(1 for row in orders if row.get("TotalBlockStatus"))
    anomalies += sum(1 for row in billing if _truthy(row.get("BillingDocumentIsCancelled")))
    anomalies += sum(1 for row in accounting if not _truthy(row.get("IsCleared")))
    output_rows = _adt_rows(inputs, "output_status")
    dispute_rows = _adt_rows(inputs, "dispute_case")
    output_complete = _adt_complete(_fallback(inputs, "output_status")) and bool(output_rows)
    dispute_complete = _adt_complete(_fallback(inputs, "dispute_case")) and bool(dispute_rows)
    missing = []
    if not output_complete:
        missing.append("billing_output_status_evidence")
    if not dispute_complete:
        missing.append("billing_dispute_case_evidence")
    gaps = _gaps(inputs, *missing)
    order_by_id = {_text(row, "SalesOrder"): row for row in orders}
    records = [
        {
            "sales_order": _text(row, "SalesOrder"),
            "sales_order_item": _text(row, "SalesOrderItem"),
            "material": _text(row, "Material"),
            "item_billing_status": _text(
                row,
                "OverallBillingStatus",
                "OverallOrdReltdBillgStatus",
            )
            or _text(
                order_by_id.get(_text(row, "SalesOrder"), {}),
                "OverallOrdReltdBillgStatus",
            ),
            "item_delivery_status": _text(row, "DeliveryStatus", "OverallDeliveryStatus")
            or _text(
                order_by_id.get(_text(row, "SalesOrder"), {}),
                "OverallDeliveryStatus",
            ),
        }
        for row in order_items
        if _text(row, "SalesOrder") and _text(row, "SalesOrderItem")
    ]
    return _result(
        inputs,
        business_status="attention" if anomalies else "partial",
        headline_zh=f"当前证据中识别到 {anomalies} 项 O2C 异常",
        headline_en=f"Identified {anomalies} O2C anomaly item(s) in current evidence",
        overview_zh="结果覆盖订单、交货、开票和应收，并复用经过完整性与 Hash 验证的输出及争议 ADT 证据。",
        overview_en="The result covers orders, deliveries, billing, and receivables and reuses completeness- and hash-verified output and dispute ADT evidence.",
        stages=[_stage("orders", "销售订单", "Sales orders", len(orders)), _stage("deliveries", "交货", "Deliveries", len(deliveries)), _stage("billing", "开票", "Billing", len(billing)), _stage("accounting", "应收", "Receivables", len(accounting)), _stage("output", "输出状态", "Output status", len(output_rows), state="confirmed" if output_complete else "unknown"), _stage("dispute", "争议案件", "Dispute cases", len(dispute_rows), state="confirmed" if dispute_complete else "unknown")],
        metrics=[{"id": "anomaly_count", "value": anomalies}],
        records=records,
        gaps=gaps,
        actions_zh=["按冻结、取消和未清应收分别分派责任人。"] if anomalies else [],
        actions_en=["Assign owners for blocks, cancellations, and open receivables."] if anomalies else [],
    )


def _billing_output(inputs: JsonObject) -> JsonObject:
    billing = _rows(inputs, "billing_headers", "billing_items")
    output_rows = _adt_rows(inputs, "output_status")
    complete = _adt_complete(_fallback(inputs, "output_status")) and bool(output_rows)
    failures = [row for row in output_rows if str(row.get("VSTAT") or "").strip() not in {"", "1"}]
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    records = [
        {
            "billing_document": _text(row, "OBJKY", "BillingDocument") or str(run_input.get("billing_document") or ""),
            "output_request": "|".join(
                filter(
                    None,
                    (
                        _text(row, "KAPPL", "Application"),
                        _text(row, "KSCHL", "OutputType"),
                        _text(row, "SPRAS", "Language"),
                        _text(row, "NACHA", "TransmissionMedium"),
                    ),
                )
            ),
            "output_status": _text(row, "VSTAT", "ProcessingStatus"),
            "output_type": _text(row, "KSCHL", "OutputType"),
            "transmission_medium": _text(row, "NACHA", "TransmissionMedium"),
        }
        for row in output_rows
    ]
    return _result(
        inputs,
        business_status="attention" if failures else "normal",
        headline_zh=f"取得 {len(output_rows)} 条结构化输出状态，{len(failures)} 条需要复核" if complete else "输出状态证据不完整，无法确认发送结果",
        headline_en=f"Found {len(output_rows)} structured output status row(s); {len(failures)} require review" if complete else "Output evidence is incomplete, so delivery cannot be confirmed",
        overview_zh="只读取 NAST 或已批准的结构化输出状态；不读取收件人、邮件正文或附件。完整零行也不能证明发票未输出。",
        overview_en="Only NAST or approved structured output status is read; recipients, message bodies, and attachments are excluded. A complete zero-row result does not prove that no output occurred.",
        stages=[_stage("base_document", "开票凭证", "Billing document", len(billing)), _stage("output_status", "结构化输出状态", "Structured output status", len(output_rows), state="confirmed" if complete else "unknown")],
        findings=[{"code": "OUTPUT_STATUS_REQUIRES_REVIEW", "severity": "medium"} for _ in failures],
        metrics=[
            {"id": "output_rows", "value": len(output_rows) if complete else None},
            {"id": "failed_outputs", "value": len(failures) if complete else None},
        ],
        records=records,
        gaps=_gaps(inputs, *(() if complete else ("billing_output_status_evidence",))),
        actions_zh=["由开票输出负责人复核失败或未处理状态。"] if failures else [],
        actions_en=["Have the billing-output owner review failed or unprocessed statuses."] if failures else [],
        source_complete_override=_source_complete(inputs),
        allow_empty_records=True,
    )


def _billing_dispute(inputs: JsonObject) -> JsonObject:
    billing = _rows(inputs, "billing_headers", "billing_items", "accounting_items")
    cases = _adt_rows(inputs, "dispute_case")
    complete = _adt_complete(_fallback(inputs, "dispute_case")) and bool(cases)
    categories: dict[str, int] = {}
    for row in cases:
        code = str(row.get("FIN_ROOT_CODE") or "UNCLASSIFIED").strip() or "UNCLASSIFIED"
        categories[code] = categories.get(code, 0) + 1
    records = [
        {
            "billing_document": _text(row, "BillingDocument"),
            "billing_document_item": _text(row, "BillingDocumentItem"),
            "dispute_case_count": len(cases) if complete else "",
            "root_cause_codes": ",".join(sorted(categories)),
        }
        for row in billing
        if _text(row, "BillingDocument") and _text(row, "BillingDocumentItem")
    ]
    return _result(
        inputs,
        business_status="attention" if cases else "partial",
        headline_zh=f"取得 {len(cases)} 条结构化争议案件属性" if complete else "争议案件证据不完整，无法完成分类",
        headline_en=f"Found {len(cases)} structured dispute case attribute row(s)" if complete else "Dispute case evidence is incomplete, so classification is inconclusive",
        overview_zh="仅使用已批准的案件GUID、根因码、到期日、客户、公司代码和来源；不导出自由文本或客户通信。",
        overview_en="Only approved case GUID, root-cause code, due date, customer, company code, and origin are used; free text and customer communications are excluded.",
        stages=[_stage("base_document", "开票与 FI 凭证", "Billing and FI documents", len(billing)), _stage("dispute_case", "结构化争议案件", "Structured dispute cases", len(cases), state="confirmed" if complete else "unknown")],
        metrics=[
            {"id": "case_count", "value": len(cases) if complete else None},
            {"id": "root_cause_counts", "value": categories if complete else None},
        ],
        records=records,
        gaps=_gaps(inputs, *(() if complete else ("billing_dispute_case_evidence",))),
        actions_zh=["由应收争议负责人按根因码复核并补充业务处置。"] if cases else [],
        actions_en=["Have the AR dispute owner review root-cause codes and determine follow-up actions."] if cases else [],
        source_complete_override=_source_complete(inputs),
    )


def _ap_payment(inputs: JsonObject) -> JsonObject:
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    inferred_mode = "p2p_evidence" if run_input.get("ap_payment_scopes") else "direct"
    query_mode = str(run_input.get("query_mode") or inferred_mode)
    cutoff = _date(run_input.get("as_of"))
    scopes = run_input.get("ap_payment_scopes") if query_mode == "p2p_evidence" else None
    if query_mode == "p2p_evidence":
        scopes = [dict(item) for item in scopes or [] if isinstance(item, dict)]
        items = [
            _ap_row_from_p2p(scope, row)
            for scope in scopes
            for row in scope.get("fi_supplier_items") or []
            if isinstance(row, dict)
        ]
        source_complete = bool(scopes) and all(scope.get("source_complete") is True for scope in scopes)
        evidence_complete = bool(scopes) and all(scope.get("evidence_complete") is True for scope in scopes)
    else:
        items = [
            row
            for row in _rows(inputs, "collect_ap_evidence", "supplier_items", "clearing_documents")
            if str(row.get("FinancialAccountType") or "K").upper() == "K"
            and _truthy(row.get("IsOpenItemManaged"))
        ]
        direct_scope_id = f"{run_input.get('company_code', '')}:{run_input.get('supplier', '')}"
        items = [{**row, "ScopeID": direct_scope_id} for row in items]
        scopes = [
            {
                "scope_id": direct_scope_id,
                "company_code": str(run_input.get("company_code") or ""),
                "supplier": str(run_input.get("supplier") or ""),
                "purchase_orders": [],
                "source_complete": _source_complete(inputs),
                "evidence_complete": _source_complete(inputs),
            }
        ]
        source_complete = _source_complete(inputs)
        evidence_complete = source_complete

    payment_run_evidence_complete = bool(scopes) and all(
        scope.get("payment_run_evidence_complete") is True for scope in scopes
    )
    bank_master_evidence_complete = bool(scopes) and all(
        scope.get("bank_master_evidence_complete") is True for scope in scopes
    )
    bank_settlement_evidence_complete = bool(scopes) and all(
        scope.get("bank_settlement_evidence_complete") is True for scope in scopes
    )

    open_items: list[JsonObject] = []
    records: list[JsonObject] = []
    missing_due_date = 0
    overdue = 0
    due_now = 0
    blocked = 0
    discount_available = 0
    for row in items:
        posting_date = _date(row.get("PostingDate"))
        clearing_date = _date(row.get("ClearingDate"))
        if cutoff is not None and posting_date is not None and posting_date > cutoff:
            continue
        if cutoff is not None and clearing_date is not None and clearing_date <= cutoff:
            continue
        open_items.append(row)
        due_date = _date(row.get("NetDueDate"))
        discount_date = _date(row.get("CashDiscount1DueDate"))
        payment_block = _text(row, "PaymentBlockingReason")
        if due_date is None:
            readiness = "unknown_due_date"
            missing_due_date += 1
        elif cutoff is not None and due_date < cutoff:
            readiness = "overdue_blocked" if payment_block else "overdue_ready"
            overdue += 1
        elif cutoff is not None and due_date == cutoff:
            readiness = "due_blocked" if payment_block else "due_ready"
            due_now += 1
        else:
            readiness = "not_due"
        if payment_block:
            blocked += 1
        if discount_date is not None and cutoff is not None and cutoff <= discount_date:
            discount_available += 1
        records.append(
            {
                "scope_id": _text(row, "ScopeID"),
                "purchase_order": _text(row, "PurchasingDocument"),
                "company_code": _text(row, "CompanyCode"),
                "supplier": _text(row, "Supplier"),
                "fiscal_year": _text(row, "FiscalYear"),
                "accounting_document": _text(row, "AccountingDocument"),
                "accounting_document_item": _text(row, "AccountingDocumentItem"),
                "ledger": _text(row, "Ledger"),
                "posting_date": posting_date.isoformat() if posting_date else "",
                "net_due_date": due_date.isoformat() if due_date else "",
                "cash_discount_due_date": discount_date.isoformat() if discount_date else "",
                "debit_credit": _text(row, "DebitCreditCode"),
                "amount": _text(row, "AmountInTransactionCurrency", "AmountInCompanyCodeCurrency"),
                "currency": _text(row, "TransactionCurrency", "CompanyCodeCurrency"),
                "payment_blocking_reason": payment_block,
                "payment_readiness": readiness,
                "as_of_status": "open_subsequently_cleared" if clearing_date else "open",
                "clearing_date": clearing_date.isoformat() if clearing_date else "",
                "clearing_document": _text(row, "ClearingAccountingDocument"),
                "payment_evidence_status": "bank_settlement_not_proven",
            }
        )

    duplicate_findings = _ap_duplicate_findings(records)
    scope_results = _ap_scope_results(scopes, records, source_complete, evidence_complete)
    if not source_complete or not evidence_complete or cutoff is None or missing_due_date:
        business_status = "inconclusive"
    elif blocked or duplicate_findings:
        business_status = "blocked"
    elif open_items:
        business_status = "in_progress"
    else:
        business_status = "complete"
    gaps = _gaps(inputs, "bank_settlement_not_proven", "payment_run_and_bank_master_evidence")
    if missing_due_date:
        gaps.append("net_due_date_evidence")
    result = _result(
        inputs,
        business_status=business_status,
        headline_zh=f"发现 {len(open_items)} 条截止日未清项，其中 {blocked} 条付款冻结、{overdue} 条逾期",
        headline_en=f"Found {len(open_items)} open item(s) at the cutoff, including {blocked} payment-blocked and {overdue} overdue item(s)",
        overview_zh="已分别核验截止日未清状态、SAP净到期日、付款冻结、现金折扣和重复候选；付款运行、银行主数据及实际扣款仍是独立证据。",
        overview_en="As-of open status, SAP net due date, payment blocks, cash discounts, and duplicate candidates were checked separately; payment-run, bank-master, and actual-debit evidence remain independent.",
        stages=[
            _stage("supplier_items", "供应商行项目", "Supplier items", len(items), state="confirmed" if source_complete else "unknown"),
            _stage("payment_readiness", "付款准备度", "Payment readiness", len(open_items), state="unknown" if missing_due_date else "confirmed"),
            _stage("payment_run", "付款运行与银行证据", "Payment run and bank evidence", 0, state="unknown"),
        ],
        findings=duplicate_findings,
        metrics=[
            {"id": "open_items", "value": len(open_items)},
            {"id": "payment_blocked", "value": blocked},
            {"id": "overdue_items", "value": overdue},
            {"id": "due_today", "value": due_now},
            {"id": "cash_discount_available", "value": discount_available},
            {"id": "duplicate_candidates", "value": len(duplicate_findings)},
        ],
        gaps=sorted(set(gaps)),
        records=records,
        preserve_business_status_on_gap=True,
        source_complete_override=source_complete,
        record_columns=[
            {"key": "purchase_order", "label": {"zh": "采购订单", "en": "Purchase order"}},
            {"key": "accounting_document", "label": {"zh": "会计凭证", "en": "Accounting document"}},
            {"key": "net_due_date", "label": {"zh": "净到期日", "en": "Net due date"}, "format": "date"},
            {"key": "payment_blocking_reason", "label": {"zh": "付款冻结", "en": "Payment block"}},
            {"key": "amount", "label": {"zh": "金额", "en": "Amount"}, "format": "decimal"},
            {"key": "currency", "label": {"zh": "币种", "en": "Currency"}},
            {"key": "payment_readiness", "label": {"zh": "付款准备度", "en": "Payment readiness"}, "format": "status"},
            {"key": "payment_evidence_status", "label": {"zh": "付款证据", "en": "Payment evidence"}, "format": "status"},
        ],
        actions_zh=["按到期日、付款冻结和重复候选复核付款清单；银行证据缺失时不得判断已实际扣款。"],
        actions_en=["Review the payment list by due date, payment block, and duplicate candidates; do not claim an actual bank debit without bank evidence."],
    )
    result["evidence_complete"] = evidence_complete
    result["payment_run_evidence_complete"] = payment_run_evidence_complete
    result["bank_master_evidence_complete"] = bank_master_evidence_complete
    result["bank_settlement_evidence_complete"] = bank_settlement_evidence_complete
    result["scope_results"] = scope_results
    scalar_company_code = str(run_input.get("company_code") or "")
    scalar_supplier = str(run_input.get("supplier") or "")
    if query_mode == "p2p_evidence" and len(scope_results) == 1:
        scalar_company_code = str(scope_results[0].get("company_code") or "")
        scalar_supplier = str(scope_results[0].get("supplier") or "")
    result["workflow_output"] = {
        "query_mode": query_mode,
        "company_code": scalar_company_code,
        "supplier": scalar_supplier,
        "as_of": str(run_input.get("as_of") or ""),
        "scope_results": scope_results,
        "business_status": business_status,
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "payment_run_evidence_complete": payment_run_evidence_complete,
        "bank_master_evidence_complete": bank_master_evidence_complete,
        "bank_settlement_evidence_complete": bank_settlement_evidence_complete,
        "bank_settlement_status": "not_assessed",
        "business_report": result["business_report"],
    }
    return result


def _ap_row_from_p2p(scope: JsonObject, row: JsonObject) -> JsonObject:
    return {
        "ScopeID": _text(scope, "scope_id"),
        "CompanyCode": _text(row, "company_code") or _text(scope, "company_code"),
        "Supplier": _text(row, "supplier") or _text(scope, "supplier"),
        "PurchasingDocument": _text(row, "purchase_order"),
        "PurchasingDocumentItem": _text(row, "purchase_order_item"),
        "FiscalYear": _text(row, "fiscal_year"),
        "AccountingDocument": _text(row, "accounting_document"),
        "AccountingDocumentItem": _text(row, "accounting_document_item"),
        "OriginalReferenceDocument": _text(row, "original_reference_document"),
        "FinancialAccountType": "K",
        "IsOpenItemManaged": True,
        "PostingDate": _text(row, "posting_date"),
        "NetDueDate": _text(row, "net_due_date"),
        "CashDiscount1DueDate": _text(row, "cash_discount_due_date"),
        "PaymentBlockingReason": _text(row, "payment_blocking_reason"),
        "AmountInTransactionCurrency": _text(row, "amount"),
        "TransactionCurrency": _text(row, "currency"),
        "IsCleared": bool(row.get("is_cleared")),
        "ClearingAccountingDocument": _text(row, "clearing_document"),
        "ClearingDocFiscalYear": _text(row, "clearing_fiscal_year"),
        "ClearingDate": _text(row, "clearing_date"),
        "PaymentMethod": _text(row, "payment_method"),
        "ClearingDocumentType": _text(row, "clearing_document_type"),
        "PaymentDocumentStatus": _text(row, "payment_document_status"),
    }


def _ap_duplicate_findings(records: list[JsonObject]) -> list[JsonObject]:
    groups: dict[tuple[str, str, str], list[JsonObject]] = {}
    for row in records:
        amount = _strict_decimal(row.get("amount"))
        key = (_text(row, "supplier"), str(abs(amount)) if amount is not None else "", _text(row, "currency"))
        if all(key):
            groups.setdefault(key, []).append(row)
    findings: list[JsonObject] = []
    for (supplier, amount, currency), rows in groups.items():
        documents = list(dict.fromkeys(_text(row, "accounting_document") for row in rows if _text(row, "accounting_document")))
        dates = [_date(row.get("posting_date")) for row in rows]
        valid_dates = [item for item in dates if item is not None]
        if len(documents) < 2 or len(valid_dates) != len(rows):
            continue
        if (max(valid_dates) - min(valid_dates)).days > 7:
            continue
        findings.append(
            {
                "rule_id": "POTENTIAL_DUPLICATE_PAYMENT",
                "severity": "high",
                "status": "candidate",
                "explanation": {
                    "zh": f"供应商 {supplier} 存在 {len(documents)} 张金额和币种相同、过账日期相近的未清凭证。",
                    "en": f"Supplier {supplier} has {len(documents)} open documents with the same amount and currency posted within seven days.",
                },
                "evidence": {"documents": documents, "amount": amount, "currency": currency},
            }
        )
    return findings


def _ap_scope_results(
    scopes: list[JsonObject],
    records: list[JsonObject],
    source_complete: bool,
    evidence_complete: bool,
) -> list[JsonObject]:
    results: list[JsonObject] = []
    for scope in scopes:
        scope_id = _text(scope, "scope_id")
        company_code = _text(scope, "company_code")
        supplier = _text(scope, "supplier")
        scoped = [
            row
            for row in records
            if (not scope_id or _text(row, "scope_id") == scope_id)
            and (not company_code or _text(row, "company_code") == company_code)
            and (not supplier or _text(row, "supplier") == supplier)
        ]
        blocked = sum(bool(_text(row, "payment_blocking_reason")) for row in scoped)
        unknown = any(_text(row, "payment_readiness") == "unknown_due_date" for row in scoped)
        status = (
            "inconclusive"
            if unknown or not source_complete or not evidence_complete
            else "blocked"
            if blocked
            else "in_progress"
            if scoped
            else "complete"
            if scope.get("fi_supplier_items")
            else "in_progress"
            if scope.get("purchase_orders")
            else "complete"
        )
        results.append(
            {
                "scope_id": scope_id,
                "company_code": company_code,
                "supplier": supplier,
                "purchase_orders": list(scope.get("purchase_orders") or []),
                "business_status": status,
                "open_item_count": len(scoped),
                "payment_blocked_count": blocked,
                "source_complete": bool(scope.get("source_complete", source_complete)),
                "evidence_complete": bool(scope.get("evidence_complete", evidence_complete)),
                "payment_run_evidence_complete": scope.get("payment_run_evidence_complete") is True,
                "bank_master_evidence_complete": scope.get("bank_master_evidence_complete") is True,
                "bank_settlement_evidence_complete": scope.get("bank_settlement_evidence_complete") is True,
                "bank_settlement_status": "not_assessed",
            }
        )
    return results


def _ar_collection_legacy(inputs: JsonObject) -> JsonObject:
    """Keep the still-active 0.1.0 package executable until 1.0.0 is accepted."""

    items = [
        row
        for row in _rows(inputs, "customer_items", "clearing_documents")
        if str(row.get("FinancialAccountType") or "D") == "D"
        and _truthy(row.get("IsOpenItemManaged"))
    ]
    cutoff = _date((inputs.get("run_input") or {}).get("as_of"))
    dunning_master = _rows(inputs, "customer_dunning")
    open_items: list[JsonObject] = []
    records: list[JsonObject] = []
    dunned_items = 0
    historical_dunning_unknown = 0
    for row in items:
        posting_date = _date(row.get("PostingDate"))
        clearing_date = _date(row.get("ClearingDate"))
        if cutoff is not None and posting_date is not None and posting_date > cutoff:
            continue
        if cutoff is not None and clearing_date is not None and clearing_date <= cutoff:
            continue
        open_items.append(row)
        dunning_level = _text(row, "DunningLevel") or "0"
        last_dunning_date = _date(row.get("LastDunningDate"))
        if (
            dunning_level not in {"", "0"}
            and last_dunning_date is not None
            and cutoff is not None
            and last_dunning_date <= cutoff
        ):
            dunning_status = "confirmed_before_cutoff"
            dunned_items += 1
        else:
            dunning_status = "historical_status_unknown"
            historical_dunning_unknown += 1
        records.append(
            {
                "company_code": _text(row, "CompanyCode"),
                "fiscal_year": _text(row, "FiscalYear"),
                "accounting_document": _text(row, "AccountingDocument"),
                "accounting_document_item": _text(row, "AccountingDocumentItem"),
                "ledger": _text(row, "Ledger"),
                "customer": _text(row, "Customer"),
                "posting_date": posting_date.isoformat() if posting_date else "",
                "due_date": _date_text(row, "NetDueDate", "DueCalculationBaseDate"),
                "amount": _text(row, "AmountInTransactionCurrency"),
                "currency": _text(row, "TransactionCurrency"),
                "as_of_status": "open_subsequently_cleared" if clearing_date else "open",
                "clearing_date": clearing_date.isoformat() if clearing_date else "",
                "clearing_document": _text(row, "ClearingAccountingDocument"),
                "dunning_level": dunning_level,
                "last_dunning_date": last_dunning_date.isoformat() if last_dunning_date else "",
                "dunning_blocking_reason": _text(row, "DunningBlockingReason"),
                "dunning_as_of_status": dunning_status,
            }
        )
    current_master_is_later = any(
        cutoff is not None
        and (last_dunned := _date(row.get("LastDunnedOn"))) is not None
        and last_dunned > cutoff
        for row in dunning_master
    )
    gaps = _gaps(inputs)
    if current_master_is_later or historical_dunning_unknown:
        gaps = sorted({*gaps, "historical_dunning_evidence"})
    return _result(
        inputs,
        business_status="inconclusive" if gaps else "attention" if open_items else "complete",
        headline_zh=f"发现 {len(open_items)} 条客户未清应收",
        headline_en=f"Found {len(open_items)} customer open receivable item(s)",
        overview_zh="已按清账日期重建截止日未清状态，并区分逐项催款日期证据与当前客户催款主数据。",
        overview_en="As-of open status was reconstructed from clearing dates; item-level dunning dates remain separate from the current customer dunning master.",
        stages=[
            _stage("receivables", "客户应收", "Customer receivables", len(items)),
            _stage("customer_dunning", "客户催款主数据", "Customer dunning master", len(dunning_master)),
        ],
        metrics=[
            {"id": "open_items", "value": len(open_items)},
            {"id": "dunned_items", "value": dunned_items},
            {"id": "historical_dunning_unknown", "value": historical_dunning_unknown},
        ],
        records=records,
        gaps=gaps,
        actions_zh=["按到期日和金额安排催收；缺少历史催款快照时复核催款日志。"],
        actions_en=["Prioritize collection by due date and amount; review dunning logs when historical snapshots are unavailable."],
    )


def _ar_collection_v1(inputs: JsonObject) -> JsonObject:
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    requested_customers = run_input.get("customers")
    if not isinstance(requested_customers, list):
        requested_customers = [run_input.get("customer")] if run_input.get("customer") else []
    customers = [str(item).strip() for item in requested_customers]
    cutoff = _date(run_input.get("as_of"))
    business_date = _date(run_input.get("business_date"))
    dunning_area = str(run_input.get("dunning_area") or "").strip()
    raw_items = [
        row for row in _rows(inputs, "customer_items")
        if _text(row, "FinancialAccountType") == "D" and _truthy(row.get("IsOpenItemManaged"))
    ]
    clearing_documents = _rows(inputs, "clearing_document_evidence")
    clearing_reversals = _rows(inputs, "clearing_reversal_documents")
    dunning_master = _rows(inputs, "customer_dunning")
    source_complete = _source_complete(inputs)
    conflicts: set[tuple[str, str, str, str, str]] = set()
    canonical: dict[tuple[str, str, str, str, str], JsonObject] = {}
    for row in raw_items:
        key = tuple(_text(row, field) for field in (
            "CompanyCode", "Ledger", "FiscalYear", "AccountingDocument", "AccountingDocumentItem"
        ))
        if not all(key):
            conflicts.add(key)
            continue
        existing = canonical.get(key)
        if existing is not None and existing != row:
            conflicts.add(key)
        else:
            canonical[key] = row

    worklist: list[JsonObject] = []
    customer_results: list[JsonObject] = []
    global_gaps = set(_gaps(inputs))
    ledger_scope = (inputs.get("evidence") or {}).get("ledger_scope") if isinstance(inputs.get("evidence"), dict) else None
    if isinstance(ledger_scope, dict):
        global_gaps.update(str(item) for item in ledger_scope.get("evidence_gaps") or [])
    if conflicts:
        global_gaps.add("fi_business_key_conflict")
    failed_customers = _failed_filter_values(inputs, "customer_items", "customer_dunning")

    def document_rows(
        rows: list[JsonObject],
        *,
        company_code: str,
        ledger: str,
        fiscal_year: str,
        accounting_document: str,
    ) -> list[JsonObject]:
        return [
            row
            for row in rows
            if _text(row, "CompanyCode") == company_code
            and _text(row, "Ledger") == ledger
            and _text(row, "FiscalYear") == fiscal_year
            and _text(row, "AccountingDocument") == accounting_document
        ]

    for customer in customers:
        scoped = [row for row in canonical.values() if _text(row, "Customer") == customer]
        master = [
            row for row in dunning_master
            if _text(row, "Customer") == customer
            and (not dunning_area or _text(row, "DunningArea") == dunning_area)
        ]
        open_rows: list[JsonObject] = []
        customer_gaps = set(global_gaps)
        if customer in failed_customers:
            customer_gaps.add("customer_query_chunk_failed")
        currency_totals: dict[str, Decimal] = {}
        ordinary_overdue: dict[str, Decimal] = {}
        credit_balances: dict[str, Decimal] = {}
        special_gl: list[JsonObject] = []
        for row in scoped:
            posting = _date(row.get("PostingDate"))
            if cutoff is None or posting is None:
                customer_gaps.add("posting_or_cutoff_date_missing")
                continue
            if posting > cutoff:
                continue
            clearing = _date(row.get("ClearingDate"))
            clearing_year = _text(row, "ClearingDocFiscalYear")
            clearing_document = _text(row, "ClearingAccountingDocument")
            clearing_rows = document_rows(
                clearing_documents,
                company_code=_text(row, "CompanyCode"),
                ledger=_text(row, "Ledger"),
                fiscal_year=clearing_year,
                accounting_document=clearing_document,
            ) if clearing_year and clearing_document else []
            reversal_refs = {
                (_text(item, "ReverseDocumentFiscalYear"), _text(item, "ReverseDocument"))
                for item in clearing_rows
                if _text(item, "ReverseDocumentFiscalYear")
                and _text(item, "ReverseDocument")
            }
            clearing_reversed = (
                _truthy(row.get("ClearingIsReversed"))
                or any(_truthy(item.get("IsReversed")) for item in clearing_rows)
                or bool(reversal_refs)
            )
            reversal_date: date | None = None
            historical_open_status = "open"
            if clearing is not None and clearing <= cutoff:
                if not clearing_reversed:
                    continue
                if len(reversal_refs) != 1:
                    customer_gaps.add("historical_clearing_reversal_date_missing")
                    continue
                reversal_year, reversal_document = next(iter(reversal_refs))
                reversal_rows = document_rows(
                    clearing_reversals,
                    company_code=_text(row, "CompanyCode"),
                    ledger=_text(row, "Ledger"),
                    fiscal_year=reversal_year,
                    accounting_document=reversal_document,
                )
                reversal_dates = {
                    parsed
                    for item in reversal_rows
                    if (parsed := _date(item.get("PostingDate"))) is not None
                }
                if len(reversal_dates) != 1:
                    customer_gaps.add("historical_clearing_reversal_date_missing")
                    continue
                reversal_date = next(iter(reversal_dates))
                if reversal_date > cutoff:
                    # Cleared at the requested cutoff; it reopened later.
                    continue
                historical_open_status = "reopened_by_reversal"
            amount = _strict_decimal(row.get("AmountInTransactionCurrency"))
            currency = _text(row, "TransactionCurrency")
            if amount is None or not currency:
                customer_gaps.add("amount_or_currency_missing")
                continue
            due = _date(row.get("NetDueDate") or row.get("DueCalculationBaseDate"))
            overdue_days = max(0, (cutoff - due).days) if due else None
            if due is None:
                aging_bucket = "unknown"
                customer_gaps.add("due_date_missing")
            elif overdue_days == 0:
                aging_bucket = "not_due"
            elif overdue_days <= 30:
                aging_bucket = "1_30"
            elif overdue_days <= 60:
                aging_bucket = "31_60"
            elif overdue_days <= 90:
                aging_bucket = "61_90"
            else:
                aging_bucket = "over_90"
            debit_credit = _text(row, "DebitCreditCode").upper()
            special_code = _text(row, "SpecialGLCode")
            dunning_level = _text(row, "DunningLevel") or "0"
            last_dunning = _date(row.get("LastDunningDate"))
            historical = bool(business_date and cutoff < business_date)
            if historical and (last_dunning is None or last_dunning > cutoff):
                dunning_status = "historical_status_unknown"
                customer_gaps.add("historical_dunning_evidence")
            elif dunning_level not in {"", "0"} and last_dunning and last_dunning <= cutoff:
                dunning_status = "confirmed_before_cutoff"
            else:
                dunning_status = "not_dunned"
            record = {
                "company_code": _text(row, "CompanyCode"),
                "ledger": _text(row, "Ledger"),
                "fiscal_year": _text(row, "FiscalYear"),
                "accounting_document": _text(row, "AccountingDocument"),
                "accounting_document_item": _text(row, "AccountingDocumentItem"),
                "customer": customer,
                "customer_result_status": "found" if scoped or master else "not_found",
                "posting_date": posting.isoformat(),
                "due_date": due.isoformat() if due else None,
                "overdue_days": overdue_days,
                "aging_bucket": aging_bucket,
                "amount": format(amount, "f"),
                "currency": currency,
                "debit_credit_indicator": debit_credit,
                "special_gl_code": special_code or None,
                "clearing_date": clearing.isoformat() if clearing else None,
                "clearing_document": clearing_document or None,
                "clearing_reversal_date": reversal_date.isoformat() if reversal_date else None,
                "historical_open_status": historical_open_status,
                "dunning_level": dunning_level,
                "last_dunning_date": last_dunning.isoformat() if last_dunning else None,
                "dunning_blocking_reason": _text(row, "DunningBlockingReason") or None,
                "dunning_as_of_status": dunning_status,
            }
            open_rows.append(record)
            worklist.append(record)
            currency_totals[currency] = currency_totals.get(currency, Decimal(0)) + amount
            if special_code:
                special_gl.append(record)
            elif debit_credit in {"H", "C", "CREDIT"} or amount < 0:
                credit_balances[currency] = credit_balances.get(currency, Decimal(0)) + amount
            elif overdue_days and overdue_days > 0:
                ordinary_overdue[currency] = ordinary_overdue.get(currency, Decimal(0)) + amount
        # Blank is a real dunning-area key in SAP, not a wildcard.  Retain it
        # while checking cardinality so a blank and a non-blank area cannot be
        # silently collapsed into one customer relationship.
        master_areas = {_text(row, "DunningArea") for row in master}
        if not dunning_area and len(master_areas) > 1:
            customer_gaps.add("dunning_area_ambiguous")
        blocked = any(_text(row, "DunningBlock") for row in master) or any(
            record.get("dunning_blocking_reason") for record in open_rows
        )
        attention = bool(ordinary_overdue or special_gl or blocked)
        status = "inconclusive" if customer_gaps or not source_complete else "attention" if attention else "normal"
        customer_results.append(
            {
                "customer": customer,
                "customer_result_status": (
                    "found" if scoped or master else "no_open_items_or_master_data"
                ),
                "business_status": status,
                "open_item_count": len(open_rows),
                "ordinary_overdue_amounts": {key: format(value, "f") for key, value in sorted(ordinary_overdue.items())},
                "credit_balance_amounts": {key: format(value, "f") for key, value in sorted(credit_balances.items())},
                "special_gl_item_count": len(special_gl),
                "dunning_blocked": blocked,
                "dunning_areas": sorted(master_areas),
                "source_complete": source_complete,
                "evidence_complete": source_complete and not customer_gaps,
                "evidence_gaps": sorted(customer_gaps),
                "items": open_rows,
            }
        )
    order = {"inconclusive": 0, "attention": 1, "normal": 2}
    worklist.sort(
        key=lambda row: (
            0 if row.get("dunning_blocking_reason") else 1,
            -int(row.get("overdue_days") or 0),
            -(_safe_int(row.get("dunning_level")) or 0),
            str(row.get("currency") or ""),
            -abs(_strict_decimal(row.get("amount")) or Decimal(0)),
            str(row.get("fiscal_year") or ""),
            str(row.get("accounting_document") or ""),
            str(row.get("accounting_document_item") or ""),
        )
    )
    customer_results.sort(key=lambda item: (order[item["business_status"]], item["customer"]))
    counts = {status: sum(item["business_status"] == status for item in customer_results) for status in order}
    evidence_complete = source_complete and not global_gaps and all(item["evidence_complete"] for item in customer_results)
    business_status = "inconclusive" if counts["inconclusive"] else "attention" if counts["attention"] else "normal"
    result = _result(
        inputs,
        business_status=business_status,
        headline_zh=f"已检查 {len(customer_results)} 个客户，其中 {counts['attention']} 个需要催收处理",
        headline_en=f"Reviewed {len(customer_results)} customer(s); {counts['attention']} require collection follow-up",
        overview_zh="按截止日重建普通应收、贷方余额和特殊总账项目；当前催收主数据不冒充历史快照。",
        overview_en="Reconstructed ordinary receivables, credit balances, and special G/L items as of the cutoff; current dunning master data is not treated as a historical snapshot.",
        stages=[
            _stage("receivables", "客户应收", "Customer receivables", len(raw_items), state="confirmed" if source_complete else "unknown"),
            _stage("dunning", "催收状态", "Dunning status", len(dunning_master), state="confirmed" if source_complete else "unknown"),
            _stage("worklist", "催收工作清单", "Collection worklist", len(worklist), state="confirmed" if evidence_complete else "unknown"),
        ],
        metrics=[
            {"id": "requested_customer_count", "value": len(customers)},
            {"id": "normal_customer_count", "value": counts["normal"]},
            {"id": "attention_customer_count", "value": counts["attention"]},
            {"id": "inconclusive_customer_count", "value": counts["inconclusive"]},
        ],
        records=worklist,
        allow_empty_records=True,
        gaps=sorted(global_gaps),
        actions_zh=["优先处理冻结、超期天数最长和已确认催收级别最高的项目；特殊总账项目单独人工复核。"],
        actions_en=["Prioritize blocked items, the longest overdue items, and the highest confirmed dunning levels; review special G/L items separately."],
        source_complete_override=source_complete,
        preserve_business_status_on_gap=True,
    )
    extras = {
        "requested_customer_count": len(customers),
        "result_customer_count": len(customer_results),
        "normal_customer_count": counts["normal"],
        "attention_customer_count": counts["attention"],
        "inconclusive_customer_count": counts["inconclusive"],
        "customer_results": customer_results,
        "worklist_artifact": {"name": "ar-collection-worklist.csv", "row_count": len(worklist)},
        "evidence_complete": evidence_complete,
    }
    result.update(extras)
    result["rule_id"] = "ar_collection_deterministic_v2"
    result["business_complete"] = evidence_complete
    result["business_report"]["customer_results"] = customer_results
    result["business_report"]["worklist"] = worklist
    result["business_report"]["action_tables"] = [
        {
            "id": "ar_collection_worklist",
            "title": {"zh": "催收工作清单", "en": "Collection worklist"},
            "artifact_name": "ar-collection-worklist.csv",
            "columns": [
                {"key": "customer", "label": {"zh": "客户", "en": "Customer"}},
                {"key": "accounting_document", "label": {"zh": "财务凭证", "en": "Accounting document"}},
                {"key": "accounting_document_item", "label": {"zh": "行项目", "en": "Item"}},
                {"key": "due_date", "label": {"zh": "到期日", "en": "Due date"}},
                {"key": "overdue_days", "label": {"zh": "逾期天数", "en": "Overdue days"}},
                {"key": "amount", "label": {"zh": "金额", "en": "Amount"}},
                {"key": "currency", "label": {"zh": "币种", "en": "Currency"}},
                {"key": "dunning_level", "label": {"zh": "催收级别", "en": "Dunning level"}},
                {"key": "dunning_blocking_reason", "label": {"zh": "催收冻结", "en": "Dunning block"}},
                {"key": "special_gl_code", "label": {"zh": "特殊总账标识", "en": "Special G/L indicator"}},
            ],
            "rows": worklist,
        }
    ]
    result["workflow_output"].update(extras)
    result["workflow_output"]["business_report"] = result["business_report"]
    for field in run_input:
        result["workflow_output"].pop(str(field), None)
    return result


def _ar_collection(inputs: JsonObject) -> JsonObject:
    run_input = inputs.get("run_input")
    if isinstance(run_input, dict) and isinstance(run_input.get("customers"), list):
        return _ar_collection_v1(inputs)
    return _ar_collection_legacy(inputs)


def _ar_cash_application(inputs: JsonObject) -> JsonObject:
    evidence = inputs.get("evidence") if isinstance(inputs.get("evidence"), dict) else {}
    bank_payloads = [
        payload for name, payload in evidence.items()
        if name.startswith("bank_receipts") and isinstance(payload, dict) and payload.get("status") != "skipped"
    ]
    bank = bank_payloads[0] if bank_payloads else {}
    receipts = [dict(item) for item in bank.get("receipts") or [] if isinstance(item, dict)]
    payment_rows = _rows(inputs, "subledger_payment_documents")
    direct_invoice_rows = _rows(inputs, "directly_cleared_invoices")
    subsequent_clearing_rows = _rows(inputs, "subsequent_clearing_documents")
    subsequent_invoice_rows = _rows(inputs, "subsequently_cleared_invoices")
    fi_rows = [*payment_rows, *direct_invoice_rows, *subsequent_clearing_rows, *subsequent_invoice_rows]
    source_complete = bool(
        bank.get("status") == "complete"
        and isinstance(bank.get("completeness"), dict)
        and bank["completeness"].get("source_complete") is True
        and _source_complete(inputs)
    )
    gaps = set(_gaps(inputs))
    cash_scope = evidence.get("cash_scope") if isinstance(evidence, dict) else None
    if isinstance(cash_scope, dict):
        gaps.update(str(item) for item in cash_scope.get("evidence_gaps") or [])
    resolved_ledger = str(cash_scope.get("ledger") or "").strip() if isinstance(cash_scope, dict) else ""

    def with_leading_ledger(rows: list[JsonObject]) -> list[JsonObject]:
        normalized: list[JsonObject] = []
        for source_row in rows:
            row = dict(source_row)
            source_ledger = _text(row, "Ledger")
            if not source_ledger and resolved_ledger:
                row["Ledger"] = resolved_ledger
            elif source_ledger and resolved_ledger and source_ledger != resolved_ledger:
                gaps.add("fi_nonleading_ledger_row")
                continue
            normalized.append(row)
        return normalized

    payment_rows = with_leading_ledger(payment_rows)
    direct_invoice_rows = with_leading_ledger(direct_invoice_rows)
    subsequent_clearing_rows = with_leading_ledger(subsequent_clearing_rows)
    subsequent_invoice_rows = with_leading_ledger(subsequent_invoice_rows)
    fi_rows = [*payment_rows, *direct_invoice_rows, *subsequent_clearing_rows, *subsequent_invoice_rows]
    if bank.get("status") == "partial":
        gaps.add("bank_receipt_source_partial")
        receipts = []
    key_map: dict[tuple[str, str, str, str, str], JsonObject] = {}
    for row in fi_rows:
        key = tuple(_text(row, field) for field in (
            "CompanyCode", "Ledger", "FiscalYear", "AccountingDocument", "AccountingDocumentItem"
        ))
        existing = key_map.get(key)
        if not all(key) or (existing is not None and existing != row):
            gaps.add("fi_business_key_conflict")
        else:
            key_map[key] = row
    results: list[JsonObject] = []
    for receipt in receipts:
        related = receipt.get("related_accounting_document") if isinstance(receipt.get("related_accounting_document"), dict) else {}
        document = str(related.get("subledger_document") or "")
        year = str(related.get("fiscal_year") or "")
        active = receipt.get("posting_status") == "completed" and receipt.get("reversal_status") == "not_reversed"
        company_code = str((inputs.get("run_input") or {}).get("company_code") or "")
        ledger = resolved_ledger
        matching = [row for row in payment_rows if _text(row, "CompanyCode") == company_code and _text(row, "Ledger") == ledger and _text(row, "AccountingDocument") == document and _text(row, "FiscalYear") == year]
        customer_lines = [row for row in matching if _text(row, "FinancialAccountType") == "D" and _text(row, "Customer")]
        customers = {_text(row, "Customer") for row in customer_lines}
        special = [row for row in customer_lines if _text(row, "SpecialGLCode")]
        ordinary = [row for row in customer_lines if not _text(row, "SpecialGLCode")]
        direct_invoices = [
            row for row in direct_invoice_rows
            if _text(row, "ClearingAccountingDocument") == document
            and _text(row, "ClearingDocFiscalYear") == year
            and _text(row, "FinancialAccountType") == "D"
            and not _text(row, "SpecialGLCode")
        ]
        later_refs = {
            (_text(row, "ClearingDocFiscalYear"), _text(row, "ClearingAccountingDocument"))
            for row in matching
            if _text(row, "ClearingAccountingDocument")
        }
        relationship_ambiguous = any(not clearing_year for clearing_year, _clearing_doc in later_refs)
        later_refs = {(clearing_year, clearing_doc) for clearing_year, clearing_doc in later_refs if clearing_year}
        if (year, document) in later_refs:
            relationship_ambiguous = True
        later_documents = {
            (_text(row, "FiscalYear"), _text(row, "AccountingDocument"))
            for row in subsequent_clearing_rows
            if _text(row, "CompanyCode") == company_code and _text(row, "Ledger") == ledger
        }
        if later_refs and not later_refs.issubset(later_documents):
            relationship_ambiguous = True
        multihop_invoices = [
            row for row in subsequent_invoice_rows
            if (_text(row, "ClearingDocFiscalYear"), _text(row, "ClearingAccountingDocument")) in later_refs
            and _text(row, "FinancialAccountType") == "D"
            and not _text(row, "SpecialGLCode")
        ]
        confirmed_invoices = [*direct_invoices, *multihop_invoices]
        if customers and any(_text(row, "Customer") not in customers for row in confirmed_invoices):
            relationship_ambiguous = True
        if not active:
            cash_status = "not_assessed"
            business = "attention"
            reversal_status = str(receipt.get("reversal_status") or "unknown")
            posting_status = str(receipt.get("posting_status") or "unknown")
            reason = (
                reversal_status
                if reversal_status != "not_reversed"
                else posting_status
                if posting_status != "completed"
                else "not_completed"
            )
        elif not document or not year or not matching:
            cash_status = "pending"
            business = "attention" if source_complete else "inconclusive"
            reason = "customer_subledger_document_missing"
        elif len(customer_lines) != 1 or len(customers) != 1 or relationship_ambiguous:
            cash_status = "ambiguous"
            business = "attention"
            reason = (
                "customer_subledger_line_not_unique"
                if len(customer_lines) != 1
                else "multiple_customers_in_payment_document"
                if len(customers) > 1
                else "clearing_relationship_ambiguous"
            )
        elif not ordinary and special:
            cash_status = "pending"
            business = "attention"
            reason = "special_gl_only"
        elif confirmed_invoices:
            cash_status = "confirmed"
            business = "normal"
            reason = "sap_clearing_relationship_confirmed"
        else:
            receipt_amount = abs(_strict_decimal(receipt.get("amount")) or Decimal(0))
            currency = str(receipt.get("currency") or "")
            candidate_rows = [
                row for row in fi_rows
                if _text(row, "FinancialAccountType") == "D"
                and not _text(row, "SpecialGLCode")
                and _text(row, "Customer") in customers
                and not (
                    _text(row, "AccountingDocument") == document
                    and _text(row, "FiscalYear") == year
                )
                and _text(row, "TransactionCurrency") == currency
                and abs(_strict_decimal(row.get("AmountInTransactionCurrency")) or Decimal(0)) == receipt_amount
                and (_date(row.get("PostingDate")) or date.max) <= (_date(receipt.get("value_date")) or date.min)
            ]
            cash_status = "candidate" if len(candidate_rows) == 1 else "ambiguous" if len(candidate_rows) > 1 else "not_found"
            business = "attention"
            reason = "unique_amount_candidate" if len(candidate_rows) == 1 else "multiple_candidates" if candidate_rows else "no_relationship_or_candidate"
        results.append(
            {
                "company_code": company_code,
                "statement_id": receipt.get("statement_id"),
                "statement_item": receipt.get("statement_item"),
                "value_date": receipt.get("value_date"),
                "amount": receipt.get("amount"),
                "currency": receipt.get("currency"),
                "posting_status": receipt.get("posting_status"),
                "reversal_status": receipt.get("reversal_status"),
                "subledger_document": document or None,
                "fiscal_year": year or None,
                "customer": next(iter(customers), None) if len(customers) == 1 else None,
                "cash_application_status": cash_status,
                "business_status": business,
                "reason_code": reason,
                "confirmed_invoice_count": len(confirmed_invoices),
                "special_gl_item_count": len(special),
            }
        )
    requested_scope = bank.get("requested_scope") if isinstance(bank.get("requested_scope"), dict) else {}
    specified_reference = requested_scope.get("receipt_reference_supplied") is True
    if not receipts and bank.get("status") == "complete":
        receipt_search_status = "not_found"
        business_status = "attention" if specified_reference else "normal"
    elif bank.get("status") == "partial":
        receipt_search_status = "partial"
        business_status = "inconclusive"
    elif not bank:
        receipt_search_status = "unavailable"
        business_status = "inconclusive"
    else:
        receipt_search_status = "found"
        business_status = "inconclusive" if gaps or any(item["business_status"] == "inconclusive" for item in results) else "attention" if any(item["business_status"] == "attention" for item in results) else "normal"
    statuses = {str(item["cash_application_status"]) for item in results}
    # The aggregate status is an actionable precedence, not an assertion that
    # heterogeneous receipt states make the FI relationship ambiguous.
    # ``ambiguous`` is reserved for a receipt with conflicting customers,
    # business keys, or more than one candidate relationship.
    cash_status = next(
        (
            status
            for status in (
                "unknown",
                "ambiguous",
                "pending",
                "candidate",
                "not_found",
                "not_assessed",
                "confirmed",
            )
            if status in statuses
        ),
        "unknown" if business_status == "inconclusive" else "not_assessed",
    )
    evidence_complete = source_complete and not gaps
    result = _result(
        inputs,
        business_status=business_status,
        headline_zh=f"找到 {len(results)} 笔银行来款，其中 {sum(item['cash_application_status'] == 'confirmed' for item in results)} 笔已由SAP清账关系确认",
        headline_en=f"Found {len(results)} bank receipt(s); {sum(item['cash_application_status'] == 'confirmed' for item in results)} are confirmed by SAP clearing relationships",
        overview_zh="银行到账事实、客户子分类账和发票清账关系分开核验；候选匹配不会显示为已清账，FI清账不是独立银行到账证据。",
        overview_en="Bank-receipt facts, customer subledger evidence, and invoice clearing relationships are assessed separately; candidates are never shown as cleared, and FI clearing is not independent bank settlement evidence.",
        stages=[
            _stage("bank", "银行来款", "Bank receipts", len(results), state="confirmed" if receipt_search_status in {"found", "not_found"} else "unknown"),
            _stage("subledger", "客户子分类账", "Customer subledger", len(fi_rows), state="confirmed" if source_complete else "unknown"),
            _stage("application", "来款与发票关系", "Receipt-to-invoice relationship", len(results), state="confirmed" if evidence_complete else "unknown"),
        ],
        records=results,
        allow_empty_records=True,
        gaps=sorted(gaps),
        actions_zh=["对待处理、候选或关系不明确的来款，由应收岗位复核后在SAP中执行后续处理。"],
        actions_en=["Have AR staff review pending, candidate, or ambiguous receipts before any subsequent SAP processing."],
        source_complete_override=source_complete,
        preserve_business_status_on_gap=True,
    )
    extras = {
        "source_receipt_count": bank.get("completeness", {}).get("total_rows") if isinstance(bank.get("completeness"), dict) else None,
        "materialized_receipt_count": len(results) if bank.get("status") == "complete" else None,
        "unresolved_receipt_count": sum(item["cash_application_status"] not in {"confirmed"} for item in results) if bank.get("status") == "complete" else None,
        "confirmed_receipt_count": sum(item["cash_application_status"] == "confirmed" for item in results) if bank.get("status") == "complete" else None,
        "attention_receipt_count": sum(item["business_status"] == "attention" for item in results) if bank.get("status") == "complete" else None,
        "inconclusive_receipt_count": sum(item["business_status"] == "inconclusive" for item in results) if bank.get("status") == "complete" else None,
        "receipt_search_status": receipt_search_status,
        "cash_application_status": cash_status,
        "receipt_results": results,
        "restricted_detail_artifact": bank.get("restricted_artifact_ref"),
        "evidence_complete": evidence_complete,
    }
    result.update(extras)
    result["rule_id"] = "ar_cash_application_deterministic_v1"
    result["business_complete"] = evidence_complete
    result["workflow_output"].update(extras)
    result["workflow_output"]["business_report"] = result["business_report"]
    for field in (inputs.get("run_input") or {}):
        result["workflow_output"].pop(str(field), None)
    return result


def _grir(inputs: JsonObject) -> JsonObject:
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    pos = _rows(inputs, "purchase_order_items", "purchase_orders")
    receipts = _rows(inputs, "material_documents")
    receipt_headers = _rows(inputs, "material_document_headers")
    invoices = _rows(inputs, "supplier_invoice_items")
    invoice_headers = _rows(inputs, "supplier_invoice_headers")
    candidate_gl = _rows(inputs, "gl_items")
    full_gl = _rows(inputs, "grir_gl_history")
    if not full_gl:
        full_gl = candidate_gl
    analysis_date = _date(run_input.get("date_to")) or date.today()
    required_steps = [
        "gl_items",
        "grir_gl_history",
        "purchase_order_items",
        "material_documents",
        "material_document_headers",
        "supplier_invoice_items",
        "supplier_invoice_headers",
    ]
    incomplete_steps = [
        f"{step_id}_incomplete"
        for step_id in required_steps
        if not _step_source_complete(inputs, step_id)
    ]
    source_complete = not incomplete_steps and _source_complete(inputs)
    analysis = evaluate_odata_grir(
        analysis_date=analysis_date,
        po_items=pos,
        material_documents=receipts,
        material_document_headers=receipt_headers,
        supplier_invoice_items=invoices,
        supplier_invoice_headers=invoice_headers,
        gl_items=full_gl,
        candidate_gl_items=candidate_gl,
        source_complete=source_complete,
        incomplete_steps=incomplete_steps,
    )
    follow_up_count = int(analysis["follow_up_item_count"])
    unknown_count = int(analysis["unknown_item_count"])
    matched_count = int(analysis["matched_item_count"])
    business_status = str(analysis["business_status"])
    if business_status == "normal":
        headline_zh = f"已核对 {analysis['examined_item_count']} 个采购项目，当前范围没有需要后续处理的GR/IR差异"
        headline_en = f"Reconciled {analysis['examined_item_count']} purchase-order item(s); no GR/IR follow-up is required in the current scope"
    elif business_status == "attention":
        headline_zh = f"发现 {follow_up_count} 个需要后续处理的GR/IR项目"
        headline_en = f"Found {follow_up_count} GR/IR item(s) requiring follow-up"
    else:
        headline_zh = f"已确认 {follow_up_count} 个待处理项目，另有 {unknown_count} 个项目因证据限制无法确认"
        headline_en = f"Confirmed {follow_up_count} follow-up item(s); {unknown_count} additional item(s) remain inconclusive because of evidence limits"
    metrics = [
        {"id": "examined_item_count", "label": {"zh": "已检查项目", "en": "Items examined"}, "value": analysis["examined_item_count"]},
        {"id": "matched_item_count", "label": {"zh": "已匹配", "en": "Matched"}, "value": matched_count},
        {"id": "follow_up_item_count", "label": {"zh": "确认需处理", "en": "Confirmed follow-up"}, "value": follow_up_count},
        {"id": "unknown_item_count", "label": {"zh": "无法确认", "en": "Inconclusive"}, "value": unknown_count},
    ]
    result = _result(
        inputs,
        business_status=business_status,
        headline_zh=headline_zh,
        headline_en=headline_en,
        overview_zh="系统按采购订单项目核对净收货数量、净发票数量、GR/IR总账净余额、冲销、币种和账龄；前台只显示确认需处理和证据不足的项目。",
        overview_en="The rule reconciles net receipt quantity, net invoice quantity, GR/IR G/L balance, reversals, currency, and aging by purchase-order item; the page shows only confirmed follow-up and inconclusive items.",
        stages=[
            _stage("candidate", "候选项目发现", "Candidate discovery", len(candidate_gl), state="confirmed" if _step_source_complete(inputs, "gl_items") else "unknown"),
            _stage("purchase_order", "采购订单项目", "Purchase-order items", len(pos), state="confirmed" if _step_source_complete(inputs, "purchase_order_items") else "unknown"),
            _stage("receipt", "收货、退货与冲销", "Receipts, returns, and reversals", len(receipts), state="confirmed" if _step_source_complete(inputs, "material_documents") else "unknown"),
            _stage("receipt_header", "物料凭证过账日期", "Material-document posting dates", len(receipt_headers), state="confirmed" if _step_source_complete(inputs, "material_document_headers") else "unknown"),
            _stage("invoice", "供应商发票与贷项", "Supplier invoices and credits", len(invoices), state="confirmed" if _step_source_complete(inputs, "supplier_invoice_items") else "unknown"),
            _stage("gl", "GR/IR总账历史", "GR/IR G/L history", len(full_gl), state="confirmed" if _step_source_complete(inputs, "grir_gl_history") or (not _rows(inputs, "grir_gl_history") and _step_source_complete(inputs, "gl_items")) else "unknown"),
        ],
        metrics=metrics,
        gaps=list(analysis["evidence_gaps"]),
        limitations=(
            ["当前查询或证据存在范围限制；已确认异常仍被保留，但当前结果不能代表全部GR/IR项目。"]
            if business_status == "inconclusive"
            else []
        ),
        actions_zh=(["按严重程度、账龄和未清金额处理待办清单；只有确认不会再发生后续业务并完成审批后才评估MR11。"] if follow_up_count else []),
        actions_en=(["Process the follow-up list by severity, age, and open amount; assess MR11 only after confirming no further business and completing approval."] if follow_up_count else []),
        source_complete_override=bool(analysis["source_complete"]),
        records=[],
        allow_empty_records=True,
        preserve_business_status_on_gap=True,
    )
    columns = [
        {"key": "purchase_order", "label": {"zh": "采购订单", "en": "Purchase order"}},
        {"key": "purchase_order_item", "label": {"zh": "项目", "en": "Item"}},
        {"key": "material", "label": {"zh": "物料", "en": "Material"}},
        {"key": "receipt_quantity", "label": {"zh": "净收货数量", "en": "Net receipt quantity"}, "format": "decimal"},
        {"key": "invoice_quantity", "label": {"zh": "净发票数量", "en": "Net invoice quantity"}, "format": "decimal"},
        {"key": "unit", "label": {"zh": "单位", "en": "Unit"}},
        {"key": "gr_ir_open_amount", "label": {"zh": "GR/IR未平金额", "en": "GR/IR open amount"}, "format": "decimal"},
        {"key": "currency", "label": {"zh": "币种", "en": "Currency"}},
        {"key": "oldest_open_date", "label": {"zh": "最早未平日期", "en": "Oldest open date"}, "format": "date"},
        {"key": "age_days", "label": {"zh": "账龄天数", "en": "Age in days"}, "format": "integer"},
        {"key": "primary_reason", "label": {"zh": "主要原因", "en": "Primary reason"}, "format": "status"},
        {"key": "severity", "label": {"zh": "优先级", "en": "Priority"}, "format": "status"},
        {"key": "responsible_team", "label": {"zh": "建议责任方", "en": "Suggested owner"}},
        {"key": "recommended_action", "label": {"zh": "建议动作", "en": "Recommended action"}},
        {"key": "material_document_refs", "label": {"zh": "物料凭证", "en": "Material documents"}},
        {"key": "supplier_invoice_refs", "label": {"zh": "供应商发票", "en": "Supplier invoices"}},
        {"key": "accounting_document_refs", "label": {"zh": "财务凭证", "en": "Accounting documents"}},
    ]
    result["business_report"]["action_tables"] = [
        {
            "id": "confirmed_follow_up",
            "title": {"zh": "已确认需要处理", "en": "Confirmed follow-up"},
            "columns": columns,
            "rows": analysis["action_records"],
            "total_rows": follow_up_count,
            "source_complete": bool(analysis["source_complete"]),
            "artifact_name": "gr-ir-follow-up.csv",
            "display": True,
            "empty_state": {"zh": "当前范围没有已确认的待处理项目。", "en": "No confirmed follow-up item was found in the current scope."},
        },
        {
            "id": "needs_confirmation",
            "title": {"zh": "证据不足，需要确认", "en": "Evidence incomplete; confirmation required"},
            "columns": columns,
            "rows": analysis["unknown_records"],
            "total_rows": unknown_count,
            "source_complete": bool(analysis["source_complete"]),
            "artifact_name": "gr-ir-needs-confirmation.csv",
            "display": True,
            "empty_state": {"zh": "没有因证据不足而无法判断的项目。", "en": "No item remains inconclusive because of missing evidence."},
        },
        {
            "id": "all_reconciliation_records",
            "title": {"zh": "全部核对记录", "en": "All reconciliation records"},
            "columns": columns,
            "rows": analysis["records"],
            "total_rows": analysis["examined_item_count"],
            "source_complete": bool(analysis["source_complete"]),
            "artifact_name": "gr-ir-all-records.csv",
            "display": False,
            "acceptance_records": True,
        },
    ]
    result["rule_id"] = "gr_ir_clearing_deterministic_v2"
    result["status"] = "complete" if business_status in {"normal", "attention"} else "inconclusive"
    result["business_status"] = business_status
    result["business_complete"] = bool(analysis["evidence_complete"])
    result["source_complete"] = bool(analysis["source_complete"])
    result["workflow_output"].update(
        {
            "analysis_date": analysis_date.isoformat(),
            "examined_item_count": analysis["examined_item_count"],
            "matched_item_count": matched_count,
            "follow_up_item_count": follow_up_count,
            "unknown_item_count": unknown_count,
            "source_complete": bool(analysis["source_complete"]),
            "evidence_complete": bool(analysis["evidence_complete"]),
            "business_status": business_status,
            "action_required_records": analysis["action_records"],
            "business_report": result["business_report"],
        }
    )
    if business_status == "inconclusive":
        result["business_report"]["tone"] = "info"
    return result


def _month_end(inputs: JsonObject) -> JsonObject:
    return evaluate_month_end_closing(inputs)


def _legacy_demand_forecast_single(inputs: JsonObject) -> JsonObject:
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    context = inputs.get("analysis_context")
    context = context if isinstance(context, dict) else {}
    material = str(run_input.get("material") or "").strip()
    plant = str(run_input.get("plant") or "").strip()
    date_from = _date(context.get("date_from") or run_input.get("date_from"))
    date_to = _date(context.get("date_to") or run_input.get("date_to"))
    threshold = _strict_decimal(context.get("deviation_threshold_percent")) or Decimal("20")
    manual_requested = context.get("manual_demand_requested") is True

    topic_names = ["pir", "sales_demand", "planned_orders"]
    if manual_requested:
        topic_names.extend(["current_stock", "supply_demand"])
    source_flags = {topic: _topic_complete(inputs, topic) for topic in topic_names}
    gaps: list[str] = [
        f"{topic}_evidence" for topic, complete in source_flags.items() if not complete
    ]

    pir_headers = _rows(inputs, "pir_headers")
    pir_items = _rows(inputs, "pir_items")
    sales_items = _rows(inputs, "sales_order_items")
    schedule_lines = _rows(inputs, "sales_schedule_lines")
    planned_orders = _rows(inputs, "planned_orders")
    stock_rows = _rows(inputs, "current_stock") if manual_requested else []
    mrp_rows = (
        _rows(inputs, "supply_demand", "supply_demand_items")
        if manual_requested
        else []
    )

    def exact_material_plant(row: JsonObject, material_field: str, plant_field: str) -> bool:
        return (
            str(row.get(material_field) or "").strip() == material
            and str(row.get(plant_field) or "").strip() == plant
        )

    scope_fields = (
        "Product",
        "Plant",
        "MRPArea",
        "PlndIndepRqmtType",
        "PlndIndepRqmtVersion",
        "RequirementPlan",
        "RequirementSegment",
    )

    def scope_key(row: JsonObject) -> tuple[str, ...]:
        return tuple(str(row.get(field) or "").strip() for field in scope_fields)

    selectors = {
        "MRPArea": str(context.get("mrp_area") or "").strip(),
        "PlndIndepRqmtType": str(context.get("pir_requirement_type") or "").strip(),
        "PlndIndepRqmtVersion": str(context.get("pir_version") or "00").strip(),
        "RequirementPlan": str(context.get("requirement_plan") or "").strip(),
        "RequirementSegment": str(context.get("requirement_segment") or "").strip(),
    }

    def matches_selectors(row: JsonObject) -> bool:
        if not exact_material_plant(row, "Product", "Plant"):
            return False
        for field, expected in selectors.items():
            if expected and str(row.get(field) or "").strip() != expected:
                return False
        return True

    active_headers = [
        row
        for row in pir_headers
        if matches_selectors(row) and _truthy(row.get("PlndIndepRqmtIsActive"))
    ]
    active_scopes = {scope_key(row) for row in active_headers}
    scoped_pir_items = [
        row
        for row in pir_items
        if matches_selectors(row) and scope_key(row) in active_scopes
    ]
    observed_scopes = sorted({scope_key(row) for row in scoped_pir_items})
    if len(observed_scopes) > 1:
        gaps.append("pir_scope_ambiguous")
    selected_scope = observed_scopes[0] if len(observed_scopes) == 1 else None
    if selected_scope is not None:
        scoped_pir_items = [row for row in scoped_pir_items if scope_key(row) == selected_scope]
    elif observed_scopes:
        scoped_pir_items = []

    def pir_period(row: JsonObject) -> tuple[date, date] | None:
        period_type = str(row.get("PeriodType") or "").strip().upper()
        start = _date(row.get("PlndIndepRqmtPeriodStartDate") or row.get("WorkingDayDate"))
        raw_period = str(row.get("PlndIndepRqmtPeriod") or "").strip()
        if start is None:
            try:
                if period_type == "M" and re.fullmatch(r"[0-9]{6}", raw_period):
                    start = date(int(raw_period[:4]), int(raw_period[4:]), 1)
                elif period_type == "W" and re.fullmatch(r"[0-9]{6}", raw_period):
                    start = date.fromisocalendar(int(raw_period[:4]), int(raw_period[4:]), 1)
                elif period_type in {"D", "T"} and re.fullmatch(r"[0-9]{8}", raw_period):
                    start = date.fromisoformat(
                        f"{raw_period[:4]}-{raw_period[4:6]}-{raw_period[6:]}"
                    )
            except ValueError:
                start = None
        if start is None:
            return None
        if period_type == "M":
            end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
        elif period_type == "W":
            end = date.fromordinal(start.toordinal() + 6)
        elif period_type in {"D", "T"}:
            end = start
        else:
            return None
        return start, end

    period_map: dict[tuple[str, str, str, str], JsonObject] = {}
    for row in scoped_pir_items:
        bounds = pir_period(row)
        quantity = _strict_decimal(row.get("PlannedQuantity"))
        withdrawal = _strict_decimal(row.get("WithdrawalQuantity"))
        unit = _unit_key(row.get("UnitOfMeasure"))
        if bounds is None or quantity is None or not unit:
            gaps.append("pir_period_evidence")
            continue
        start, end = bounds
        if date_from and end < date_from or date_to and start > date_to:
            continue
        key = (
            str(row.get("PlndIndepRqmtPeriod") or start.isoformat()),
            str(row.get("PeriodType") or "").strip().upper(),
            start.isoformat(),
            end.isoformat(),
        )
        record = period_map.setdefault(
            key,
            {
                "period": key[0],
                "period_type": key[1],
                "period_start": key[2],
                "period_end": key[3],
                "sales_demand_quantity": Decimal(0),
                "pir_quantity": Decimal(0),
                "pir_withdrawal_quantity": Decimal(0),
                "planned_order_quantity": Decimal(0),
                "units": set(),
            },
        )
        record["pir_quantity"] += quantity
        record["pir_withdrawal_quantity"] += withdrawal or Decimal(0)
        record["units"].add(unit)

    item_by_key = {
        (str(row.get("SalesOrder") or "").strip(), str(row.get("SalesOrderItem") or "").strip()): row
        for row in sales_items
        if exact_material_plant(row, "Material", "ProductionPlant")
    }
    sales_evidence_rows: list[JsonObject] = []
    for row in schedule_lines:
        key = (str(row.get("SalesOrder") or "").strip(), str(row.get("SalesOrderItem") or "").strip())
        item = item_by_key.get(key)
        if not item or str(item.get("SalesDocumentRjcnReason") or "").strip():
            continue
        demand_date = _date(row.get("RequestedDeliveryDate"))
        quantity = _strict_decimal(row.get("ScheduleLineOrderQuantity"))
        unit = _unit_key(row.get("OrderQuantityUnit") or item.get("RequestedQuantityUnit"))
        if demand_date is None or quantity is None or not unit:
            gaps.append("sales_demand_period_evidence")
            continue
        if date_from and demand_date < date_from or date_to and demand_date > date_to:
            continue
        matches = [
            value
            for value in period_map.values()
            if date.fromisoformat(value["period_start"])
            <= demand_date
            <= date.fromisoformat(value["period_end"])
        ]
        if len(matches) > 1:
            gaps.append("pir_period_overlap")
            continue
        if not matches:
            synthetic_key = (demand_date.isoformat(), "D", demand_date.isoformat(), demand_date.isoformat())
            matches = [
                period_map.setdefault(
                    synthetic_key,
                    {
                        "period": demand_date.isoformat(),
                        "period_type": "D",
                        "period_start": demand_date.isoformat(),
                        "period_end": demand_date.isoformat(),
                        "sales_demand_quantity": Decimal(0),
                        "pir_quantity": Decimal(0),
                        "pir_withdrawal_quantity": Decimal(0),
                        "planned_order_quantity": Decimal(0),
                        "units": set(),
                    },
                )
            ]
        matches[0]["sales_demand_quantity"] += quantity
        matches[0]["units"].add(unit)
        sales_evidence_rows.append(
            {
                "sales_order": key[0],
                "sales_order_item": key[1],
                "schedule_line": str(row.get("ScheduleLine") or "").strip(),
                "requirement_date": demand_date.isoformat(),
                "quantity": format(quantity, "f"),
                "unit": unit,
            }
        )

    planned_evidence_rows: list[JsonObject] = []
    for row in planned_orders:
        if not exact_material_plant(row, "Material", "ProductionPlant"):
            continue
        planned_date = _date(
            row.get("PlndOrderPlannedEndDate")
            or row.get("PlndOrderPlannedStartDate")
        )
        quantity = _strict_decimal(row.get("TotalQuantity"))
        unit = _unit_key(row.get("BaseUnit"))
        if planned_date is None or quantity is None or not unit:
            gaps.append("planned_order_period_evidence")
            continue
        if date_from and planned_date < date_from or date_to and planned_date > date_to:
            continue
        matches = [
            value
            for value in period_map.values()
            if date.fromisoformat(value["period_start"])
            <= planned_date
            <= date.fromisoformat(value["period_end"])
        ]
        if not matches:
            synthetic_key = (
                planned_date.isoformat(),
                "D",
                planned_date.isoformat(),
                planned_date.isoformat(),
            )
            matches = [
                period_map.setdefault(
                    synthetic_key,
                    {
                        "period": planned_date.isoformat(),
                        "period_type": "D",
                        "period_start": planned_date.isoformat(),
                        "period_end": planned_date.isoformat(),
                        "sales_demand_quantity": Decimal(0),
                        "pir_quantity": Decimal(0),
                        "pir_withdrawal_quantity": Decimal(0),
                        "planned_order_quantity": Decimal(0),
                        "units": set(),
                    },
                )
            ]
        if len(matches) == 1:
            matches[0]["planned_order_quantity"] += quantity
            matches[0]["units"].add(unit)
        elif len(matches) > 1:
            gaps.append("pir_period_overlap")
        planned_evidence_rows.append(
            {
                "planned_order": str(row.get("PlannedOrder") or "").strip(),
                "planned_date": planned_date.isoformat(),
                "quantity": format(quantity, "f"),
                "unit": unit,
                "is_firm": _truthy(row.get("PlannedOrderIsFirm")),
            }
        )

    period_results: list[JsonObject] = []
    forecast_states: list[str] = []
    sales_total = Decimal(0)
    pir_total = Decimal(0)
    withdrawal_total = Decimal(0)
    planned_total = Decimal(0)
    for record in sorted(period_map.values(), key=lambda item: (item["period_start"], item["period"])):
        units = sorted(record.pop("units"))
        sales_quantity = record["sales_demand_quantity"]
        pir_quantity = record["pir_quantity"]
        planned_quantity = record["planned_order_quantity"]
        withdrawal_quantity = record["pir_withdrawal_quantity"]
        variance: Decimal | None = None
        variance_percent: Decimal | None = None
        if len(units) != 1:
            status = "unknown"
            gaps.append("demand_unit_not_comparable")
        elif pir_quantity == 0 and sales_quantity > 0:
            status = "pir_missing"
            variance = sales_quantity
        elif pir_quantity == 0 and sales_quantity == 0:
            status = "no_activity"
            variance = Decimal(0)
        else:
            variance = sales_quantity - pir_quantity
            variance_percent = variance / pir_quantity * Decimal(100)
            if abs(variance_percent) <= threshold:
                status = "within_tolerance"
            elif variance > 0:
                status = "over_forecast"
            else:
                status = "under_forecast"
        forecast_states.append(status)
        sales_total += sales_quantity
        pir_total += pir_quantity
        withdrawal_total += withdrawal_quantity
        planned_total += planned_quantity
        period_results.append(
            {
                **record,
                "material": material,
                "plant": plant,
                "sales_demand_quantity": format(sales_quantity, "f"),
                "pir_quantity": format(pir_quantity, "f"),
                "pir_withdrawal_quantity": format(withdrawal_quantity, "f"),
                "planned_order_quantity": format(planned_quantity, "f"),
                "unit": units[0] if len(units) == 1 else None,
                "variance_quantity": format(variance, "f") if variance is not None else None,
                "variance_percent": (
                    format(variance_percent.quantize(Decimal("0.01")), "f")
                    if variance_percent is not None
                    else None
                ),
                "status": status,
            }
        )

    if not period_results:
        forecast_status = "no_activity"
    elif "unknown" in forecast_states:
        forecast_status = "unknown"
    elif "pir_missing" in forecast_states:
        forecast_status = "pir_missing"
    elif all(state == "no_activity" for state in forecast_states):
        forecast_status = "no_activity"
    else:
        directional = {state for state in forecast_states if state in {"over_forecast", "under_forecast"}}
        forecast_status = (
            "mixed"
            if len(directional) > 1
            else next(iter(directional))
            if directional
            else "within_tolerance"
        )

    manual_quantity = _strict_decimal(context.get("manual_demand_quantity"))
    manual_date = _date(context.get("manual_demand_date"))
    manual_unit_input = _unit_key(context.get("manual_demand_unit"))
    current_stock: Decimal | None = None
    projected_before: Decimal | None = None
    projected_after: Decimal | None = None
    existing_demand: Decimal | None = None
    future_receipts: Decimal | None = None
    lowest_simulated: Decimal | None = None
    first_shortage_date: str | None = None
    manual_status = "not_requested"
    horizon_status = "not_requested"
    manual_unit: str | None = None
    normalized_supply_demand: list[JsonObject] = []
    category_sign_fallback_used = False

    if manual_requested:
        exact_stock = [
            row
            for row in stock_rows
            if exact_material_plant(row, "Material", "Plant")
            and str(row.get("InventoryStockType") or "").strip() == "01"
            and not str(row.get("InventorySpecialStockType") or "").strip()
        ]
        exact_mrp = [
            row
            for row in mrp_rows
            if exact_material_plant(row, "Material", "MRPPlant")
            and str(row.get("MRPArea") or "").strip() == str(context.get("mrp_area") or "").strip()
            and (
                _date(context.get("date_to")) is None
                or _date(row.get("MRPElementAvailyOrRqmtDate")) is None
                or _date(row.get("MRPElementAvailyOrRqmtDate")) <= _date(context.get("date_to"))
            )
        ]
        mrp_units = {_unit_key(row.get("MaterialBaseUnit")) for row in exact_mrp if _unit_key(row.get("MaterialBaseUnit"))}
        stock_units = {_unit_key(row.get("MaterialBaseUnit")) for row in exact_stock if _unit_key(row.get("MaterialBaseUnit"))}
        comparable_units = mrp_units | stock_units
        if len(mrp_units) != 1:
            gaps.append("mrp_unit_not_comparable")
        else:
            manual_unit = next(iter(mrp_units))
            if manual_unit_input and manual_unit_input != manual_unit:
                gaps.append("manual_demand_unit_not_comparable")
            if stock_units and stock_units != {manual_unit}:
                gaps.append("stock_unit_not_comparable")
        segments = {
            (
                str(row.get("MRPPlanningSegment") or "").strip(),
                str(row.get("MRPPlanningSegmentType") or "").strip(),
            )
            for row in exact_mrp
        }
        if len(segments) > 1:
            gaps.append("mrp_planning_segment_ambiguous")
        if comparable_units and manual_unit and comparable_units == {manual_unit}:
            stock_values = [_strict_decimal(row.get("MatlWrhsStkQtyInMatlBaseUnit")) for row in exact_stock]
            if all(value is not None for value in stock_values):
                current_stock = sum((value for value in stock_values if value is not None), Decimal(0))

        dated_balances: dict[date, list[Decimal]] = {}
        demand_sum = Decimal(0)
        receipt_sum = Decimal(0)
        for row in exact_mrp:
            row_date = _date(row.get("MRPElementAvailyOrRqmtDate"))
            open_quantity = _strict_decimal(row.get("MRPElementOpenQuantity"))
            available_quantity = _strict_decimal(row.get("MRPAvailableQuantity"))
            demand_group = str(row.get("DemandCategoryGroup") or "").strip()
            receipt_group = str(row.get("ReceiptCategoryGroup") or "").strip()
            element_category = str(row.get("MRPElementCategory") or "").strip()
            flow_direction = "neutral"
            classification_source = "category_group"
            if element_category == "WB":
                flow_direction = "stock"
                classification_source = "mrp_element_category"
            elif demand_group and not receipt_group:
                flow_direction = "demand"
            elif receipt_group and not demand_group:
                flow_direction = "receipt"
            elif demand_group and receipt_group:
                flow_direction = "ambiguous"
                gaps.append("mrp_category_ambiguous")
            elif open_quantity is not None and open_quantity != 0:
                # Some target systems populate DemandCategoryGroup and
                # ReceiptCategoryGroup only when a shortage profile is supplied.
                # The unprofiled sequence is authoritative for completeness, so
                # retain the raw blank groups and transparently classify display
                # rows from SAP's signed open quantity.  Never use this derived
                # direction to recalculate MRPAvailableQuantity.
                flow_direction = "demand" if open_quantity < 0 else "receipt"
                classification_source = "signed_open_quantity"
                category_sign_fallback_used = True
            normalized_supply_demand.append(
                {
                    "mrp_element": str(row.get("MRPElement") or "").strip(),
                    "mrp_element_item": str(row.get("MRPElementItem") or "").strip(),
                    "mrp_element_schedule_line": str(row.get("MRPElementScheduleLine") or "").strip(),
                    "element_category": element_category,
                    "element_name": str(row.get("MRPElementCategoryName") or "").strip(),
                    "date": row_date.isoformat() if row_date else None,
                    "open_quantity": format(open_quantity, "f") if open_quantity is not None else None,
                    "available_quantity": format(available_quantity, "f") if available_quantity is not None else None,
                    "unit": _unit_key(row.get("MaterialBaseUnit")) or None,
                    "demand_category_group": demand_group or None,
                    "receipt_category_group": receipt_group or None,
                    "flow_direction": flow_direction,
                    "classification_source": classification_source,
                    "is_firm": _truthy(row.get("MRPElementQuantityIsFirm")),
                    "is_released": _truthy(row.get("MRPElementIsReleased")),
                }
            )
            if row_date is None or available_quantity is None:
                gaps.append("mrp_balance_evidence")
                continue
            dated_balances.setdefault(row_date, []).append(available_quantity)
            if manual_date and row_date <= manual_date and open_quantity is not None:
                if flow_direction == "demand":
                    demand_sum += abs(open_quantity)
                elif (
                    flow_direction == "receipt"
                    and row_date >= (_date(context.get("analysis_date")) or row_date)
                ):
                    receipt_sum += abs(open_quantity)

        if manual_date is not None and manual_quantity is not None and dated_balances:
            horizon_start = min(dated_balances)
            horizon_end = max(dated_balances)
            if manual_date < horizon_start or manual_date > horizon_end:
                gaps.append("manual_demand_outside_mrp_horizon")
            eligible_dates = [value for value in dated_balances if value <= manual_date]
            if eligible_dates:
                balance_date = max(eligible_dates)
                projected_before = min(dated_balances[balance_date])
                projected_after = projected_before - manual_quantity
                manual_status = "covered" if projected_after >= 0 else "not_covered"
                original_points = [(manual_date, projected_before)] + [
                    (row_date, min(values))
                    for row_date, values in sorted(dated_balances.items())
                    if row_date >= manual_date
                ]
                original_low = min(value for _, value in original_points)
                simulated_points = [(row_date, value - manual_quantity) for row_date, value in original_points]
                lowest_simulated = min(value for _, value in simulated_points)
                first_shortage_date = next(
                    (row_date.isoformat() for row_date, value in simulated_points if value < 0),
                    None,
                )
                if original_low < 0:
                    horizon_status = "worsens_existing_shortage"
                elif lowest_simulated < 0:
                    horizon_status = "creates_shortage"
                else:
                    horizon_status = "no_new_shortage"
                existing_demand = demand_sum
                future_receipts = receipt_sum
            else:
                gaps.append("manual_demand_balance_unavailable")
        if any(
            code in gaps
            for code in (
                "current_stock_evidence",
                "supply_demand_evidence",
                "mrp_unit_not_comparable",
                "manual_demand_unit_not_comparable",
                "stock_unit_not_comparable",
                "mrp_planning_segment_ambiguous",
                "mrp_balance_evidence",
                "manual_demand_outside_mrp_horizon",
                "manual_demand_balance_unavailable",
            )
        ):
            manual_status = "unknown"
            horizon_status = "unknown"

    gaps = sorted(set(_gaps(inputs, *gaps)))
    source_complete = all(source_flags.values())
    evidence_complete = source_complete and not gaps
    forecast_attention = forecast_status in {"over_forecast", "under_forecast", "mixed", "pir_missing"}
    manual_attention = manual_status == "not_covered" or horizon_status in {
        "creates_shortage",
        "worsens_existing_shortage",
    }
    business_status = (
        "inconclusive"
        if not evidence_complete
        else "attention"
        if forecast_attention or manual_attention
        else "normal"
    )

    if business_status == "inconclusive":
        headline_zh = "已取得部分需求计划证据，但当前结论仍有证据缺口"
        headline_en = "Demand-planning evidence was collected, but the result remains inconclusive"
    elif manual_attention:
        headline_zh = "手工需求按当前MRP供需快照无法安全覆盖"
        headline_en = "The manual demand is not safely covered by the current MRP snapshot"
    elif forecast_attention:
        headline_zh = "销售需求与PIR存在需要计划员复核的偏差"
        headline_en = "Sales demand and PIR contain a variance requiring planner review"
    else:
        headline_zh = "当前需求计划证据未发现超出阈值的风险"
        headline_en = "No demand-planning risk above the threshold was found"

    metrics = [
        {"id": "sales_demand_quantity", "value": format(sales_total, "f")},
        {"id": "pir_quantity", "value": format(pir_total, "f")},
        {"id": "pir_withdrawal_quantity", "value": format(withdrawal_total, "f")},
        {"id": "planned_order_quantity", "value": format(planned_total, "f")},
        {"id": "period_count", "value": len(period_results)},
        {"id": "manual_demand_quantity", "value": context.get("manual_demand_quantity")},
        {"id": "projected_available_before_manual", "value": format(projected_before, "f") if projected_before is not None else None},
        {"id": "projected_available_after_manual", "value": format(projected_after, "f") if projected_after is not None else None},
    ]
    result = _result(
        inputs,
        business_status=business_status,
        headline_zh=headline_zh,
        headline_en=headline_en,
        overview_zh="已按PIR原生期间比较销售需求，并在用户提供外部需求时使用SAP累计MRP可用量进行只读模拟；该模拟不是正式ATP确认。",
        overview_en="Sales demand was compared at the native PIR period grain. When manual demand was supplied, SAP cumulative MRP availability was used for a read-only simulation; this is not a formal ATP confirmation.",
        stages=[
            _stage("sales_demand", "销售需求", "Sales demand", len(sales_evidence_rows)),
            _stage("pir", "计划独立需求 PIR", "Planned independent requirements", len(scoped_pir_items)),
            _stage("planned_orders", "计划订单", "Planned orders", len(planned_evidence_rows)),
            _stage("current_stock", "当前非限制库存", "Current unrestricted stock", len(stock_rows), state="not_requested" if not manual_requested else None),
            _stage("mrp_simulation", "手工需求MRP模拟", "Manual-demand MRP simulation", len(normalized_supply_demand), state="not_requested" if not manual_requested else "confirmed" if evidence_complete else "unknown"),
            _stage("atp", "正式ATP确认", "Formal ATP confirmation", 0, state="not_requested"),
        ],
        findings=[
            *([{"code": "FORECAST_VARIANCE", "severity": "medium", "status": forecast_status}] if forecast_attention else []),
            *([{"code": "MANUAL_DEMAND_NOT_COVERED", "severity": "high", "status": manual_status}] if manual_status == "not_covered" else []),
            *([{"code": "MANUAL_DEMAND_HORIZON_IMPACT", "severity": "high", "status": horizon_status}] if horizon_status in {"creates_shortage", "worsens_existing_shortage"} else []),
        ],
        metrics=metrics,
        gaps=gaps,
        limitations=(
            [
                "mrp_simulation_not_formal_atp",
                *(
                    ["mrp_category_groups_blank_used_signed_open_quantity_for_display"]
                    if category_sign_fallback_used
                    else []
                ),
            ]
            if manual_requested
            else []
        ),
        records=period_results,
        allow_empty_records=True,
        preserve_business_status_on_gap=True,
        source_complete_override=source_complete,
        actions_zh=(
            ["由计划员复核PIR和供需元素；正式承诺交期前仍需在SAP中执行ATP检查。"]
            if business_status != "normal"
            else ["如需向客户承诺交期，仍应在SAP中执行正式ATP检查。"]
        ),
        actions_en=(
            ["Have the planner review PIR and MRP elements; run formal SAP ATP before promising a delivery date."]
            if business_status != "normal"
            else ["Run formal SAP ATP before promising a customer delivery date."]
        ),
    )
    pir_scope = (
        {field: selected_scope[index] for index, field in enumerate(scope_fields)}
        if selected_scope is not None
        else None
    )
    total_variance = sales_total - pir_total
    total_variance_percent = (
        total_variance / pir_total * Decimal(100) if pir_total != 0 else None
    )
    extras = {
        "analysis_date": context.get("analysis_date"),
        "pir_scope": pir_scope,
        "sales_demand_quantity": format(sales_total, "f"),
        "pir_quantity": format(pir_total, "f"),
        "planned_order_quantity": format(planned_total, "f"),
        "variance_quantity": format(total_variance, "f"),
        "variance_percent": (
            format(total_variance_percent.quantize(Decimal("0.01")), "f")
            if total_variance_percent is not None
            else None
        ),
        "forecast_status": forecast_status,
        "manual_demand_quantity": context.get("manual_demand_quantity"),
        "manual_demand_date": context.get("manual_demand_date"),
        "manual_demand_unit": manual_unit or manual_unit_input or None,
        "current_unrestricted_stock": format(current_stock, "f") if current_stock is not None else None,
        "projected_available_before_manual": format(projected_before, "f") if projected_before is not None else None,
        "projected_available_after_manual": format(projected_after, "f") if projected_after is not None else None,
        "existing_demand_before_manual": format(existing_demand, "f") if existing_demand is not None else None,
        "future_receipts_before_manual": format(future_receipts, "f") if future_receipts is not None else None,
        "manual_demand_status": manual_status,
        "horizon_impact_status": horizon_status,
        "first_simulated_shortage_date": first_shortage_date,
        "lowest_simulated_available_quantity": format(lowest_simulated, "f") if lowest_simulated is not None else None,
        "atp_status": "not_assessed",
        "period_results": period_results,
        "supply_demand_items": normalized_supply_demand,
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "evidence_gaps": gaps,
        "business_status": business_status,
    }
    result["rule_id"] = "demand_forecast_planning_single_v2"
    result["status"] = "complete" if evidence_complete else "inconclusive"
    result["business_status"] = business_status
    result["business_complete"] = evidence_complete
    result["source_complete"] = source_complete
    result["business_report"]["evidence_complete"] = evidence_complete
    result["business_report"]["evidence_tables"] = [
        {
            "id": "forecast_periods",
            "title": {"zh": "销售需求与PIR偏差", "en": "Sales demand and PIR variance"},
            "columns": ["period", "period_type", "period_start", "period_end", "sales_demand_quantity", "pir_quantity", "planned_order_quantity", "variance_quantity", "variance_percent", "unit", "status"],
            "rows": period_results,
        },
        {
            "id": "manual_demand_summary",
            "title": {"zh": "手工需求MRP模拟", "en": "Manual-demand MRP simulation"},
            "columns": ["manual_demand_quantity", "manual_demand_date", "manual_demand_unit", "current_unrestricted_stock", "projected_available_before_manual", "projected_available_after_manual", "existing_demand_before_manual", "future_receipts_before_manual", "manual_demand_status", "horizon_impact_status", "first_simulated_shortage_date", "atp_status"],
            "rows": [{key: extras[key] for key in ("manual_demand_quantity", "manual_demand_date", "manual_demand_unit", "current_unrestricted_stock", "projected_available_before_manual", "projected_available_after_manual", "existing_demand_before_manual", "future_receipts_before_manual", "manual_demand_status", "horizon_impact_status", "first_simulated_shortage_date", "atp_status")}],
        },
        {
            "id": "mrp_supply_demand",
            "title": {"zh": "SAP MRP供需明细", "en": "SAP MRP supply-demand details"},
            "columns": ["date", "mrp_element", "mrp_element_item", "element_category", "element_name", "open_quantity", "available_quantity", "unit", "demand_category_group", "receipt_category_group", "flow_direction", "classification_source", "is_firm", "is_released"],
            "rows": normalized_supply_demand,
        },
    ]
    result["workflow_output"].update(extras)
    result["workflow_output"]["business_report"] = result["business_report"]
    return result


def _evidence_for_material(inputs: JsonObject, material: str) -> JsonObject:
    """Return a material view while preserving per-chunk completeness."""

    scoped = deepcopy(inputs)
    expected = material.strip().upper()

    def row_material(row: JsonObject) -> str:
        return _text(row, "Material", "Product").upper()

    def visit(value: Any) -> Any:
        if isinstance(value, list):
            return [visit(child) for child in value]
        if not isinstance(value, dict):
            return value
        normalized: JsonObject = {}
        for key, child in value.items():
            if key == "results" and isinstance(child, list):
                normalized[key] = [
                    visit(row)
                    for row in child
                    if not isinstance(row, dict)
                    or not row_material(row)
                    or row_material(row) == expected
                ]
            else:
                normalized[key] = visit(child)

        chunks = normalized.get("chunk_results")
        if isinstance(chunks, list) and any(
            isinstance(chunk, dict) and chunk.get("filter_values") for chunk in chunks
        ):
            relevant = [
                chunk
                for chunk in chunks
                if isinstance(chunk, dict)
                and expected
                in {
                    str(item or "").strip().upper()
                    for item in chunk.get("filter_values") or []
                }
            ]
            normalized["chunk_results"] = relevant
            normalized["failed_filter_values"] = [
                expected
                for chunk in relevant
                if chunk.get("source_complete") is not True
                or chunk.get("source_truncated") is True
                or chunk.get("error_code")
            ]
            normalized["source_complete"] = bool(relevant) and all(
                chunk.get("source_complete") is True
                and chunk.get("source_truncated") is not True
                and not chunk.get("error_code")
                for chunk in relevant
            )
            normalized["source_truncated"] = any(
                chunk.get("source_truncated") is True for chunk in relevant
            )
        elif isinstance(normalized.get("source_complete"), bool):
            child_flags: list[bool] = []

            def collect(child: Any) -> None:
                if isinstance(child, dict):
                    if child is not normalized and isinstance(child.get("source_complete"), bool):
                        child_flags.append(bool(child["source_complete"]))
                    for nested in child.values():
                        collect(nested)
                elif isinstance(child, list):
                    for nested in child:
                        collect(nested)

            for child in normalized.values():
                collect(child)
            if child_flags:
                normalized["source_complete"] = all(child_flags)
        return normalized

    evidence = scoped.get("evidence")
    if isinstance(evidence, dict):
        scoped["evidence"] = visit(evidence)
    return scoped


def _mrp_material_view(
    inputs: JsonObject,
    *,
    material: str,
    plant: str,
    mrp_area: str,
    date_from: date | None,
    date_to: date | None,
) -> JsonObject:
    rows = [
        row
        for row in _rows(inputs, "supply_demand", "supply_demand_items")
        if _text(row, "Material").upper() == material.upper()
        and _text(row, "MRPPlant", "Plant").upper() == plant.upper()
        and _text(row, "MRPArea").upper() == mrp_area.upper()
    ]
    master = [
        row
        for row in _rows(inputs, "mrp_material", "mrp_materials")
        if _text(row, "Material").upper() == material.upper()
        and _text(row, "MRPPlant", "Plant").upper() == plant.upper()
        and _text(row, "MRPArea").upper() == mrp_area.upper()
    ]
    gaps: list[str] = []
    if not _topic_complete(inputs, "mrp_material"):
        gaps.append("mrp_material_evidence")
    if not _topic_complete(inputs, "supply_demand"):
        gaps.append("supply_demand_evidence")
    if len(master) != 1:
        gaps.append("mrp_material_scope_evidence")
    units = {
        _unit_key(row.get("MaterialBaseUnit") or row.get("BaseUnit"))
        for row in [*master, *rows]
        if _unit_key(row.get("MaterialBaseUnit") or row.get("BaseUnit"))
    }
    if len(units) != 1:
        gaps.append("mrp_unit_not_comparable")
    unit = next(iter(units), None)

    details: list[JsonObject] = []
    balances: list[tuple[date, Decimal]] = []
    classification_complete = True
    exception_numbers: set[str] = set()
    for row in rows:
        row_date = _date(row.get("MRPElementAvailyOrRqmtDate"))
        if date_from and row_date and row_date < date_from:
            continue
        if date_to and row_date and row_date > date_to:
            continue
        open_quantity = _strict_decimal(row.get("MRPElementOpenQuantity"))
        available = _strict_decimal(row.get("MRPAvailableQuantity"))
        demand_group = _text(row, "DemandCategoryGroup")
        receipt_group = _text(row, "ReceiptCategoryGroup")
        category = _text(row, "MRPElementCategory")
        if category == "WB":
            direction, group, classification_source = (
                "stock",
                "current_stock",
                "mrp_element_category",
            )
        elif demand_group and not receipt_group:
            direction, group, classification_source = (
                "demand",
                demand_group,
                "demand_category_group",
            )
        elif receipt_group and not demand_group:
            direction, group, classification_source = (
                "receipt",
                receipt_group,
                "receipt_category_group",
            )
        else:
            direction = (
                "demand"
                if open_quantity is not None and open_quantity < 0
                else "receipt"
                if open_quantity is not None and open_quantity > 0
                else "unknown"
            )
            group = "unclassified_element"
            classification_source = "signed_open_quantity" if direction != "unknown" else "none"
            classification_complete = False
        for field in ("ExceptionMessageNumber", "ExceptionMessageNumber2"):
            number = _text(row, field)
            if number:
                exception_numbers.add(number)
        details.append(
            {
                "material": material,
                "date": row_date.isoformat() if row_date else None,
                "mrp_element": _text(row, "MRPElement") or None,
                "mrp_element_item": _text(row, "MRPElementItem") or None,
                "element_category": category or None,
                "element_name": _text(row, "MRPElementCategoryName") or None,
                "open_quantity": format(open_quantity, "f") if open_quantity is not None else None,
                "available_quantity": format(available, "f") if available is not None else None,
                "unit": _unit_key(row.get("MaterialBaseUnit")) or unit,
                "flow_direction": direction,
                "business_group": group,
                "classification_source": classification_source,
                "is_firm": _truthy(row.get("MRPElementQuantityIsFirm")),
                "is_released": _truthy(row.get("MRPElementIsReleased")),
            }
        )
        if row_date is None or available is None:
            gaps.append("mrp_balance_evidence")
        else:
            balances.append((row_date, available))
    balances.sort(key=lambda item: item[0])
    if not details:
        gaps.append("mrp_balance_unavailable")

    start_balance = balances[0][1] if balances else None
    end_balance = balances[-1][1] if balances else None
    lowest = min((value for _, value in balances), default=None)
    first_shortage = next((when.isoformat() for when, value in balances if value < 0), None)
    coverage_status = "unknown" if lowest is None else "shortage" if lowest < 0 else "covered"
    return {
        "unit": unit,
        "mrp_available_at_start": format(start_balance, "f") if start_balance is not None else None,
        "mrp_available_at_end": format(end_balance, "f") if end_balance is not None else None,
        "lowest_mrp_available_quantity": format(lowest, "f") if lowest is not None else None,
        "first_shortage_date": first_shortage,
        "mrp_coverage_status": coverage_status,
        "classification_complete": classification_complete,
        "exception_numbers": sorted(exception_numbers),
        "supply_demand_items": details,
        "gaps": sorted(set(gaps)),
    }


def _demand_forecast(inputs: JsonObject) -> JsonObject:
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    context = inputs.get("analysis_context") if isinstance(inputs.get("analysis_context"), dict) else {}
    materials = [
        str(item).strip().upper()
        for item in context.get("materials") or run_input.get("materials") or []
    ]
    plant = _text(run_input, "plant").upper()
    mrp_area = _text(context, "mrp_area").upper() or plant
    date_from = _date(context.get("date_from") or run_input.get("date_from"))
    date_to = _date(context.get("date_to") or run_input.get("date_to"))
    material_results: list[JsonObject] = []

    for material in materials:
        scoped = _evidence_for_material(inputs, material)
        scoped["run_input"] = {**run_input, "material": material}
        scoped["analysis_context"] = {**context, "manual_demand_requested": False}
        forecast = _legacy_demand_forecast_single(scoped)["workflow_output"]
        mrp = _mrp_material_view(
            scoped,
            material=material,
            plant=plant,
            mrp_area=mrp_area,
            date_from=date_from,
            date_to=date_to,
        )
        forecast_status = {
            "over_forecast": "pir_under_coverage",
            "under_forecast": "pir_over_coverage",
        }.get(str(forecast.get("forecast_status") or "unknown"), forecast.get("forecast_status"))
        pir_action = (
            "increase_or_bring_forward_pir"
            if forecast_status in {"pir_under_coverage", "pir_missing"}
            else "reduce_or_reschedule_pir"
            if forecast_status == "pir_over_coverage"
            else "review_pir_scope"
            if forecast_status in {"mixed", "unknown"}
            else "no_pir_adjustment"
        )
        if pir_action != "no_pir_adjustment":
            planned_action = "rerun_mrp_after_pir_review"
        elif mrp["mrp_coverage_status"] == "shortage":
            planned_action = "increase_or_bring_forward_planned_receipt"
        elif "10" in mrp["exception_numbers"]:
            planned_action = "bring_forward_planned_receipt"
        elif any(code in mrp["exception_numbers"] for code in ("15", "20", "26")):
            planned_action = "reduce_postpone_or_cancel_planned_receipt"
        else:
            planned_action = "no_planned_order_adjustment"
        gaps = sorted(set([*(forecast.get("evidence_gaps") or []), *mrp["gaps"]]))
        source_complete = bool(forecast.get("source_complete")) and not any(
            gap in {"mrp_material_evidence", "supply_demand_evidence"} for gap in gaps
        )
        evidence_complete = source_complete and not gaps
        attention = forecast_status in {
            "pir_under_coverage",
            "pir_over_coverage",
            "pir_missing",
            "mixed",
        } or mrp["mrp_coverage_status"] == "shortage" or planned_action not in {
            "no_planned_order_adjustment",
            "rerun_mrp_after_pir_review",
        }
        business_status = (
            "inconclusive"
            if not evidence_complete
            else "attention"
            if attention
            else "normal"
        )
        withdrawal_total = sum(
            (
                Decimal(str(row.get("pir_withdrawal_quantity") or 0))
                for row in forecast.get("period_results") or []
            ),
            Decimal(0),
        )
        material_results.append(
            {
                "material": material,
                "plant": plant,
                "mrp_area": mrp_area,
                "unit": mrp.get("unit"),
                "business_status": business_status,
                "source_complete": source_complete,
                "evidence_complete": evidence_complete,
                "evidence_gaps": gaps,
                "pir_scope": forecast.get("pir_scope"),
                "sales_demand_quantity": forecast.get("sales_demand_quantity"),
                "pir_quantity": forecast.get("pir_quantity"),
                "pir_withdrawal_quantity": format(withdrawal_total, "f"),
                "planned_order_quantity": forecast.get("planned_order_quantity"),
                "variance_quantity": forecast.get("variance_quantity"),
                "variance_percent": forecast.get("variance_percent"),
                "forecast_status": forecast_status,
                "mrp_available_at_start": mrp["mrp_available_at_start"],
                "mrp_available_at_end": mrp["mrp_available_at_end"],
                "lowest_mrp_available_quantity": mrp["lowest_mrp_available_quantity"],
                "first_shortage_date": mrp["first_shortage_date"],
                "mrp_coverage_status": mrp["mrp_coverage_status"],
                "classification_complete": mrp["classification_complete"],
                "period_results": forecast.get("period_results") or [],
                "supply_demand_items": mrp["supply_demand_items"],
                "recommendations": [
                    {"priority": 1, "subject": "pir", "action": pir_action},
                    {"priority": 2, "subject": "planned_order", "action": planned_action},
                ],
                "business_report": {
                    "headline": {
                        "zh": f"物料 {material} 的计划覆盖结果",
                        "en": f"Planning coverage result for material {material}",
                    },
                    "overview": {
                        "zh": "先比较销售需求与PIR，再使用SAP累计MRP可用量判断净供需。",
                        "en": "Sales demand is compared with PIR before SAP cumulative MRP availability is used for net coverage.",
                    },
                    "stages": [
                        _stage("pir", "PIR覆盖", "PIR coverage", len(forecast.get("period_results") or []), state=str(forecast_status)),
                        _stage("planned_order", "计划订单", "Planned orders", 1, state="reviewed"),
                        _stage("mrp", "MRP净供需", "MRP net supply and demand", len(mrp["supply_demand_items"]), state=str(mrp["mrp_coverage_status"])),
                    ],
                    "next_actions": {
                        "zh": ["先复核PIR，再根据复核后的MRP结果处理计划订单。"],
                        "en": ["Review PIR first, then act on planned orders using the reviewed MRP result."],
                    },
                },
            }
        )

    counts = {
        status: sum(item["business_status"] == status for item in material_results)
        for status in ("normal", "attention", "inconclusive")
    }
    source_complete = bool(material_results) and all(
        item["source_complete"] for item in material_results
    )
    evidence_complete = bool(material_results) and all(
        item["evidence_complete"] for item in material_results
    )
    business_status = (
        "inconclusive"
        if counts["inconclusive"]
        else "attention"
        if counts["attention"]
        else "normal"
    )
    summary_rows = [
        {
            "material": item["material"],
            "unit": item["unit"],
            "sales_demand_quantity": item["sales_demand_quantity"],
            "pir_quantity": item["pir_quantity"],
            "planned_order_quantity": item["planned_order_quantity"],
            "forecast_status": item["forecast_status"],
            "mrp_coverage_status": item["mrp_coverage_status"],
            "business_status": item["business_status"],
            "source_complete": item["source_complete"],
            "evidence_complete": item["evidence_complete"],
        }
        for item in material_results
    ]
    result = _result(
        inputs,
        business_status=business_status,
        headline_zh=f"已完成 {len(material_results)} 个物料的计划覆盖检查",
        headline_en=f"Planning coverage was checked for {len(material_results)} material(s)",
        overview_zh="每个物料独立比较销售需求与PIR，并使用SAP累计MRP可用量判断净供需。建议始终先复核PIR，再处理计划订单。",
        overview_en="Each material independently compares sales demand with PIR and uses SAP cumulative MRP availability for net coverage. Recommendations always review PIR before planned orders.",
        stages=[
            _stage("materials", "物料范围", "Material scope", len(material_results)),
            _stage("pir", "PIR复核", "PIR review", len(material_results)),
            _stage("mrp", "MRP净供需", "MRP net supply and demand", len(material_results)),
        ],
        findings=[
            {"code": "MATERIAL_ATTENTION", "severity": "medium", "count": counts["attention"]},
            {"code": "MATERIAL_INCONCLUSIVE", "severity": "high", "count": counts["inconclusive"]},
        ],
        metrics=[
            {"id": "requested_material_count", "value": len(materials)},
            {"id": "processed_material_count", "value": len(material_results)},
            {"id": "normal_material_count", "value": counts["normal"]},
            {"id": "attention_material_count", "value": counts["attention"]},
            {"id": "inconclusive_material_count", "value": counts["inconclusive"]},
        ],
        gaps=sorted({gap for item in material_results for gap in item["evidence_gaps"]}),
        records=summary_rows,
        allow_empty_records=True,
        source_complete_override=source_complete,
        preserve_business_status_on_gap=True,
        actions_zh=["先处理各物料的PIR建议，再根据复核后的MRP结果处理计划订单。"],
        actions_en=["Address each material's PIR recommendation first, then act on planned orders using the reviewed MRP result."],
    )
    extras = {
        "requested_material_count": len(materials),
        "processed_material_count": len(material_results),
        "normal_material_count": counts["normal"],
        "attention_material_count": counts["attention"],
        "inconclusive_material_count": counts["inconclusive"],
        "material_results": material_results,
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "evidence_gaps": sorted(
            {gap for item in material_results for gap in item["evidence_gaps"]}
        ),
        "business_status": business_status,
    }
    result["rule_id"] = "planned_order_pir_coverage_deterministic_v3"
    result["status"] = "complete" if evidence_complete else "inconclusive"
    result["business_complete"] = evidence_complete
    result["business_report"]["evidence_complete"] = evidence_complete
    result["business_report"]["evidence_tables"] = [
        {
            "id": "material_coverage_summary",
            "title": {"zh": "多物料覆盖结果", "en": "Multi-material coverage results"},
            "columns": list(summary_rows[0]) if summary_rows else ["material"],
            "rows": summary_rows,
        }
    ]
    result["workflow_output"].update(extras)
    result["workflow_output"]["business_report"] = result["business_report"]
    return result


def _new_sales_demand_coverage(inputs: JsonObject) -> JsonObject:
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    context = inputs.get("analysis_context") if isinstance(inputs.get("analysis_context"), dict) else {}
    plant = _text(context, "plant").upper() or _text(run_input, "plant").upper()
    mrp_area = _text(context, "mrp_area").upper() or plant
    material_results: list[JsonObject] = []
    for demand in context.get("demand_items") or []:
        if not isinstance(demand, dict):
            continue
        material = _text(demand, "material").upper()
        scoped = _evidence_for_material(inputs, material)
        evidence = scoped.get("evidence") if isinstance(scoped.get("evidence"), dict) else {}
        for topic in ("pir", "sales_demand", "planned_orders"):
            evidence.setdefault(
                topic,
                {"ok": True, "source_complete": True, "data": {"results": []}},
            )
        scoped["evidence"] = evidence
        scoped["run_input"] = {
            "material": material,
            "plant": plant,
            "date_from": context.get("analysis_date"),
            "date_to": demand.get("horizon_end_date"),
            "manual_demand_quantity": demand.get("quantity"),
            "manual_demand_date": demand.get("demand_date"),
            "manual_demand_unit": demand.get("unit"),
        }
        scoped["analysis_context"] = {
            "analysis_date": context.get("analysis_date"),
            "date_from": context.get("analysis_date"),
            "date_to": demand.get("horizon_end_date"),
            "pir_version": "00",
            "mrp_area": mrp_area,
            "deviation_threshold_percent": "20",
            "manual_demand_requested": True,
            "manual_demand_quantity": demand.get("quantity"),
            "manual_demand_date": demand.get("demand_date"),
            "manual_demand_unit": demand.get("unit"),
        }
        single = _legacy_demand_forecast_single(scoped)["workflow_output"]
        master_complete = _topic_complete(scoped, "mrp_material")
        gaps = sorted(
            set(
                [
                    *(single.get("evidence_gaps") or []),
                    *([] if master_complete else ["mrp_material_evidence"]),
                ]
            )
        )
        source_complete = bool(single.get("source_complete")) and master_complete
        evidence_complete = source_complete and not gaps
        demand_status = single.get("manual_demand_status") or "unknown"
        horizon_status = single.get("horizon_impact_status") or "unknown"
        attention = demand_status == "not_covered" or horizon_status in {
            "creates_shortage",
            "worsens_existing_shortage",
        }
        business_status = (
            "inconclusive"
            if not evidence_complete
            else "attention"
            if attention
            else "normal"
        )
        material_results.append(
            {
                "material": material,
                "plant": plant,
                "mrp_area": mrp_area,
                "unit": single.get("manual_demand_unit"),
                "new_sales_demand_quantity": demand.get("quantity"),
                "new_sales_demand_date": demand.get("demand_date"),
                "current_unrestricted_stock": single.get("current_unrestricted_stock"),
                "projected_available_before_demand": single.get("projected_available_before_manual"),
                "projected_available_after_demand": single.get("projected_available_after_manual"),
                "existing_demand_before_request": single.get("existing_demand_before_manual"),
                "future_receipts_before_request": single.get("future_receipts_before_manual"),
                "demand_coverage_status": demand_status,
                "horizon_impact_status": horizon_status,
                "first_simulated_shortage_date": single.get("first_simulated_shortage_date"),
                "lowest_simulated_available_quantity": single.get("lowest_simulated_available_quantity"),
                "atp_status": "not_assessed",
                "supply_demand_items": single.get("supply_demand_items") or [],
                "source_complete": source_complete,
                "evidence_complete": evidence_complete,
                "evidence_gaps": gaps,
                "business_status": business_status,
                "business_report": {
                    "headline": {
                        "zh": f"物料 {material} 的新增销售需求覆盖结果",
                        "en": f"New sales demand coverage for material {material}",
                    },
                    "overview": {
                        "zh": "使用SAP累计MRP可用量模拟新增需求；当前库存未重复计入，结果不是正式ATP确认。",
                        "en": "The simulation uses SAP cumulative MRP availability without re-adding current stock and is not formal ATP confirmation.",
                    },
                    "stages": [
                        _stage("stock", "当前库存", "Current stock", 1, state="reviewed"),
                        _stage("demand", "新增需求覆盖", "New demand coverage", 1, state=str(demand_status)),
                        _stage("horizon", "后续短缺影响", "Subsequent shortage impact", 1, state=str(horizon_status)),
                        _stage("atp", "正式ATP", "Formal ATP", 0, state="not_assessed"),
                    ],
                    "next_actions": {
                        "zh": ["如需向客户承诺交期，请在SAP中执行正式ATP检查。"],
                        "en": ["Run formal SAP ATP before committing a delivery date to the customer."],
                    },
                },
            }
        )

    counts = {
        status: sum(item["business_status"] == status for item in material_results)
        for status in ("normal", "attention", "inconclusive")
    }
    source_complete = bool(material_results) and all(
        item["source_complete"] for item in material_results
    )
    evidence_complete = bool(material_results) and all(
        item["evidence_complete"] for item in material_results
    )
    business_status = (
        "inconclusive"
        if counts["inconclusive"]
        else "attention"
        if counts["attention"]
        else "normal"
    )
    summary_rows = [
        {
            "material": item["material"],
            "new_sales_demand_quantity": item["new_sales_demand_quantity"],
            "new_sales_demand_date": item["new_sales_demand_date"],
            "unit": item["unit"],
            "projected_available_before_demand": item["projected_available_before_demand"],
            "projected_available_after_demand": item["projected_available_after_demand"],
            "demand_coverage_status": item["demand_coverage_status"],
            "horizon_impact_status": item["horizon_impact_status"],
            "business_status": item["business_status"],
        }
        for item in material_results
    ]
    result = _result(
        inputs,
        business_status=business_status,
        headline_zh=f"已模拟 {len(material_results)} 个物料的新增销售需求",
        headline_en=f"New sales demand was simulated for {len(material_results)} material(s)",
        overview_zh="模拟直接使用SAP累计MRP可用量，不重复累加库存或收货；该结果不是正式ATP确认。",
        overview_en="The simulation uses SAP cumulative MRP availability without re-adding stock or receipts; it is not a formal ATP confirmation.",
        stages=[
            _stage("stock", "当前库存", "Current stock", len(material_results)),
            _stage("mrp", "MRP供需模拟", "MRP simulation", len(material_results)),
            _stage("atp", "正式ATP", "Formal ATP", 0, state="not_assessed"),
        ],
        findings=[
            {"code": "DEMAND_NOT_COVERED", "severity": "high", "count": counts["attention"]},
            {"code": "MATERIAL_INCONCLUSIVE", "severity": "high", "count": counts["inconclusive"]},
        ],
        metrics=[
            {"id": "requested_material_count", "value": len(context.get("materials") or [])},
            {"id": "processed_material_count", "value": len(material_results)},
            {"id": "normal_material_count", "value": counts["normal"]},
            {"id": "attention_material_count", "value": counts["attention"]},
            {"id": "inconclusive_material_count", "value": counts["inconclusive"]},
        ],
        gaps=sorted({gap for item in material_results for gap in item["evidence_gaps"]}),
        limitations=["mrp_simulation_not_formal_atp"],
        records=summary_rows,
        allow_empty_records=True,
        source_complete_override=source_complete,
        preserve_business_status_on_gap=True,
        actions_zh=["如需向客户承诺交期，请在SAP中执行正式ATP检查。"],
        actions_en=["Run formal SAP ATP before committing a delivery date to the customer."],
    )
    extras = {
        "requested_material_count": len(context.get("materials") or []),
        "processed_material_count": len(material_results),
        "normal_material_count": counts["normal"],
        "attention_material_count": counts["attention"],
        "inconclusive_material_count": counts["inconclusive"],
        "material_results": material_results,
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "evidence_gaps": sorted(
            {gap for item in material_results for gap in item["evidence_gaps"]}
        ),
        "business_status": business_status,
    }
    result["rule_id"] = "new_sales_demand_coverage_deterministic_v1"
    result["status"] = "complete" if evidence_complete else "inconclusive"
    result["business_complete"] = evidence_complete
    result["business_report"]["evidence_complete"] = evidence_complete
    result["business_report"]["evidence_tables"] = [
        {
            "id": "new_sales_demand_summary",
            "title": {"zh": "新增销售需求覆盖结果", "en": "New sales demand coverage results"},
            "columns": list(summary_rows[0]) if summary_rows else ["material"],
            "rows": summary_rows,
        }
    ]
    result["workflow_output"].update(extras)
    result["workflow_output"]["business_report"] = result["business_report"]
    return result


def _mrp_exception(inputs: JsonObject) -> JsonObject:
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    analysis_context = (
        inputs.get("analysis_context")
        if isinstance(inputs.get("analysis_context"), dict)
        else {}
    )
    analysis_date = _date(analysis_context.get("analysis_date")) or date.today()
    master = _rows(inputs, "mrp_material")
    coverage = _rows(inputs, "material_coverages")
    raw_elements = _rows(inputs, "supply_demand_items")

    master_complete = _topic_complete(inputs, "mrp_material")
    coverage_complete = _topic_complete(inputs, "material_coverages")
    elements_complete = _topic_complete(inputs, "supply_demand_items")
    elements = raw_elements if elements_complete else []

    expected_material = str(run_input.get("material") or "").strip()
    expected_plant = str(run_input.get("plant") or "").strip()
    expected_area = str(run_input.get("mrp_area") or "").strip()
    expected_profile = str(run_input.get("shortage_profile") or "").strip()
    expected_counter = str(run_input.get("shortage_counter") or "").strip()

    evidence_gaps: list[str] = []
    if not master_complete:
        evidence_gaps.append("mrp_master_evidence")
    if not coverage_complete:
        evidence_gaps.append("mrp_coverage_evidence")
    if not elements_complete:
        evidence_gaps.append("mrp_supply_demand_evidence")

    def scoped(row: JsonObject, *, include_profile: bool) -> bool:
        actual = {
            "material": _text(row, "Material"),
            "plant": _text(row, "MRPPlant", "Plant"),
            "mrp_area": _text(row, "MRPArea"),
        }
        expected = {
            "material": expected_material,
            "plant": expected_plant,
            "mrp_area": expected_area,
        }
        if include_profile:
            actual.update(
                {
                    "shortage_profile": _text(row, "MaterialShortageProfile"),
                    "shortage_counter": _text(row, "MaterialShortageProfileCount"),
                }
            )
            expected.update(
                {
                    "shortage_profile": expected_profile,
                    "shortage_counter": expected_counter,
                }
            )
        return all(actual[key] and actual[key] == value for key, value in expected.items())

    scoped_master = [row for row in master if scoped(row, include_profile=False)]
    scoped_coverage = [row for row in coverage if scoped(row, include_profile=True)]
    scoped_elements = [row for row in elements if scoped(row, include_profile=True)]
    if master_complete and (len(scoped_master) != len(master) or not scoped_master):
        evidence_gaps.append("mrp_master_scope_evidence")
    if coverage_complete and (len(scoped_coverage) != len(coverage) or not scoped_coverage):
        evidence_gaps.append("mrp_coverage_scope_evidence")
    if elements_complete and len(scoped_elements) != len(elements):
        evidence_gaps.append("mrp_supply_demand_scope_evidence")

    def strict_decimal(value: Any) -> Decimal | None:
        if value in {None, ""} or isinstance(value, bool):
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None

    def strict_int(value: Any) -> int | None:
        if value in {None, ""} or isinstance(value, bool):
            return None
        try:
            return int(str(value))
        except ValueError:
            return None

    # SAP APIs may expose the same piece/each unit as an internal SAP code,
    # a language-dependent commercial code, or the ISO code. Preserve the
    # original source value for reporting and use only this deliberately small
    # versioned alias set when comparing evidence from different APIs.
    unit_aliases = {"EA": "PCE", "PC": "PCE", "PCE": "PCE", "ST": "PCE"}

    def comparable_unit(value: str) -> str:
        normalized = value.strip().upper()
        return unit_aliases.get(normalized, normalized)

    coverage_signatures = {
        (
            _text(row, "MaterialShortageQuantity"),
            _date_text(row, "MaterialShortageStartDate"),
            _date_text(row, "MaterialShortageEndDate"),
            _text(row, "DaysOfSupplyDuration"),
            _text(row, "MaterialShortageDuration"),
            _text(row, "MaterialShortageDurnInWorkdays"),
            _date_text(row, "MaterialReplnmtLeadDurnEndDate"),
            _text(row, "MaterialBaseUnit"),
            _text(row, "TimeHorizonInDays"),
            _text(row, "HasAcceptedShortage"),
        )
        for row in scoped_coverage
    }
    if len(coverage_signatures) > 1:
        evidence_gaps.append("mrp_coverage_conflict")
    coverage_row = scoped_coverage[0] if scoped_coverage else {}
    master_row = scoped_master[0] if scoped_master else {}

    shortage_quantity = strict_decimal(coverage_row.get("MaterialShortageQuantity"))
    shortage_start = _date(coverage_row.get("MaterialShortageStartDate"))
    shortage_end = _date(coverage_row.get("MaterialShortageEndDate"))
    days_of_supply = strict_int(coverage_row.get("DaysOfSupplyDuration"))
    replenishment_lead_end = _date(coverage_row.get("MaterialReplnmtLeadDurnEndDate"))
    time_horizon_days = strict_int(coverage_row.get("TimeHorizonInDays"))
    accepted_shortage = _truthy(coverage_row.get("HasAcceptedShortage"))
    coverage_unit = _text(coverage_row, "MaterialBaseUnit")
    master_unit = _text(master_row, "BaseUnit")

    shortage_status = "unknown"
    shortage_priority = "unknown"
    if coverage_row:
        if shortage_quantity is None or shortage_quantity < 0:
            evidence_gaps.append("mrp_shortage_quantity_evidence")
        elif shortage_quantity == 0:
            if shortage_start is not None or shortage_end is not None:
                evidence_gaps.append("mrp_shortage_date_conflict")
            else:
                shortage_status = "none"
                shortage_priority = "none"
        elif shortage_start is None:
            evidence_gaps.append("mrp_shortage_start_date_evidence")
        elif shortage_start <= analysis_date or (
            days_of_supply is not None and days_of_supply <= 0
        ):
            shortage_status = "active"
            shortage_priority = "critical"
        elif replenishment_lead_end is None:
            evidence_gaps.append("mrp_replenishment_lead_time_evidence")
        elif shortage_start <= replenishment_lead_end:
            shortage_status = "imminent"
            shortage_priority = "high"
        else:
            shortage_status = "future"
            shortage_priority = "medium"

    if coverage_row and not coverage_unit:
        evidence_gaps.append("mrp_coverage_unit_evidence")
    if (
        master_unit
        and coverage_unit
        and comparable_unit(master_unit) != comparable_unit(coverage_unit)
    ):
        evidence_gaps.append("mrp_master_coverage_unit_conflict")

    priority_rank = {
        "unknown": -1,
        "none": 0,
        "low": 1,
        "medium": 2,
        "high": 3,
        "critical": 4,
    }
    exception_types = {
        "06": "start_date_past",
        "07": "finish_date_past",
        "10": "reschedule_in",
        "15": "reschedule_out",
        "20": "cancel",
        "26": "reduce",
        "30": "schedule_adjusted",
    }
    exception_type_labels = {
        "start_date_past": {"zh": "开始日期已过", "en": "Start date is in the past"},
        "finish_date_past": {"zh": "完成日期已过", "en": "Finish date is in the past"},
        "reschedule_in": {"zh": "建议提前处理", "en": "Bring process forward"},
        "reschedule_out": {"zh": "建议推迟处理", "en": "Postpone process"},
        "cancel": {"zh": "建议取消", "en": "Cancel process"},
        "reduce": {"zh": "建议减少数量", "en": "Reduce quantity"},
        "schedule_adjusted": {"zh": "按调整后的计划处理", "en": "Process according to adjusted schedule"},
        "other_exception": {"zh": "其他 SAP MRP 异常", "en": "Other SAP MRP exception"},
    }
    priority_labels = {
        "critical": {"zh": "紧急", "en": "Critical"},
        "high": {"zh": "高", "en": "High"},
        "medium": {"zh": "中", "en": "Medium"},
        "low": {"zh": "低", "en": "Low"},
        "none": {"zh": "无", "en": "None"},
        "unknown": {"zh": "无法确认", "en": "Unknown"},
    }
    actions_by_type = {
        "start_date_past": {
            "zh": "核对该未结 MRP 元素的实际开始状态和过期日期。",
            "en": "Review the open MRP element's actual start status and overdue date.",
        },
        "finish_date_past": {
            "zh": "核对该未结 MRP 元素是否已完成、收货或需要重新排期。",
            "en": "Check whether the open MRP element is complete, received, or requires rescheduling.",
        },
        "reschedule_in": {
            "zh": "评估把现有供应提前到 SAP 建议日期，以覆盖较早需求。",
            "en": "Assess bringing the existing receipt forward to the SAP-proposed date to cover earlier demand.",
        },
        "reschedule_out": {
            "zh": "确认后续需求后，再评估推迟过早到货的供应。",
            "en": "Confirm downstream demand before postponing an early receipt.",
        },
        "cancel": {
            "zh": "确认不存在后续需求后，再评估取消多余供应。",
            "en": "Confirm that no downstream demand remains before cancelling excess supply.",
        },
        "reduce": {
            "zh": "复核净需求后，再评估减少多余供应数量。",
            "en": "Review net requirements before reducing excess receipt quantity.",
        },
        "schedule_adjusted": {
            "zh": "复核计划时界、提前期和调整后的日期是否符合业务要求。",
            "en": "Review the planning time fence, lead time, and adjusted date against business requirements.",
        },
        "other_exception": {
            "zh": "由 MRP 控制员根据 SAP 原始消息复核该元素。",
            "en": "Have the MRP controller review the element using the original SAP message.",
        },
    }

    exception_details: list[JsonObject] = []
    for row in elements:
        if row not in scoped_elements:
            continue
        material = _text(row, "Material")
        plant = _text(row, "MRPPlant", "Plant")
        category = _text(row, "MRPElementCategory")
        element = _text(row, "MRPElement", "MRPElementType")
        if not element and category == "WB":
            element = "_STOCK"
        elif not element:
            element = category
        open_quantity = strict_decimal(row.get("MRPElementOpenQuantity"))
        available_quantity = strict_decimal(row.get("MRPAvailableQuantity"))
        unit = _text(row, "MaterialBaseUnit", "BaseUnit")
        if row.get("MRPElementOpenQuantity") not in {None, ""} and open_quantity is None:
            evidence_gaps.append("mrp_open_quantity_evidence")
        if row.get("MRPAvailableQuantity") not in {None, ""} and available_quantity is None:
            evidence_gaps.append("mrp_available_quantity_evidence")
        if (open_quantity is not None or available_quantity is not None) and not unit:
            evidence_gaps.append("mrp_supply_demand_unit_evidence")
        if (
            unit
            and coverage_unit
            and comparable_unit(unit) != comparable_unit(coverage_unit)
        ):
            evidence_gaps.append("mrp_supply_demand_unit_conflict")
        for number_field, text_field in (
            ("ExceptionMessageNumber", "ExceptionMessageText"),
            ("ExceptionMessageNumber2", "ExceptionMessageText2"),
        ):
            number = _text(row, number_field)
            message = _text(row, text_field)
            if not number and not message:
                continue
            if not material or not plant or not element or not category:
                evidence_gaps.append("mrp_exception_business_key_evidence")
            exception_type = exception_types.get(number, "other_exception")
            if exception_type == "reschedule_in":
                exception_priority = "high"
            elif exception_type in {"start_date_past", "finish_date_past"}:
                exception_priority = (
                    "high" if open_quantity is not None and open_quantity > 0 else "medium"
                )
            elif exception_type in {"reschedule_out", "cancel", "reduce", "other_exception"}:
                exception_priority = "medium"
            else:
                exception_priority = "low"
            exception_details.append(
                {
                    "material": material,
                    "plant": plant,
                    "mrp_area": _text(row, "MRPArea"),
                    "mrp_element": element,
                    "mrp_element_item": _text(row, "MRPElementItem") or None,
                    "mrp_element_schedule_line": _text(row, "MRPElementScheduleLine") or None,
                    "element_category": category,
                    "element_category_name": _text(
                        row, "MRPElementCategoryName", "MRPElementCategoryShortName"
                    ) or None,
                    "requirement_or_receipt_date": _date_text(
                        row, "MRPElementAvailyOrRqmtDate", "RequirementDate"
                    ) or None,
                    "open_quantity": str(open_quantity) if open_quantity is not None else None,
                    "available_quantity": str(available_quantity) if available_quantity is not None else None,
                    "unit": unit or None,
                    "exception_number": number or None,
                    "exception_type": exception_type,
                    "exception_type_label": exception_type_labels[exception_type],
                    "sap_exception_text": message or None,
                    "rescheduling_date": _date_text(row, "MRPElementReschedulingDate") or None,
                    "priority_level": exception_priority,
                    "priority_label": priority_labels[exception_priority],
                    "recommended_action": actions_by_type[exception_type],
                }
            )

    deduplicated_details: list[JsonObject] = []
    seen_exception_keys: set[tuple[Any, ...]] = set()
    for item in exception_details:
        key = (
            item.get("material"),
            item.get("plant"),
            item.get("mrp_area"),
            item.get("mrp_element"),
            item.get("mrp_element_item"),
            item.get("mrp_element_schedule_line"),
            item.get("exception_number"),
            item.get("sap_exception_text"),
        )
        if key not in seen_exception_keys:
            deduplicated_details.append(item)
            seen_exception_keys.add(key)
    exception_details = deduplicated_details

    exception_priorities = [str(item["priority_level"]) for item in exception_details]
    confirmed_priorities = [
        value for value in [shortage_priority, *exception_priorities] if value != "unknown"
    ]
    priority_level = (
        max(confirmed_priorities, key=priority_rank.get)
        if confirmed_priorities
        else "unknown"
    )
    rescheduling_types = {
        str(item["exception_type"])
        for item in exception_details
        if item.get("exception_type")
        in {"reschedule_in", "reschedule_out", "cancel", "reduce"}
    }
    if not rescheduling_types:
        rescheduling_status = "none"
    elif rescheduling_types == {"reschedule_in"}:
        rescheduling_status = "bring_forward"
    elif rescheduling_types == {"reschedule_out"}:
        rescheduling_status = "postpone"
    elif rescheduling_types <= {"cancel", "reduce"}:
        rescheduling_status = "cancel_or_reduce"
    else:
        rescheduling_status = "mixed"

    evidence_gaps = _gaps(inputs, *evidence_gaps)
    source_complete = master_complete and coverage_complete and elements_complete
    evidence_complete = source_complete and not evidence_gaps
    if not evidence_complete:
        business_status = "inconclusive"
    elif priority_level == "critical":
        business_status = "critical"
    elif priority_level in {"high", "medium", "low"}:
        business_status = "attention"
    elif priority_level == "none":
        business_status = "normal"
    else:
        business_status = "inconclusive"

    unit = coverage_unit or master_unit or None
    shortage_quantity_text = str(shortage_quantity) if shortage_quantity is not None else None
    shortage_start_text = shortage_start.isoformat() if shortage_start else None
    shortage_end_text = shortage_end.isoformat() if shortage_end else None
    shortage_status_labels = {
        "active": {"zh": "短缺已经发生", "en": "Shortage is active"},
        "imminent": {"zh": "短缺即将进入补货窗口", "en": "Shortage is within the replenishment window"},
        "future": {"zh": "未来存在短缺", "en": "Future shortage exists"},
        "none": {"zh": "当前范围未发现短缺", "en": "No shortage found in the current scope"},
        "unknown": {"zh": "短缺状态无法确认", "en": "Shortage status is unknown"},
    }
    rescheduling_labels = {
        "bring_forward": {"zh": "需要评估提前供应", "en": "Bring-forward review required"},
        "postpone": {"zh": "需要评估推迟供应", "en": "Postponement review required"},
        "cancel_or_reduce": {"zh": "需要评估取消或减少供应", "en": "Cancellation or reduction review required"},
        "mixed": {"zh": "存在多种重排建议", "en": "Mixed rescheduling proposals exist"},
        "none": {"zh": "没有重排类消息", "en": "No rescheduling message"},
        "unknown": {"zh": "重排状态无法确认", "en": "Rescheduling status is unknown"},
    }
    affected_elements = {
        (
            item.get("mrp_element"),
            item.get("mrp_element_item"),
            item.get("mrp_element_schedule_line"),
        )
        for item in exception_details
    }

    if business_status == "critical":
        headline_zh = f"物料 {expected_material} 的短缺已经发生，需要立即处理"
        headline_en = f"The shortage for material {expected_material} is active and requires immediate action"
    elif business_status == "attention":
        headline_zh = f"物料 {expected_material} 存在{priority_labels[priority_level]['zh']}优先级 MRP 异常"
        headline_en = f"Material {expected_material} has {priority_labels[priority_level]['en'].lower()}-priority MRP exceptions"
    elif business_status == "normal":
        headline_zh = f"当前 SAP 短缺参数和时间范围内未发现物料 {expected_material} 的 MRP 异常"
        headline_en = f"No MRP exception was found for material {expected_material} within the current SAP shortage profile and horizon"
    elif priority_level not in {"unknown", "none"}:
        headline_zh = f"已确认物料 {expected_material} 存在{priority_labels[priority_level]['zh']}优先级风险，但证据仍不完整"
        headline_en = f"A {priority_labels[priority_level]['en'].lower()}-priority risk is confirmed for material {expected_material}, but evidence remains incomplete"
    else:
        headline_zh = f"无法完整判断物料 {expected_material} 的 MRP 异常"
        headline_en = f"MRP exceptions for material {expected_material} are inconclusive"

    findings: list[JsonObject] = []
    if shortage_status in {"active", "imminent", "future"}:
        findings.append(
            {
                "code": f"MRP_SHORTAGE_{shortage_status.upper()}",
                "severity": shortage_priority,
                "quantity": shortage_quantity_text,
                "unit": unit,
                "detail": {
                    "zh": f"短缺状态：{shortage_status_labels[shortage_status]['zh']}；数量 {shortage_quantity_text or '未确认'} {unit or ''}。",
                    "en": f"Shortage status: {shortage_status_labels[shortage_status]['en']}; quantity {shortage_quantity_text or 'unknown'} {unit or ''}.",
                },
            }
        )
    for item in exception_details:
        findings.append(
            {
                "code": f"MRP_EXCEPTION_{item.get('exception_number') or 'OTHER'}",
                "severity": item["priority_level"],
                "mrp_element": item["mrp_element"],
                "detail": {
                    "zh": f"MRP 元素 {item['mrp_element']}：{item['exception_type_label']['zh']}。",
                    "en": f"MRP element {item['mrp_element']}: {item['exception_type_label']['en']}.",
                },
            }
        )

    next_actions_zh: list[str] = []
    next_actions_en: list[str] = []
    if shortage_status == "active":
        next_actions_zh.append("立即复核最早未覆盖需求和可提前的固定供应。")
        next_actions_en.append("Immediately review the earliest uncovered demand and fixed receipts that can be brought forward.")
    elif shortage_status in {"imminent", "future"}:
        next_actions_zh.append("在短缺日期前复核补货方案和供应日期。")
        next_actions_en.append("Review replenishment options and receipt dates before the shortage date.")
    for item in exception_details:
        action = item["recommended_action"]
        if action["zh"] not in next_actions_zh:
            next_actions_zh.append(action["zh"])
        if action["en"] not in next_actions_en:
            next_actions_en.append(action["en"])

    limitations: list[str] = []
    if time_horizon_days is not None:
        limitations.append("sap_shortage_time_horizon_applies")
    if accepted_shortage:
        limitations.append("accepted_shortage_not_returned_as_first")

    summary_row = {
        "analysis_date": analysis_date.isoformat(),
        "material": expected_material,
        "plant": expected_plant,
        "mrp_area": expected_area,
        "shortage_status": shortage_status,
        "shortage_status_label": shortage_status_labels[shortage_status],
        "shortage_quantity": shortage_quantity_text,
        "unit": unit,
        "shortage_start_date": shortage_start_text,
        "shortage_end_date": shortage_end_text,
        "days_of_supply": days_of_supply,
        "time_horizon_days": time_horizon_days,
        "accepted_shortage": accepted_shortage,
        "priority_level": priority_level,
        "priority_label": priority_labels[priority_level],
        "rescheduling_status": rescheduling_status,
        "rescheduling_status_label": rescheduling_labels[rescheduling_status],
        "affected_element_count": len(affected_elements),
        "exception_count": len(exception_details),
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
    }

    result = _result(
        inputs,
        business_status=business_status,
        headline_zh=headline_zh,
        headline_en=headline_en,
        overview_zh="业务处理优先级由 SAPBusinessAgents 的版本化确定性规则计算；SAP 原始异常消息单独保留。本 Agent 只提供诊断建议，不运行 MRP 或修改单据。",
        overview_en="Business priority is calculated by a versioned SAPBusinessAgents deterministic rule, while original SAP exception messages are preserved separately. This Agent provides diagnosis only and never runs MRP or changes documents.",
        stages=[
            _stage("master", "MRP 主数据", "MRP master", len(scoped_master), state="confirmed" if master_complete and scoped_master else "unknown"),
            _stage("coverage", "物料覆盖与短缺", "Material coverage and shortage", len(scoped_coverage), state="attention" if shortage_status in {"active", "imminent", "future"} else "confirmed" if shortage_status == "none" else "unknown", detail_zh=f"{shortage_status_labels[shortage_status]['zh']}；短缺数量 {shortage_quantity_text or '无法确认'} {unit or ''}。", detail_en=f"{shortage_status_labels[shortage_status]['en']}; shortage quantity {shortage_quantity_text or 'unknown'} {unit or ''}."),
            _stage("elements", "供需项目", "Supply-demand items", len(scoped_elements), state="confirmed" if elements_complete else "unknown"),
            _stage("exceptions", "MRP 异常分类", "MRP exception classification", len(exception_details), state="attention" if exception_details else "confirmed" if evidence_complete else "unknown", detail_zh=f"识别到 {len(exception_details)} 条异常消息，影响 {len(affected_elements)} 个 MRP 元素。", detail_en=f"Identified {len(exception_details)} exception message(s) affecting {len(affected_elements)} MRP element(s)."),
            _stage("completeness", "数据完整性", "Data completeness", 1 if evidence_complete else 0, state="confirmed" if evidence_complete else "unknown", detail_zh="MRP 主数据、覆盖、供需分页、业务键和单位均完整。" if evidence_complete else "至少一项查询、分页、业务键、日期或单位证据不完整。", detail_en="MRP master, coverage, supply-demand paging, business keys, and units are complete." if evidence_complete else "At least one query, page, business key, date, or unit is incomplete."),
        ],
        findings=findings,
        metrics=[
            {"id": "shortage_quantity", "label": {"zh": "短缺数量", "en": "Shortage quantity"}, "value": shortage_quantity_text, "unit": unit},
            {"id": "days_of_supply", "label": {"zh": "库存覆盖天数", "en": "Days of supply"}, "value": days_of_supply},
            {"id": "exception_count", "label": {"zh": "异常消息数", "en": "Exception messages"}, "value": len(exception_details)},
            {"id": "affected_element_count", "label": {"zh": "受影响 MRP 元素数", "en": "Affected MRP elements"}, "value": len(affected_elements)},
        ],
        gaps=evidence_gaps,
        limitations=limitations,
        records=exception_details,
        record_columns=[
            {"key": "mrp_element", "label": {"zh": "MRP 元素", "en": "MRP element"}},
            {"key": "mrp_element_item", "label": {"zh": "行项目", "en": "Item"}},
            {"key": "element_category", "label": {"zh": "元素类别", "en": "Element category"}},
            {"key": "requirement_or_receipt_date", "label": {"zh": "需求/供应日期", "en": "Requirement/receipt date"}},
            {"key": "open_quantity", "label": {"zh": "未结数量", "en": "Open quantity"}},
            {"key": "unit", "label": {"zh": "单位", "en": "Unit"}},
            {"key": "exception_number", "label": {"zh": "SAP 异常编号", "en": "SAP exception number"}},
            {"key": "exception_type_label", "label": {"zh": "异常类型", "en": "Exception type"}},
            {"key": "sap_exception_text", "label": {"zh": "SAP 原文", "en": "Original SAP message"}},
            {"key": "priority_label", "label": {"zh": "业务处理优先级", "en": "Business priority"}},
            {"key": "recommended_action", "label": {"zh": "建议处理", "en": "Recommended action"}},
        ],
        allow_empty_records=True,
        actions_zh=next_actions_zh,
        actions_en=next_actions_en,
        source_complete_override=source_complete,
        preserve_business_status_on_gap=True,
    )
    exception_summary: dict[str, JsonObject] = {}
    for item in exception_details:
        exception_type = str(item["exception_type"])
        summary = exception_summary.setdefault(
            exception_type,
            {
                "exception_type": exception_type,
                "exception_type_label": item["exception_type_label"],
                "count": 0,
                "highest_priority": item["priority_level"],
                "highest_priority_label": item["priority_label"],
            },
        )
        summary["count"] = int(summary["count"]) + 1
        if priority_rank[str(item["priority_level"])] > priority_rank[str(summary["highest_priority"])]:
            summary["highest_priority"] = item["priority_level"]
            summary["highest_priority_label"] = item["priority_label"]
    result["business_report"]["evidence_tables"] = [
        {
            "id": "mrp_shortage_summary",
            "title": {"zh": "短缺与优先级结论", "en": "Shortage and priority conclusion"},
            "columns": [
                {"key": "analysis_date", "label": {"zh": "分析日期", "en": "Analysis date"}, "format": "date"},
                {"key": "shortage_status_label", "label": {"zh": "短缺状态", "en": "Shortage status"}},
                {"key": "shortage_quantity", "label": {"zh": "短缺数量", "en": "Shortage quantity"}, "format": "decimal"},
                {"key": "unit", "label": {"zh": "单位", "en": "Unit"}},
                {"key": "shortage_start_date", "label": {"zh": "短缺开始日期", "en": "Shortage start date"}, "format": "date"},
                {"key": "days_of_supply", "label": {"zh": "库存覆盖天数", "en": "Days of supply"}, "format": "integer"},
                {"key": "priority_label", "label": {"zh": "业务处理优先级", "en": "Business priority"}},
                {"key": "rescheduling_status_label", "label": {"zh": "重排状态", "en": "Rescheduling status"}},
            ],
            "rows": [summary_row],
        },
        {
            "id": "mrp_exception_summary",
            "title": {"zh": "异常类型汇总", "en": "Exception type summary"},
            "columns": [
                {"key": "exception_type_label", "label": {"zh": "异常类型", "en": "Exception type"}},
                {"key": "count", "label": {"zh": "消息数", "en": "Messages"}, "format": "integer"},
                {"key": "highest_priority_label", "label": {"zh": "最高业务优先级", "en": "Highest business priority"}},
            ],
            "rows": list(exception_summary.values()),
        },
        {
            "id": "mrp_exception_details",
            "title": {"zh": "受影响的 MRP 元素", "en": "Affected MRP elements"},
            "columns": [
                {"key": "mrp_element", "label": {"zh": "MRP 元素", "en": "MRP element"}},
                {"key": "mrp_element_item", "label": {"zh": "行项目", "en": "Item"}},
                {"key": "element_category_name", "label": {"zh": "元素类别", "en": "Element category"}},
                {"key": "requirement_or_receipt_date", "label": {"zh": "需求/供应日期", "en": "Requirement/receipt date"}, "format": "date"},
                {"key": "open_quantity", "label": {"zh": "未结数量", "en": "Open quantity"}, "format": "decimal"},
                {"key": "unit", "label": {"zh": "单位", "en": "Unit"}},
                {"key": "exception_number", "label": {"zh": "SAP 异常编号", "en": "SAP exception number"}},
                {"key": "exception_type_label", "label": {"zh": "异常类型", "en": "Exception type"}},
                {"key": "sap_exception_text", "label": {"zh": "SAP 原文", "en": "Original SAP message"}},
                {"key": "rescheduling_date", "label": {"zh": "SAP 重排日期", "en": "SAP rescheduling date"}, "format": "date"},
                {"key": "priority_label", "label": {"zh": "业务处理优先级", "en": "Business priority"}},
                {"key": "recommended_action", "label": {"zh": "建议处理", "en": "Recommended action"}},
            ],
            "rows": exception_details,
        },
    ]
    result["rule_id"] = "mrp_exception_analysis_deterministic_v2"
    result["status"] = "complete" if evidence_complete else "inconclusive"
    result["business_status"] = business_status
    result["business_complete"] = evidence_complete
    result["source_complete"] = source_complete
    result["evidence_complete"] = evidence_complete
    result["missing_evidence"] = evidence_gaps
    result["business_report"]["tone"] = (
        "success" if business_status == "normal" else "warning" if business_status in {"critical", "attention"} else "info"
    )
    result["business_report"]["source_complete"] = source_complete
    result["business_report"]["evidence_complete"] = evidence_complete
    result["business_report"]["missing_evidence"] = evidence_gaps
    result["workflow_output"].update(summary_row)
    result["workflow_output"].update(
        {
            "shortage_profile": expected_profile,
            "shortage_counter": expected_counter,
            "shortage_end_date": shortage_end_text,
            "rescheduling_status": rescheduling_status,
            "exception_details": exception_details,
            "business_status": business_status,
            "source_complete": source_complete,
            "evidence_complete": evidence_complete,
        }
    )
    return result


def _production_monitor(inputs: JsonObject) -> JsonObject:
    orders = _rows(inputs, "production_order", "production_order_items")
    statuses = _rows(inputs, "production_statuses")
    operations = _rows(inputs, "production_operations")
    components = _rows(inputs, "production_components")
    movements = _rows(inputs, "material_documents")
    attention = any(not _truthy(row.get("OperationIsConfirmed")) for row in operations)
    records = [
        {
            "manufacturing_order": _text(row, "ManufacturingOrder", "ProductionOrder"),
            "operation": _text(row, "ManufacturingOrderOperation", "Operation"),
            "work_center": _text(row, "WorkCenter", "WorkCenterInternalID"),
            "confirmed": _truthy(row.get("OperationIsConfirmed")),
            "planned_start": _date_text(row, "OpErlstSchedldExecStrtDte", "OpPlannedStartDate", "OperationPlannedStartDate"),
            "planned_end": _date_text(row, "OpErlstSchedldExecEndDte", "OpPlannedEndDate", "OperationPlannedEndDate"),
        }
        for row in operations
        if _text(row, "ManufacturingOrder", "ProductionOrder")
        and _text(row, "ManufacturingOrderOperation", "Operation")
    ]
    return _result(
        inputs,
        business_status="attention" if attention else "normal",
        headline_zh="生产订单仍有未确认工序" if attention else "生产订单执行证据已完整取得",
        headline_en="The production order has unconfirmed operations" if attention else "Production-order execution evidence was collected",
        overview_zh="已核对订单状态、工序确认、组件领料和物料凭证，不执行确认、发料、收货或 TECO。",
        overview_en="Order status, operation confirmation, component withdrawal, and material documents were checked without confirmation, issue, receipt, or TECO actions.",
        stages=[_stage("order", "生产订单", "Production order", len(orders)), _stage("status", "订单状态", "Order status", len(statuses)), _stage("operations", "生产工序", "Operations", len(operations)), _stage("components", "组件", "Components", len(components)), _stage("movements", "物料凭证", "Material documents", len(movements))],
        metrics=[
            {"id": "operation_rows", "value": len(operations)},
            {"id": "unconfirmed_operations", "value": sum(1 for row in operations if not _truthy(row.get("OperationIsConfirmed")))},
            {"id": "movement_rows", "value": len(movements)},
        ],
        records=records,
        actions_zh=["由生产人员复核未确认工序、缺料和待收货状态。"] if attention else [],
        actions_en=["Have production review unconfirmed operations, shortages, and pending receipts."] if attention else [],
    )


def _production_schedule(inputs: JsonObject) -> JsonObject:
    planned = _rows(inputs, "planned_orders", "planned_capacities", "planned_pipeline_operations")
    operations = _rows(inputs, "production_operations")
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    date_from = _date(run_input.get("date_from"))
    date_to = _date(run_input.get("date_to"))
    if date_from and date_to:
        operations = [
            row
            for row in operations
            if any(
                value is not None and date_from <= value <= date_to
                for value in (
                    _date(row.get("OpActualExecutionStartDate")),
                    _date(row.get("OpActualExecutionEndDate")),
                )
            )
        ]
    centers = _rows(inputs, "work_centers", "work_center_capacities")
    buckets = _rows(inputs, "capacity_buckets")
    gaps = _gaps(inputs, *("complete_capacity_bucket_evidence",) if not buckets else ())
    record_source = buckets if buckets else planned
    records = [
        {
            "plant": _text(row, "Plant"),
            "work_center": _text(row, "WorkCenter", "WorkCenterInternalID"),
            "capacity_date": _date_text(row, "CapacityEvaluationTimePeriod", "OperationLatestStartDate", "CapacityDate", "StartDate", "ValidityStartDate"),
            "required_capacity": _text(row, "RemainingCapReqExecutionDurn", "CapacityRequirement", "RequiredCapacity"),
            "available_capacity": _text(row, "WorkCenterAvailableCapacity", "AvailableCapacity"),
            "unit": _text(row, "CapacityRequirementUnit", "WorkCenterCapacityUnit", "CapacityUnit", "Unit"),
        }
        for row in record_source
        if _text(row, "Plant")
        and _text(row, "WorkCenter", "WorkCenterInternalID")
        and _date_text(row, "CapacityEvaluationTimePeriod", "OperationLatestStartDate", "CapacityDate", "StartDate", "ValidityStartDate")
    ]
    return _result(
        inputs,
        business_status="attention" if buckets else "capability_blocked",
        headline_zh="已取得排程对象和完整产能桶" if buckets else "已取得排程对象，但缺少完整产能桶",
        headline_en="Scheduling objects and complete capacity buckets were collected" if buckets else "Scheduling objects were collected, but complete capacity buckets are missing",
        overview_zh="只有计划负荷和可用产能按同一工作中心、日期及单位完整返回时才计算利用率。",
        overview_en="Utilization is calculated only when planned load and available capacity are complete for the same work center, dates, and units.",
        stages=[_stage("planned", "计划订单与产能需求", "Planned orders and requirements", len(planned)), _stage("operations", "生产工序", "Production operations", len(operations)), _stage("centers", "工作中心", "Work centers", len(centers)), _stage("buckets", "产能桶", "Capacity buckets", len(buckets), state="confirmed" if buckets else "unknown")],
        metrics=[
            {"id": "planned_rows", "value": 0},
            {"id": "operation_rows", "value": len(planned) + len(operations)},
            {"id": "capacity_bucket_rows", "value": len(buckets)},
        ],
        gaps=gaps,
        records=records,
        allow_empty_records=True,
        actions_zh=["补齐产能桶后由计划人员人工比较排程方案。"],
        actions_en=["Complete capacity-bucket evidence and have planners compare scheduling scenarios."],
    )


def _production_variance(inputs: JsonObject) -> JsonObject:
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    requested_order = str(run_input.get("manufacturing_order") or "").strip()
    headers = _rows(inputs, "production_order_header")
    items = _rows(inputs, "production_order_items")
    operations = _rows(inputs, "production_operations")
    components = _rows(inputs, "production_components")
    movements = _rows(inputs, "material_documents")
    source_complete = _source_complete(inputs)
    gaps = set(_gaps(inputs))

    def scoped(rows: list[JsonObject]) -> list[JsonObject]:
        matched: list[JsonObject] = []
        for row in rows:
            row_order = _text(row, "ManufacturingOrder")
            if requested_order and row_order and row_order != requested_order:
                gaps.add("production_order_scope_mismatch")
                continue
            matched.append(row)
        return matched

    headers = scoped(headers)
    items = scoped(items)
    operations = scoped(operations)
    components = scoped(components)
    movements = scoped(movements)

    header = headers[0] if len(headers) == 1 else None
    if len(headers) > 1:
        gaps.add("production_order_header_conflict")
    order_found = header is not None or bool(items or operations or components or movements)
    if order_found and header is None:
        gaps.add("production_order_header_missing")
    teco = _truthy(header.get("OrderIsTechnicallyCompleted")) if header else False
    teco_status = "confirmed" if teco else "not_confirmed" if header else "unknown"

    comparable_item = items[0] if len(items) == 1 else None
    if len(items) > 1:
        gaps.add("multiple_production_order_items_not_comparable")
    elif order_found and not items:
        gaps.add("production_order_item_missing")
    planned = _strict_decimal(comparable_item.get("MfgOrderItemPlannedTotalQty")) if comparable_item else None
    received = _strict_decimal(comparable_item.get("MfgOrderItemGoodsReceiptQty")) if comparable_item else None
    production_unit = _unit_key(
        (comparable_item or {}).get("ProductionUnit") or (header or {}).get("ProductionUnit")
    )
    if comparable_item and (planned is None or received is None or not production_unit):
        gaps.add("production_quantity_evidence_incomplete")

    receipt_variance = received - planned if received is not None and planned is not None else None
    receipt_variance_percent = (
        receipt_variance / abs(planned) * Decimal(100)
        if receipt_variance is not None and planned not in {None, Decimal(0)}
        else None
    )
    quantity_status = (
        "unknown"
        if receipt_variance is None
        else "matched"
        if receipt_variance == 0
        else "short_receipt"
        if receipt_variance < 0
        else "over_receipt"
    )

    operation_keys = [_text(row, "ManufacturingOrderOperation") for row in operations]
    if operations and (any(not item for item in operation_keys) or len(set(operation_keys)) != len(operation_keys)):
        gaps.add("production_operation_sequence_incomplete")

    def operation_sort_key(row: JsonObject) -> tuple[int, int | str]:
        value = _text(row, "ManufacturingOrderOperation")
        return (0, int(value)) if value.isdigit() else (1, value)

    final_operation = sorted(operations, key=operation_sort_key)[-1] if operations else None
    if order_found and final_operation is None:
        gaps.add("final_production_operation_missing")
    final_confirmed = bool(final_operation and _truthy(final_operation.get("OperationIsConfirmed")))
    final_partial = bool(final_operation and _truthy(final_operation.get("OperationIsPartiallyConfirmed")))
    confirmed_yield = (
        _strict_decimal(final_operation.get("OpTotalConfirmedYieldQty"))
        if final_operation and final_confirmed and not final_partial
        else None
    )
    final_operation_unit = _unit_key((final_operation or {}).get("OperationUnit"))
    if confirmed_yield is not None and not final_operation_unit:
        confirmed_yield = None
        gaps.add("production_operation_unit_missing")
    if confirmed_yield is not None and final_operation_unit and production_unit and final_operation_unit != production_unit:
        confirmed_yield = None
        gaps.add("production_operation_unit_conflict")
    operation_status = (
        "unknown"
        if not final_operation
        else "partial"
        if final_partial
        else "confirmed"
        if final_confirmed and confirmed_yield is not None
        else "not_confirmed"
    )

    component_details: list[JsonObject] = []
    component_variance_count = 0
    for row in components:
        required = _strict_decimal(row.get("RequiredQuantity"))
        withdrawn = _strict_decimal(row.get("WithdrawnQuantity"))
        unit = _unit_key(row.get("BaseUnit"))
        if required is None or withdrawn is None or not unit:
            gaps.add("production_component_evidence_incomplete")
            variance = None
            status = "unknown"
        else:
            variance = withdrawn - required
            status = "matched" if variance == 0 else "variance"
            if variance != 0:
                component_variance_count += 1
        component_details.append(
            {
                "reservation": _text(row, "Reservation"),
                "reservation_item": _text(row, "ReservationItem"),
                "material": _text(row, "Material"),
                "required_quantity": str(required) if required is not None else None,
                "withdrawn_quantity": str(withdrawn) if withdrawn is not None else None,
                "variance_quantity": str(variance) if variance is not None else None,
                "unit": unit or None,
                "status": status,
            }
        )
    component_status = (
        "not_applicable"
        if not components
        else "unknown"
        if any(item["status"] == "unknown" for item in component_details)
        else "variance"
        if component_variance_count
        else "matched"
    )

    movement_details: list[JsonObject] = []
    reversal_count = 0
    receipt_movement_total = Decimal(0)
    receipt_movement_comparable = True
    recognized_movement_count = 0
    movement_keys: set[tuple[str, str, str]] = set()
    for row in movements:
        movement_type = _text(row, "GoodsMovementType")
        quantity = _strict_decimal(row.get("QuantityInBaseUnit"))
        unit = _unit_key(row.get("MaterialBaseUnit"))
        direction = Decimal(1) if movement_type in {"101", "261"} else Decimal(-1) if movement_type in {"102", "262"} else None
        if direction is not None:
            recognized_movement_count += 1
        if movement_type in {"102", "262"}:
            reversal_count += 1
            if not _text(row, "ReversedMaterialDocument"):
                gaps.add("material_movement_reversal_reference_incomplete")
        movement_key = (
            _text(row, "MaterialDocumentYear"),
            _text(row, "MaterialDocument"),
            _text(row, "MaterialDocumentItem"),
        )
        if not all(movement_key) or movement_key in movement_keys:
            gaps.add("material_movement_business_key_incomplete")
        movement_keys.add(movement_key)
        if movement_type in {"101", "102"}:
            if quantity is None or not unit or (production_unit and unit != production_unit):
                receipt_movement_comparable = False
                gaps.add("goods_receipt_movement_unit_conflict")
            else:
                receipt_movement_total += quantity * (direction or Decimal(0))
        movement_details.append(
            {
                "material_document_year": _text(row, "MaterialDocumentYear"),
                "material_document": _text(row, "MaterialDocument"),
                "material_document_item": _text(row, "MaterialDocumentItem"),
                "material": _text(row, "Material"),
                "movement_type": movement_type,
                "quantity": str(quantity) if quantity is not None else None,
                "unit": unit or None,
                "debit_credit_code": _text(row, "DebitCreditCode"),
                "reversed_document": _text(row, "ReversedMaterialDocument"),
                "reversed_document_year": _text(row, "ReversedMaterialDocumentYear"),
                "reversed_document_item": _text(row, "ReversedMaterialDocumentItem"),
            }
        )
    if received is not None and movements and receipt_movement_comparable and receipt_movement_total != received:
        gaps.add("goods_receipt_document_mismatch")
    if received not in {None, Decimal(0)} and not movements:
        gaps.add("goods_receipt_movement_evidence_missing")
    movement_status = (
        "no_activity"
        if not movements
        else "reversal_present"
        if reversal_count
        else "documented"
        if recognized_movement_count
        else "supporting_only"
    )

    root_causes: list[JsonObject] = []
    if confirmed_yield is not None and received is not None:
        if planned is not None and confirmed_yield == planned and received < confirmed_yield:
            root_causes.append(
                {
                    "code": "receipt_shortfall_after_confirmation",
                    "severity": "high",
                    "confirmed_yield_quantity": str(confirmed_yield),
                    "goods_receipt_quantity": str(received),
                }
            )
        elif planned is not None and confirmed_yield < planned:
            root_causes.append(
                {
                    "code": "production_yield_shortfall",
                    "severity": "high",
                    "planned_quantity": str(planned),
                    "confirmed_yield_quantity": str(confirmed_yield),
                }
            )
        elif planned is not None and confirmed_yield > planned:
            root_causes.append(
                {
                    "code": "production_over_confirmation",
                    "severity": "medium",
                    "planned_quantity": str(planned),
                    "confirmed_yield_quantity": str(confirmed_yield),
                }
            )
    if component_variance_count:
        root_causes.append(
            {
                "code": "component_issue_variance",
                "severity": "medium",
                "affected_component_count": component_variance_count,
            }
        )
    if reversal_count:
        root_causes.append(
            {
                "code": "material_movement_reversal_effect",
                "severity": "medium",
                "reversal_count": reversal_count,
            }
        )

    evidence_complete = bool(source_complete and not gaps and order_found)
    has_variance = bool(
        receipt_variance not in {None, Decimal(0)}
        or component_variance_count
        or reversal_count
        or root_causes
    )
    business_status = (
        "inconclusive"
        if not source_complete or gaps
        else "not_found"
        if not order_found
        else "in_progress"
        if not teco
        else "attention"
        if has_variance
        else "normal"
    )
    material = _text(comparable_item or {}, "Material") or _text(header or {}, "Material")
    plant = _text(comparable_item or {}, "ProductionPlant", "Plant") or _text(header or {}, "ProductionPlant", "Plant")
    headline_zh = (
        f"生产已确认 {confirmed_yield} {production_unit}，但库存只收到 {received} {production_unit}"
        if any(item.get("code") == "receipt_shortfall_after_confirmation" for item in root_causes)
        else "生产数量与物料差异检查完成"
        if business_status in {"normal", "attention"}
        else "生产数量与物料差异证据尚不能形成最终结论"
    )
    headline_en = (
        f"Production confirmed {confirmed_yield} {production_unit}, but inventory received only {received} {production_unit}"
        if any(item.get("code") == "receipt_shortfall_after_confirmation" for item in root_causes)
        else "Production quantity and material variance check completed"
        if business_status in {"normal", "attention"}
        else "Production quantity and material evidence is not yet conclusive"
    )
    findings = [dict(item) for item in root_causes]
    result = _result(
        inputs,
        business_status=business_status,
        headline_zh=headline_zh,
        headline_en=headline_en,
        overview_zh="本Agent分别核对计划数量、最终工序确认、成品入库、组件领料和冲销；成本不在本Agent范围内。",
        overview_en="This Agent checks planned quantity, final-operation confirmation, finished-goods receipt, component issues, and reversals separately; cost is outside its scope.",
        stages=[
            _stage("order", "生产订单状态", "Production order status", len(headers), state=teco_status),
            _stage("quantity", "计划与入库数量", "Planned and received quantity", len(items), state=quantity_status),
            _stage("operations", "最终工序确认", "Final operation confirmation", len(operations), state=operation_status),
            _stage("components", "组件领料", "Component issues", len(components), state=component_status),
            _stage("movements", "物料移动与冲销", "Material movements and reversals", len(movements), state=movement_status),
            _stage("cost", "成本分析", "Cost analysis", 0, state="not_assessed"),
        ],
        findings=findings,
        metrics=[
            {"id": "planned_quantity", "value": str(planned) if planned is not None else None, "unit": production_unit or None},
            {"id": "confirmed_yield_quantity", "value": str(confirmed_yield) if confirmed_yield is not None else None, "unit": production_unit or None},
            {"id": "goods_receipt_quantity", "value": str(received) if received is not None else None, "unit": production_unit or None},
            {"id": "receipt_variance_quantity", "value": str(receipt_variance) if receipt_variance is not None else None, "unit": production_unit or None},
            {"id": "component_variance_count", "value": component_variance_count},
            {"id": "reversal_count", "value": reversal_count},
        ],
        gaps=sorted(gaps),
        limitations=["cost_not_assessed"],
        records=component_details + movement_details,
        allow_empty_records=True,
        preserve_business_status_on_gap=True,
        source_complete_override=source_complete,
        actions_zh=["按候选原因复核最终工序确认、成品收货和组件领退料；需要成本分析时运行独立的生产订单成本差异Agent。"],
        actions_en=["Review final-operation confirmation, finished-goods receipt, and component issues/reversals; run the separate production-order cost Agent when cost analysis is required."],
    )
    quantity_detail = {
        "planned_quantity": str(planned) if planned is not None else None,
        "confirmed_yield_quantity": str(confirmed_yield) if confirmed_yield is not None else None,
        "goods_receipt_quantity": str(received) if received is not None else None,
        "receipt_variance_quantity": str(receipt_variance) if receipt_variance is not None else None,
        "receipt_variance_percent": str(receipt_variance_percent) if receipt_variance_percent is not None else None,
        "unit": production_unit or None,
    }
    operation_details = [
        {
            "operation": _text(row, "ManufacturingOrderOperation"),
            "work_center": _text(row, "WorkCenter"),
            "planned_quantity": _text(row, "OpPlannedTotalQuantity"),
            "confirmed_yield_quantity": _text(row, "OpTotalConfirmedYieldQty"),
            "unit": _text(row, "OperationUnit"),
            "confirmed": _truthy(row.get("OperationIsConfirmed")),
            "partially_confirmed": _truthy(row.get("OperationIsPartiallyConfirmed")),
        }
        for row in sorted(operations, key=operation_sort_key)
    ]
    result["rule_id"] = "production_quantity_material_variance_v2"
    result["business_report"].update(
        {
            "quantity_detail": quantity_detail,
            "operation_details": operation_details,
            "component_details": component_details,
            "movement_details": movement_details,
            "root_cause_candidates": root_causes,
        }
    )
    result["workflow_output"].update(
        {
            "manufacturing_order": requested_order,
            "material": material,
            "plant": plant,
            "teco_status": teco_status,
            "production_unit": production_unit,
            "planned_quantity": str(planned) if planned is not None else None,
            "confirmed_yield_quantity": str(confirmed_yield) if confirmed_yield is not None else None,
            "goods_receipt_quantity": str(received) if received is not None else None,
            "receipt_variance_quantity": str(receipt_variance) if receipt_variance is not None else None,
            "receipt_variance_percent": str(receipt_variance_percent) if receipt_variance_percent is not None else None,
            "quantity_status": quantity_status,
            "operation_status": operation_status,
            "component_status": component_status,
            "movement_status": movement_status,
            "cost_status": "not_assessed",
            "component_variance_count": component_variance_count,
            "reversal_count": reversal_count,
            "root_cause_candidates": root_causes,
            "source_complete": source_complete,
            "evidence_complete": evidence_complete,
            "business_status": business_status,
            "business_report": result["business_report"],
        }
    )
    result["business_status"] = business_status
    result["business_complete"] = evidence_complete
    return result


def _material_shortage_procurement(inputs: JsonObject) -> JsonObject:
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    as_of = _date(run_input.get("as_of")) or date.today()
    master = _rows(inputs, "mrp_master")
    # MaterialCoverages is the authoritative shortage aggregate for this Agent.
    # SupplyDemandItems may corroborate the situation, but its receipt and stock
    # rows must never be added to the reported shortage quantity.
    mrp = _rows(inputs, "mrp", "mrp_coverage") + _adt_rows(inputs, "mrp")
    requisitions = _rows(inputs, "pr", "purchase_requisitions") + _adt_rows(inputs, "pr")
    po_items = _rows(inputs, "schedule_po_items") or _rows(inputs, "po", "purchase_orders")
    po_headers = _rows(inputs, "po_headers")
    po_schedules = _rows(inputs, "po_schedules")
    po_receipts = _rows(inputs, "po_receipts")
    receipt_headers = _rows(inputs, "receipt_headers")
    po_fallback_rows = _adt_rows(inputs, "po_schedule")
    if not po_items:
        po_items = [
            row
            for row in po_fallback_rows
            if _text(row, "EBELN") and _text(row, "EBELP") and not _text(row, "ETENR")
        ]
    if not po_schedules:
        po_schedules = [row for row in po_fallback_rows if _text(row, "ETENR")]
    sources = (
        _rows(inputs, "source", "info_records", "contracts", "suppliers")
        + _adt_rows(inputs, "source")
    )
    topic_complete, missing = _required_topics(inputs, "mrp", "pr", "po_schedule", "source")
    pr_complete = _topic_complete(inputs, "pr")
    po_complete = _topic_complete(inputs, "po_schedule")
    source_topic_complete = _topic_complete(inputs, "source")
    evidence_gaps: list[str] = []
    evidence_payloads = inputs.get("evidence")
    po_payload = (
        evidence_payloads.get("po_schedule")
        if isinstance(evidence_payloads, dict)
        else None
    )
    assessment = inputs.get("assessment")
    api_complete = (
        assessment.get("api_complete")
        if isinstance(assessment, dict)
        and isinstance(assessment.get("api_complete"), dict)
        else {}
    )
    if isinstance(po_payload, dict) and api_complete.get("po_schedule") is not False:
        step_results = po_payload.get("step_results")
        if not isinstance(step_results, dict):
            data = po_payload.get("data")
            step_results = data.get("step_results") if isinstance(data, dict) else None
        if isinstance(step_results, dict):
            for required_step in (
                "schedule_po_items",
                "po_headers",
                "po_schedules",
                "po_receipts",
                "receipt_headers",
            ):
                step_result = step_results.get(required_step)
                if not isinstance(step_result, dict):
                    po_complete = False
                    evidence_gaps.append(f"{required_step}_evidence")
                    continue
                if (
                    step_result.get("source_complete") is not True
                    or step_result.get("source_truncated") is True
                    or step_result.get("ok") is False
                ):
                    po_complete = False
                    evidence_gaps.append(f"{required_step}_evidence")
    if not pr_complete and "pr_evidence" not in missing:
        missing.append("pr_evidence")
    if not po_complete and "po_schedule_evidence" not in missing:
        missing.append("po_schedule_evidence")
    if not source_topic_complete and "source_evidence" not in missing:
        missing.append("source_evidence")
    topic_complete = topic_complete and pr_complete and po_complete and source_topic_complete
    external_procurement = any(
        _text(row, "MaterialProcurementCategory", "ProcurementType").upper() == "F"
        for row in master
    )
    if not master:
        missing.append("external_procurement_evidence")
    elif not external_procurement:
        missing.append("external_procurement_scope")
    complete = topic_complete and external_procurement
    units = {
        str(row.get(key) or "").strip()
        for row in mrp
        for key in ("MaterialBaseUnit", "BaseUnit", "Unit")
        if str(row.get(key) or "").strip()
    }
    shortage_rows = [
        row for row in mrp if row.get("MaterialShortageQuantity") not in {None, ""}
    ]
    comparable = complete and len(units) <= 1 and bool(shortage_rows)
    shortage = sum(
        _decimal(row.get("MaterialShortageQuantity"))
        for row in shortage_rows
        if _decimal(row.get("MaterialShortageQuantity")) > 0
    )
    release_labels = {
        "01": {"zh": "版本处理中（01）", "en": "Version in process (01)"},
        "02": {"zh": "活动（02）", "en": "Active (02)"},
        "03": {"zh": "审批中（03）", "en": "In release (03)"},
        "04": {"zh": "等待整体审批（04）", "en": "For overall release (04)"},
        "05": {"zh": "审批完成（05）", "en": "Release completed (05)"},
        "08": {"zh": "审批拒绝（08）", "en": "Release refused (08)"},
    }
    action_labels = {
        "complete_version": {"zh": "完善采购申请", "en": "Complete requisition version"},
        "assign_source": {"zh": "分配货源并处理 PR", "en": "Assign source and process PR"},
        "process_active": {"zh": "处理活动 PR", "en": "Process active PR"},
        "complete_release": {"zh": "完成审批", "en": "Complete release"},
        "ready_to_convert": {"zh": "转换为采购订单", "en": "Convert to purchase order"},
        "handle_rejection": {"zh": "处理拒绝并重新提交", "en": "Resolve rejection and resubmit"},
        "manual_review": {"zh": "人工复核状态", "en": "Review status manually"},
    }
    action_rank = {
        "manual_review": 0,
        "handle_rejection": 1,
        "complete_release": 2,
        "assign_source": 3,
        "process_active": 4,
        "ready_to_convert": 5,
        "complete_version": 6,
    }
    pr_actions: list[JsonObject] = []
    for row in requisitions:
        if _truthy(row.get("IsDeleted")) or _truthy(row.get("IsClosed")):
            continue
        if _text(row, "ProcessingStatus", "STATU").upper() != "N":
            continue
        requested = _strict_decimal(row.get("RequestedQuantity", row.get("MENGE")))
        ordered = _strict_decimal(row.get("OrderedQuantity", row.get("BSMNG")))
        if requested is None or ordered is None:
            evidence_gaps.append("pr_quantity_evidence")
            continue
        remaining = max(requested - ordered, Decimal(0))
        if remaining <= 0:
            continue
        release_status = _text(row, "PurReqnReleaseStatus", "ReleaseStatus", "FRGKZ").upper()
        release_not_complete = _truthy(row.get("ReleaseIsNotCompleted"))
        source_assigned = _truthy(row.get("SourceOfSupplyIsAssigned"))
        supplier = _text(row, "FixedSupplier", "Supplier", "LIFNR")
        release_state_conflict = release_status == "05" and release_not_complete
        if release_state_conflict:
            action_id = "manual_review"
        elif release_status == "01":
            action_id = "complete_version"
        elif release_status == "02":
            action_id = "process_active" if source_assigned or supplier else "assign_source"
        elif release_status in {"03", "04"}:
            action_id = "complete_release"
        elif release_status == "05":
            action_id = (
                "ready_to_convert"
                if source_assigned or supplier
                else "assign_source"
            )
        elif release_status == "08":
            action_id = "handle_rejection"
        else:
            action_id = "manual_review"
        delivery_date = _date(row.get("DeliveryDate"))
        overdue_days = max((as_of - delivery_date).days, 0) if delivery_date else None
        price = _strict_decimal(row.get("PurchaseRequisitionPrice"))
        price_quantity = _strict_decimal(row.get("PurReqnPriceQuantity"))
        currency = _text(row, "PurReqnItemCurrency")
        price_summary = ""
        if price is not None and price_quantity is not None and price_quantity > 0:
            price_summary = f"{price} {currency} / {price_quantity}".strip()
        source_status = (
            {
                "zh": f"已分配：{supplier}" if supplier else "已分配",
                "en": f"Assigned: {supplier}" if supplier else "Assigned",
            }
            if source_assigned or supplier
            else {"zh": "未分配", "en": "Not assigned"}
        )
        pr_actions.append(
            {
                "action": action_labels[action_id],
                "purchase_requisition": _text(row, "PurchaseRequisition", "BANFN"),
                "purchase_requisition_item": _text(row, "PurchaseRequisitionItem", "BNFPO"),
                "item_text": _text(row, "PurchaseRequisitionItemText", "TXZ01"),
                "requested_quantity": str(requested),
                "ordered_quantity": str(ordered),
                "remaining_quantity": str(remaining),
                "unit": _text(row, "BaseUnit", "MEINS"),
                "delivery_date": delivery_date.isoformat() if delivery_date else "",
                "overdue_days": overdue_days,
                "release_status": release_labels.get(
                    release_status,
                    {
                        "zh": f"未知状态（{release_status or '空'}）",
                        "en": f"Unknown status ({release_status or 'blank'})",
                    },
                ),
                "source_assignment": source_status,
                "supplier": supplier,
                "purchasing_organization": _text(row, "PurchasingOrganization", "EKORG"),
                "purchasing_group": _text(row, "PurchasingGroup", "EKGRP"),
                "requisitioner": _text(row, "RequisitionerName", "AFNAM", "CreatedByUser"),
                "creation_date": _date_text(row, "PurReqCreationDate", "BADAT"),
                "price": price_summary,
                "action_id": action_id,
                "_sort": (
                    action_rank[action_id],
                    delivery_date or date.max,
                    _text(row, "PurchaseRequisition", "BANFN"),
                    _text(row, "PurchaseRequisitionItem", "BNFPO"),
                ),
            }
        )
    pr_actions.sort(key=lambda item: item["_sort"])
    for item in pr_actions:
        item.pop("_sort", None)

    po_item_map: dict[tuple[str, str], JsonObject] = {}
    for row in po_items:
        key = (
            _text(row, "PurchaseOrder", "EBELN"),
            _text(row, "PurchaseOrderItem", "EBELP"),
        )
        if all(key):
            po_item_map[key] = row
    po_header_map = {
        _text(row, "PurchaseOrder", "EBELN"): row
        for row in po_headers
        if _text(row, "PurchaseOrder", "EBELN")
    }
    receipt_header_map = {
        (
            _text(row, "MaterialDocumentYear", "MJAHR"),
            _text(row, "MaterialDocument", "MBLNR"),
        ): row
        for row in receipt_headers
        if _text(row, "MaterialDocumentYear", "MJAHR")
        and _text(row, "MaterialDocument", "MBLNR")
    }
    receipt_totals: dict[tuple[str, str], Decimal] = {}
    receipt_units: dict[tuple[str, str], set[str]] = {}
    seen_receipts: set[tuple[str, str, str]] = set()
    for row in po_receipts:
        receipt_key = (
            _text(row, "MaterialDocumentYear", "MJAHR"),
            _text(row, "MaterialDocument", "MBLNR"),
            _text(row, "MaterialDocumentItem", "ZEILE"),
        )
        if not all(receipt_key) or receipt_key in seen_receipts:
            evidence_gaps.append("po_receipt_business_key_evidence")
            continue
        seen_receipts.add(receipt_key)
        header = receipt_header_map.get(receipt_key[:2])
        if header is None:
            evidence_gaps.append("po_receipt_header_evidence")
            continue
        posting_date = _date(header.get("PostingDate"))
        if posting_date is None:
            evidence_gaps.append("po_receipt_posting_date_evidence")
            continue
        if posting_date > as_of:
            continue
        po_key = (
            _text(row, "PurchaseOrder", "EBELN"),
            _text(row, "PurchaseOrderItem", "EBELP"),
        )
        quantity = _strict_decimal(row.get("QuantityInEntryUnit", row.get("MENGE")))
        debit_credit = _text(row, "DebitCreditCode", "SHKZG").upper()
        unit = _unit_key(_text(row, "EntryUnit", "ERFME", "MEINS"))
        if not all(po_key) or quantity is None or quantity < 0 or debit_credit not in {"S", "H"}:
            evidence_gaps.append("po_receipt_quantity_evidence")
            continue
        signed = quantity if debit_credit == "S" else -quantity
        receipt_totals[po_key] = receipt_totals.get(po_key, Decimal(0)) + signed
        if unit:
            receipt_units.setdefault(po_key, set()).add(unit)

    optional_supplier_rows = _optional_rows(inputs, "supplier_master")
    optional_supplier_org_rows = _optional_rows(inputs, "supplier_purchasing_org")
    supplier_master_map = {
        _text(row, "Supplier", "LIFNR"): row
        for row in optional_supplier_rows
        if _text(row, "Supplier", "LIFNR")
    }
    supplier_org_map = {
        (
            _text(row, "Supplier", "LIFNR"),
            _text(row, "PurchasingOrganization", "EKORG"),
        ): row
        for row in optional_supplier_org_rows
        if _text(row, "Supplier", "LIFNR")
        and _text(row, "PurchasingOrganization", "EKORG")
    }
    schedules_by_item: dict[tuple[str, str], list[JsonObject]] = {}
    for row in po_schedules:
        key = (
            _text(row, "PurchasingDocument", "PurchaseOrder", "EBELN"),
            _text(row, "PurchasingDocumentItem", "PurchaseOrderItem", "EBELP"),
        )
        if all(key):
            schedules_by_item.setdefault(key, []).append(row)
        else:
            evidence_gaps.append("po_schedule_business_key_evidence")

    po_actions: list[JsonObject] = []
    for po_key, schedule_rows in schedules_by_item.items():
        item = po_item_map.get(po_key)
        if item is None:
            evidence_gaps.append("po_item_evidence")
            continue
        if _text(item, "PurchasingDocumentDeletionCode", "LOEKZ"):
            continue
        item_unit = _unit_key(
            _text(item, "PurchaseOrderQuantityUnit", "OrderQuantityUnit", "MEINS")
        )
        if not item_unit:
            schedule_units = {
                _unit_key(
                    _text(row, "PurchaseOrderQuantityUnit", "OrderQuantityUnit", "MEINS")
                )
                for row in schedule_rows
                if _unit_key(
                    _text(row, "PurchaseOrderQuantityUnit", "OrderQuantityUnit", "MEINS")
                )
            }
            if len(schedule_units) == 1:
                item_unit = next(iter(schedule_units))
        if not item_unit:
            evidence_gaps.append("po_order_unit_evidence")
            continue
        units = receipt_units.get(po_key, set())
        if any(unit != item_unit for unit in units):
            evidence_gaps.append("po_receipt_unit_conflict")
            continue
        receipt_pool = receipt_totals.get(po_key, Decimal(0))
        if receipt_pool < 0:
            evidence_gaps.append("po_negative_net_receipt")
            continue
        header = po_header_map.get(po_key[0], {})
        supplier = _text(header, "Supplier", "LIFNR")
        purchasing_org = _text(header, "PurchasingOrganization", "EKORG")
        supplier_master = supplier_master_map.get(supplier, {})
        supplier_org = supplier_org_map.get((supplier, purchasing_org), {})
        supplier_name = _text(supplier_master, "SupplierFullName", "SupplierName", "NAME1")
        contact = _text(
            header,
            "SupplierRespSalesPersonName",
        ) or _text(supplier_org, "SupplierRespSalesPersonName")
        phone = _text(header, "SupplierPhoneNumber") or _text(
            supplier_org, "SupplierPhoneNumber"
        )
        sorted_schedules = sorted(
            schedule_rows,
            key=lambda row: (
                _date(
                    row.get("ScheduleLineDeliveryDate", row.get("DeliveryDate", row.get("EINDT")))
                )
                or date.max,
                _text(row, "ScheduleLine", "ETENR"),
            ),
        )
        for row in sorted_schedules:
            delivery_date = _date(
                row.get("ScheduleLineDeliveryDate", row.get("DeliveryDate", row.get("EINDT")))
            )
            scheduled_quantity = _strict_decimal(
                row.get("ScheduleLineOrderQuantity", row.get("MENGE"))
            )
            schedule_unit = _unit_key(
                _text(row, "PurchaseOrderQuantityUnit", "OrderQuantityUnit") or item_unit
            )
            if delivery_date is None or scheduled_quantity is None or scheduled_quantity < 0:
                evidence_gaps.append("po_schedule_quantity_or_date_evidence")
                continue
            if schedule_unit != item_unit:
                evidence_gaps.append("po_schedule_unit_conflict")
                continue
            adt_received = _strict_decimal(row.get("WEMNG"))
            if adt_received is not None:
                received_quantity = max(min(adt_received, scheduled_quantity), Decimal(0))
            else:
                received_quantity = max(min(receipt_pool, scheduled_quantity), Decimal(0))
                receipt_pool -= received_quantity
            open_quantity = max(scheduled_quantity - received_quantity, Decimal(0))
            if delivery_date >= as_of or open_quantity <= 0:
                continue
            committed_quantity = _strict_decimal(row.get("ScheduleLineCommittedQuantity"))
            overdue_days = (as_of - delivery_date).days
            po_actions.append(
                {
                    "action": {"zh": "联系供应商确认并催交", "en": "Confirm and expedite with supplier"},
                    "purchase_order": po_key[0],
                    "purchase_order_item": po_key[1],
                    "schedule_line": _text(row, "ScheduleLine", "ETENR"),
                    "supplier": supplier,
                    "supplier_name": supplier_name,
                    "supplier_contact": contact,
                    "supplier_phone": phone,
                    "material": _text(item, "Material", "MATNR"),
                    "item_text": _text(item, "PurchaseOrderItemText", "TXZ01"),
                    "supplier_material": _text(item, "SupplierMaterialNumber", "IDNLF"),
                    "plant": _text(item, "Plant", "WERKS"),
                    "delivery_date": delivery_date.isoformat(),
                    "overdue_days": overdue_days,
                    "scheduled_quantity": str(scheduled_quantity),
                    "received_quantity": str(received_quantity),
                    "open_quantity": str(open_quantity),
                    "committed_quantity": (
                        str(committed_quantity) if committed_quantity is not None else ""
                    ),
                    "unit": item_unit,
                    "purchase_requisition": _text(item, "PurchaseRequisition", "BANFN"),
                    "purchase_requisition_item": _text(
                        item, "PurchaseRequisitionItem", "BNFPO"
                    ),
                    "purchasing_group": _text(header, "PurchasingGroup", "EKGRP"),
                    "_sort": (
                        -overdue_days,
                        delivery_date,
                        po_key[0],
                        po_key[1],
                        _text(row, "ScheduleLine", "ETENR"),
                    ),
                }
            )
    po_actions.sort(key=lambda item: item["_sort"])
    for item in po_actions:
        item.pop("_sort", None)

    valid_sources = [
        row
        for row in sources
        if not _truthy(row.get("IsMarkedForDeletion"))
        and _truthy(row.get("IsRelevantForAutomSrcg"))
    ]
    source_actions = [
        {
            "action": {"zh": "复核并用于分配货源", "en": "Review for source assignment"},
            "purchasing_info_record": _text(row, "PurchasingInfoRecord", "INFNR"),
            "supplier": _text(row, "Supplier", "LIFNR"),
            "purchasing_organization": _text(row, "PurchasingOrganization", "EKORG"),
            "plant": _text(row, "Plant", "WERKS"),
            "purchasing_group": _text(row, "PurchasingGroup", "EKGRP"),
            "planned_delivery_days": (
                str(value)
                if (
                    value := _strict_decimal(
                        row.get("MaterialPlannedDeliveryDurn", row.get("PLIFZ"))
                    )
                )
                is not None
                else ""
            ),
            "order_unit": _text(row, "PurgDocOrderQuantityUnit", "BSTME"),
            "minimum_order_quantity": _text(row, "MinimumPurchaseOrderQuantity", "MINBM"),
            "standard_order_quantity": _text(row, "StandardPurchaseOrderQuantity", "NORBM"),
            "maximum_order_quantity": _text(row, "MaximumOrderQuantity", "MABM"),
            "automatic_sourcing": {"zh": "可用于自动寻源", "en": "Relevant for automatic sourcing"},
        }
        for row in valid_sources
    ]
    source_actions.sort(
        key=lambda item: (
            str(item.get("planned_delivery_days") or ""),
            str(item.get("supplier") or ""),
            str(item.get("purchasing_info_record") or ""),
        )
    )
    last_mrp_dates = [
        parsed
        for row in [*master, *mrp]
        for parsed in [_date(row.get("MaterialLastMRPDateTime"))]
        if parsed is not None
    ]
    last_mrp = max(last_mrp_dates) if last_mrp_dates else None
    snapshot_age_days = (as_of - last_mrp).days if last_mrp and as_of >= last_mrp else 0
    findings = [
        {"code": "UNIT_NOT_COMPARABLE", "severity": "high"}
        for _ in [0]
        if len(units) > 1
    ]
    if not master:
        findings.append({"code": "EXTERNAL_PROCUREMENT_NOT_PROVEN", "severity": "high"})
    elif not external_procurement:
        findings.append({"code": "MATERIAL_NOT_EXTERNALLY_PROCURED", "severity": "high"})
    if snapshot_age_days > 30:
        last_mrp_text = last_mrp.isoformat() if last_mrp else ""
        findings.append(
            {
                "code": "MRP_SNAPSHOT_STALE",
                "severity": "low",
                "age_days": snapshot_age_days,
                "last_mrp_date": last_mrp_text,
                "detail": {
                    "zh": (
                        f"MRP 快照较旧：最后 MRP 日期为 {last_mrp_text}，"
                        f"距查询基准日 {snapshot_age_days} 天；此提示不阻塞业务结论。"
                    ),
                    "en": (
                        f"The MRP snapshot is stale: the last MRP date is {last_mrp_text}, "
                        f"{snapshot_age_days} day(s) before the as-of date; this warning "
                        "does not block the business conclusion."
                    ),
                },
            }
        )
    if not complete:
        findings.append({"code": "REQUIRED_EVIDENCE_INCOMPLETE", "severity": "high"})
    evidence_gaps = sorted(set(evidence_gaps))
    pr_action_complete = pr_complete and "pr_quantity_evidence" not in evidence_gaps
    po_action_complete = po_complete and not any(
        gap.startswith("po_") for gap in evidence_gaps
    )
    pr_counts = {
        "release": sum(item.get("action_id") == "complete_release" for item in pr_actions),
        "convert": sum(item.get("action_id") == "ready_to_convert" for item in pr_actions),
        "source_or_processing": sum(
            item.get("action_id") in {"complete_version", "assign_source", "process_active"}
            for item in pr_actions
        ),
        "rejected_or_review": sum(
            item.get("action_id") in {"handle_rejection", "manual_review"}
            for item in pr_actions
        ),
    }
    for item in pr_actions:
        item.pop("action_id", None)
    records: list[JsonObject] = []
    for index, row in enumerate(mrp):
        material = _text(row, "Material") or str(run_input.get("material") or "")
        plant = _text(row, "MRPPlant", "Plant", "MRPArea") or str(run_input.get("plant") or "")
        profile = _text(row, "MaterialShortageProfile")
        counter = _text(row, "MaterialShortageProfileCount")
        segment_type = _text(row, "MRPPlanningSegmentType")
        segment_number = _text(row, "MRPPlanningSegmentNumber") or "EMPTY"
        requirement_id = (
            "|".join(
                (
                    profile,
                    counter,
                    _text(row, "MRPArea") or plant,
                    "(blank)" if segment_number == "EMPTY" else segment_number,
                    segment_type,
                )
            )
            if profile and counter
            else "|".join(
                filter(
                    None,
                    (
                        _text(row, "MRPElement", "MRPElementType"),
                        _text(row, "MRPElementDocument", "PurchaseRequisition", "PurchaseOrder"),
                        _text(row, "MRPElementDocumentItem", "PurchaseRequisitionItem", "PurchaseOrderItem"),
                        _date_text(row, "MRPElementAvailyOrRqmtDate", "RequirementDate"),
                    ),
                )
            ) or f"row-{index + 1}"
        )
        if material and plant:
            records.append(
                {
                    "material": material,
                    "plant": plant,
                    "requirement_id": requirement_id,
                    "requirement_date": _date_text(row, "MaterialShortageStartDate", "MRPElementAvailyOrRqmtDate", "RequirementDate"),
                    "mrp_element_type": "material_coverage" if profile else _text(row, "MRPElement", "MRPElementType"),
                    "shortage_quantity": _text(row, "MaterialShortageQuantity", "MRPElementOpenQuantity"),
                    "unit": _text(row, "MaterialBaseUnit", "BaseUnit", "Unit"),
                }
            )
    if not records:
        for row in pr_actions:
            material = str(run_input.get("material") or "")
            plant = str(run_input.get("plant") or "")
            requisition = str(row.get("purchase_requisition") or "")
            item = str(row.get("purchase_requisition_item") or "")
            if material and plant and requisition and item:
                records.append(
                    {
                        "material": material,
                        "plant": plant,
                        "requirement_id": f"PR:{requisition}/{item}",
                        "requirement_date": str(row.get("delivery_date") or ""),
                        "mrp_element_type": "purchase_requisition",
                        "shortage_quantity": "",
                        "unit": str(row.get("unit") or ""),
                    }
                )
    expedite_count: int | None = len(po_actions) if po_action_complete else None
    pr_breakdown_zh = (
        f"待审批 {pr_counts['release']}、可转 PO {pr_counts['convert']}、"
        f"待完善或分配货源 {pr_counts['source_or_processing']}、拒绝或复核 {pr_counts['rejected_or_review']}"
    )
    pr_breakdown_en = (
        f"{pr_counts['release']} awaiting release, {pr_counts['convert']} ready for PO conversion, "
        f"{pr_counts['source_or_processing']} needing completion or source assignment, and "
        f"{pr_counts['rejected_or_review']} rejected or requiring review"
    )
    po_summary_zh = (
        f"{expedite_count} 条 PO 计划行需要催交"
        if expedite_count is not None
        else "PO 催交数量无法确定"
    )
    po_summary_en = (
        f"{expedite_count} PO schedule line(s) require expediting"
        if expedite_count is not None
        else "the PO expediting count is inconclusive"
    )
    actions_zh: list[str] = []
    actions_en: list[str] = []
    if pr_actions:
        actions_zh.append("按“采购申请待办”逐项完成审批、货源分配或 PO 转换。")
        actions_en.append("Work through the purchase-requisition action table for release, sourcing, or PO conversion.")
    if po_actions:
        actions_zh.append("优先联系逾期天数最长的供应商，确认未交数量和新的承诺交期。")
        actions_en.append("Contact the most overdue suppliers first to confirm open quantity and a revised committed date.")
    if not source_actions and source_topic_complete:
        actions_zh.append("当前未找到可自动寻源的信息记录，需要采购员补充或复核货源。")
        actions_en.append("No auto-sourcing-relevant info record was found; purchasing must add or review sources.")
    if not actions_zh:
        actions_zh.append("当前没有已确认的采购处置待办。")
        actions_en.append("No confirmed procurement action is currently required.")

    result = _result(
        inputs,
        business_status=(
            "capability_blocked"
            if not complete
            else "attention"
            if shortage or pr_actions or po_actions
            else "normal"
        ),
        headline_zh=(
            f"识别到 {len(pr_actions)} 条 PR 待办（{pr_breakdown_zh}），{po_summary_zh}；确定缺口为 {shortage}"
            if comparable else
            f"识别到 {len(pr_actions)} 条 PR 待办（{pr_breakdown_zh}），{po_summary_zh}；缺口数量无法确定"
        ),
        headline_en=(
            f"Found {len(pr_actions)} PR action(s) ({pr_breakdown_en}); {po_summary_en}; confirmed shortage is {shortage}"
            if comparable else
            f"Found {len(pr_actions)} PR action(s) ({pr_breakdown_en}); {po_summary_en}; shortage quantity is inconclusive"
        ),
        overview_zh="PR 建议按审批与货源状态分类；PO 催交数量按截止日净收货重建，不使用 SAP 承诺数量替代实际收货。本 Agent 只给出处置建议。",
        overview_en="PR actions are classified by release and sourcing status. PO expediting uses net goods receipts at the cutoff and never substitutes SAP committed quantity for actual receipts. The Agent is advisory only.",
        stages=[
            _stage("mrp", "MRP 供需", "MRP supply and demand", len(mrp), state="confirmed" if _topic_complete(inputs, "mrp") else "unknown"),
            _stage("pr", "采购申请待办", "Purchase requisition actions", len(pr_actions), state="confirmed" if pr_action_complete else "unknown", detail_zh=f"{len(pr_actions)} 条待办：{pr_breakdown_zh}。", detail_en=f"{len(pr_actions)} action(s): {pr_breakdown_en}."),
            _stage("po", "采购订单催交", "PO expediting", len(po_actions), state="confirmed" if po_action_complete else "unknown", detail_zh=po_summary_zh + "。", detail_en=po_summary_en + "."),
            _stage("source", "有效货源", "Valid sources", len(valid_sources), state="confirmed" if _topic_complete(inputs, "source") else "unknown"),
        ],
        findings=findings,
        metrics=[
            {"id": "shortage_quantity", "label": {"zh": "短缺数量", "en": "Shortage quantity"}, "value": str(shortage) if comparable else None},
            {"id": "pr_action_total", "label": {"zh": "PR 待办总数", "en": "Total PR actions"}, "value": len(pr_actions)},
            {"id": "pr_awaiting_release", "label": {"zh": "待审批 PR", "en": "PRs awaiting release"}, "value": pr_counts["release"]},
            {"id": "pr_ready_to_convert", "label": {"zh": "可转 PO 的 PR", "en": "PRs ready for PO conversion"}, "value": pr_counts["convert"]},
            {"id": "pr_source_or_processing_required", "label": {"zh": "待完善或分配货源 PR", "en": "PRs needing completion or sourcing"}, "value": pr_counts["source_or_processing"]},
            {"id": "po_schedule_lines_to_expedite", "label": {"zh": "确认需要催交的 PO 计划行", "en": "Confirmed PO schedule lines to expedite"}, "value": expedite_count},
            {"id": "pending_pr", "label": {"zh": "PR 待办总数（兼容）", "en": "Pending PRs (compatibility)"}, "value": len(pr_actions), "deprecated": True},
            {"id": "expedite_po", "label": {"zh": "需催交 PO 计划行", "en": "PO schedule lines to expedite"}, "value": expedite_count, "deprecated": True},
            {"id": "valid_source_candidates", "label": {"zh": "有效货源候选", "en": "Valid source candidates"}, "value": len(valid_sources)},
        ],
        records=records,
        gaps=_gaps(inputs, *missing, *evidence_gaps),
        actions_zh=actions_zh,
        actions_en=actions_en,
        source_complete_override=topic_complete,
    )
    result["business_report"]["action_tables"] = [
        {
            "id": "pr_actions",
            "title": {"zh": "采购申请待办", "en": "Purchase requisition actions"},
            "columns": [
                {"key": "action", "label": {"zh": "建议动作", "en": "Recommended action"}, "format": "status"},
                {"key": "purchase_requisition", "label": {"zh": "采购申请", "en": "Purchase requisition"}},
                {"key": "purchase_requisition_item", "label": {"zh": "项目", "en": "Item"}},
                {"key": "item_text", "label": {"zh": "项目描述", "en": "Item description"}},
                {"key": "requested_quantity", "label": {"zh": "申请数量", "en": "Requested quantity"}, "format": "decimal"},
                {"key": "ordered_quantity", "label": {"zh": "已订购数量", "en": "Ordered quantity"}, "format": "decimal"},
                {"key": "remaining_quantity", "label": {"zh": "待处理数量", "en": "Remaining quantity"}, "format": "decimal"},
                {"key": "unit", "label": {"zh": "单位", "en": "Unit"}},
                {"key": "delivery_date", "label": {"zh": "需求日期", "en": "Delivery date"}, "format": "date"},
                {"key": "overdue_days", "label": {"zh": "逾期天数", "en": "Days overdue"}, "format": "integer"},
                {"key": "release_status", "label": {"zh": "审批状态", "en": "Release status"}, "format": "status"},
                {"key": "source_assignment", "label": {"zh": "货源分配", "en": "Source assignment"}, "format": "status"},
                {"key": "supplier", "label": {"zh": "供应商", "en": "Supplier"}},
                {"key": "purchasing_organization", "label": {"zh": "采购组织", "en": "Purchasing organization"}},
                {"key": "purchasing_group", "label": {"zh": "采购组", "en": "Purchasing group"}},
                {"key": "requisitioner", "label": {"zh": "申请人", "en": "Requisitioner"}},
                {"key": "creation_date", "label": {"zh": "创建日期", "en": "Creation date"}, "format": "date"},
                {"key": "price", "label": {"zh": "参考价格", "en": "Reference price"}},
            ],
            "rows": pr_actions,
            "total_rows": len(pr_actions),
            "source_complete": pr_action_complete,
            "empty_state": (
                {"zh": "完整查询未发现需要继续处理的采购申请。", "en": "The complete query found no purchase requisition requiring action."}
                if pr_action_complete
                else {"zh": "采购申请证据不完整，无法确认待办清单。", "en": "Purchase requisition evidence is incomplete, so the action list cannot be confirmed."}
            ),
            "artifact_name": "pr-actions.csv",
        },
        {
            "id": "po_expedite_actions",
            "title": {"zh": "采购订单催交待办", "en": "Purchase order expediting actions"},
            "columns": [
                {"key": "action", "label": {"zh": "建议动作", "en": "Recommended action"}, "format": "status"},
                {"key": "purchase_order", "label": {"zh": "采购订单", "en": "Purchase order"}},
                {"key": "purchase_order_item", "label": {"zh": "项目", "en": "Item"}},
                {"key": "schedule_line", "label": {"zh": "计划行", "en": "Schedule line"}},
                {"key": "supplier", "label": {"zh": "供应商", "en": "Supplier"}},
                {"key": "supplier_name", "label": {"zh": "供应商名称", "en": "Supplier name"}},
                {"key": "supplier_contact", "label": {"zh": "联系人", "en": "Contact"}},
                {"key": "supplier_phone", "label": {"zh": "联系电话", "en": "Phone"}},
                {"key": "material", "label": {"zh": "物料", "en": "Material"}},
                {"key": "item_text", "label": {"zh": "项目描述", "en": "Item description"}},
                {"key": "supplier_material", "label": {"zh": "供应商物料号", "en": "Supplier material"}},
                {"key": "plant", "label": {"zh": "工厂", "en": "Plant"}},
                {"key": "delivery_date", "label": {"zh": "计划交货日期", "en": "Scheduled delivery date"}, "format": "date"},
                {"key": "overdue_days", "label": {"zh": "逾期天数", "en": "Days overdue"}, "format": "integer"},
                {"key": "scheduled_quantity", "label": {"zh": "计划数量", "en": "Scheduled quantity"}, "format": "decimal"},
                {"key": "received_quantity", "label": {"zh": "截止日净收货", "en": "Net receipts at cutoff"}, "format": "decimal"},
                {"key": "open_quantity", "label": {"zh": "未交数量", "en": "Open quantity"}, "format": "decimal"},
                {"key": "committed_quantity", "label": {"zh": "SAP 承诺数量", "en": "SAP committed quantity"}, "format": "decimal"},
                {"key": "unit", "label": {"zh": "单位", "en": "Unit"}},
                {"key": "purchase_requisition", "label": {"zh": "关联 PR", "en": "Related PR"}},
                {"key": "purchase_requisition_item", "label": {"zh": "PR 项目", "en": "PR item"}},
                {"key": "purchasing_group", "label": {"zh": "采购组", "en": "Purchasing group"}},
            ],
            "rows": po_actions,
            "total_rows": len(po_actions),
            "source_complete": po_action_complete,
            "empty_state": (
                {"zh": "完整查询未发现截止日仍有未交数量的逾期计划行。", "en": "The complete query found no overdue schedule line with open quantity at the cutoff."}
                if po_action_complete
                else {"zh": "PO 或收货证据不完整，无法确认催交清单。", "en": "PO or receipt evidence is incomplete, so the expediting list cannot be confirmed."}
            ),
            "artifact_name": "po-expedite-actions.csv",
        },
        {
            "id": "source_candidates",
            "title": {"zh": "有效货源候选", "en": "Valid source candidates"},
            "columns": [
                {"key": "action", "label": {"zh": "建议动作", "en": "Recommended action"}, "format": "status"},
                {"key": "purchasing_info_record", "label": {"zh": "采购信息记录", "en": "Purchasing info record"}},
                {"key": "supplier", "label": {"zh": "供应商", "en": "Supplier"}},
                {"key": "purchasing_organization", "label": {"zh": "采购组织", "en": "Purchasing organization"}},
                {"key": "plant", "label": {"zh": "工厂", "en": "Plant"}},
                {"key": "purchasing_group", "label": {"zh": "采购组", "en": "Purchasing group"}},
                {"key": "planned_delivery_days", "label": {"zh": "计划交货天数", "en": "Planned delivery days"}, "format": "decimal"},
                {"key": "order_unit", "label": {"zh": "订单单位", "en": "Order unit"}},
                {"key": "minimum_order_quantity", "label": {"zh": "最小订单量", "en": "Minimum order quantity"}, "format": "decimal"},
                {"key": "standard_order_quantity", "label": {"zh": "标准订单量", "en": "Standard order quantity"}, "format": "decimal"},
                {"key": "maximum_order_quantity", "label": {"zh": "最大订单量", "en": "Maximum order quantity"}, "format": "decimal"},
                {"key": "automatic_sourcing", "label": {"zh": "自动寻源", "en": "Automatic sourcing"}, "format": "status"},
            ],
            "rows": source_actions,
            "total_rows": len(source_actions),
            "source_complete": source_topic_complete,
            "empty_state": (
                {"zh": "完整查询未发现符合自动寻源条件的信息记录。", "en": "The complete query found no info record relevant for automatic sourcing."}
                if source_topic_complete
                else {"zh": "货源证据不完整，无法确认有效货源候选。", "en": "Source evidence is incomplete, so valid source candidates cannot be confirmed."}
            ),
            "artifact_name": "source-candidates.csv",
        },
    ]
    return result


def _inventory_health_balancing(inputs: JsonObject) -> JsonObject:
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    window = inputs.get("window") if isinstance(inputs.get("window"), dict) else {}
    snapshot = _date(window.get("snapshot_date")) or date.today()
    check_slow = window.get("check_slow_moving") is True
    check_obsolete = window.get("check_obsolete") is True
    check_expiry = window.get("check_expiry") is True
    movement_requested = check_slow or check_obsolete
    selected_checks = [str(item) for item in window.get("selected_checks") or []]

    assessment = inputs.get("assessment") if isinstance(inputs.get("assessment"), dict) else {}
    api_complete = assessment.get("api_complete") if isinstance(assessment.get("api_complete"), dict) else {}
    use_stock_fallback = api_complete.get("stock") is False
    use_movement_fallback = api_complete.get("movement") is False
    initial_stock_rows = _rows(inputs, "stock_initial", "stock", "material_stock")
    confirmation_stock_rows = _rows(inputs, "stock_confirmation")
    if use_stock_fallback:
        initial_stock_rows = _adt_rows(inputs, "stock")
    movement_payload_rows = (
        _adt_rows(inputs, "movement")
        if use_movement_fallback
        else _rows(inputs, "movement", "movement_history", "material_movements")
    )
    api_batches = _rows(inputs, "batch_expiry", "batch", "batches")
    adt_batches = _adt_rows(inputs, "batch_expiry")
    batch_assessment = (
        inputs.get("batch_assessment")
        if isinstance(inputs.get("batch_assessment"), dict)
        else {}
    )
    batch_needs_adt = (
        isinstance(batch_assessment.get("needs_adt"), dict)
        and batch_assessment["needs_adt"].get("batch_expiry") is True
    )

    def decimal_or_none(value: Any) -> Decimal | None:
        if value in {None, ""}:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None

    def normalize_stock(rows: list[JsonObject]) -> tuple[dict[str, Decimal], set[str], bool]:
        quantities: dict[str, Decimal] = {}
        units: set[str] = set()
        invalid = False
        for row in rows:
            is_adt = any(field in row for field in ("MATNR", "WERKS", "LGORT", "LABST"))
            if not is_adt:
                if str(row.get("InventoryStockType") or "").strip() != "01":
                    continue
                if str(row.get("InventorySpecialStockType") or "").strip():
                    continue
            quantity_value = next(
                (
                    row.get(field)
                    for field in ("MatlWrhsStkQtyInMatlBaseUnit", "UnrestrictedUseStock", "LABST")
                    if row.get(field) not in {None, ""}
                ),
                None,
            )
            quantity = decimal_or_none(quantity_value)
            unit_value = _text(row, "MaterialBaseUnit", "BaseUnit", "MEINS")
            if quantity is None or not unit_value:
                invalid = True
                continue
            if quantity < 0:
                invalid = True
                continue
            batch = _text(row, "Batch", "CHARG")
            quantities[batch] = quantities.get(batch, Decimal(0)) + quantity
            units.add(unit_value)
        return quantities, units, invalid

    initial_stock, initial_units, invalid_initial_stock = normalize_stock(initial_stock_rows)
    confirmation_stock, confirmation_units, invalid_confirmation_stock = normalize_stock(
        confirmation_stock_rows
    )
    if not movement_requested:
        confirmation_stock = dict(initial_stock)
        confirmation_units = set(initial_units)

    stock_complete = _topic_complete(inputs, "stock")
    stock_confirmation_complete = (
        not movement_requested
        or (
            not use_stock_fallback
            and _topic_complete(inputs, "stock_confirmation")
        )
    )
    movement_complete = (not movement_requested) or _topic_complete(inputs, "movement")
    api_expiry_complete = (not check_expiry) or _topic_complete(inputs, "batch_expiry")
    # A complete Batch API read remains query-source complete even when some
    # returned master-data rows have no usable expiry date.  ADT is an
    # evidence supplement in that case, not a replacement source whose
    # metadata limitation should retroactively make the completed API query
    # incomplete.  ADT completeness replaces API completeness only when the
    # API query itself was incomplete.
    expiry_complete = (
        True
        if not check_expiry
        else _adt_complete(_fallback(inputs, "batch_expiry"))
        if batch_needs_adt and not api_expiry_complete
        else api_expiry_complete
    )
    source_complete = (
        stock_complete
        and stock_confirmation_complete
        and movement_complete
        and expiry_complete
    )
    units = confirmation_units or initial_units
    unit_complete = len(units) <= 1 and not invalid_initial_stock and not invalid_confirmation_stock
    unrestricted = sum(confirmation_stock.values(), Decimal(0)) if unit_complete else None
    unit = next(iter(units), "") if len(units) == 1 else ""

    header_by_key: dict[tuple[str, str], JsonObject] = {}
    movement_items: list[JsonObject] = []
    for row in movement_payload_rows:
        year = _text(row, "MaterialDocumentYear", "MJAHR")
        document = _text(row, "MaterialDocument", "MBLNR")
        item = _text(row, "MaterialDocumentItem", "ZEILE")
        if year and document and not item and (
            row.get("PostingDate") not in {None, ""}
            or row.get("CreationDate") not in {None, ""}
        ):
            header_by_key[(year, document)] = row
        elif year and document and item:
            movement_items.append(row)

    time_pattern = re.compile(
        r"^PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?$"
    )

    def time_key(value: Any) -> tuple[int, int, Decimal]:
        text = str(value or "").strip()
        match = time_pattern.fullmatch(text)
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

    events: list[JsonObject] = []
    movement_keys: set[tuple[str, str, str]] = set()
    duplicate_movement_key = False
    invalid_movement = False
    for row in movement_items:
        year = _text(row, "MaterialDocumentYear", "MJAHR")
        document = _text(row, "MaterialDocument", "MBLNR")
        item = _text(row, "MaterialDocumentItem", "ZEILE")
        stable_key = (year, document, item)
        if stable_key in movement_keys:
            duplicate_movement_key = True
            continue
        movement_keys.add(stable_key)
        header = header_by_key.get((year, document), {})
        posting_date = _date(row.get("PostingDate") or row.get("BUDAT") or header.get("PostingDate"))
        creation_date = _date(row.get("CreationDate") or row.get("CPUDT") or header.get("CreationDate")) or posting_date
        creation_time = row.get("CreationTime") or row.get("CPUTM") or header.get("CreationTime")
        quantity = decimal_or_none(row.get("QuantityInBaseUnit") or row.get("MENGE"))
        debit_credit = _text(row, "DebitCreditCode", "SHKZG").upper()
        movement_unit = _text(row, "MaterialBaseUnit", "BaseUnit", "MEINS")
        stock_type = _text(row, "InventoryStockType", "INSMK")
        special_stock = _text(row, "InventorySpecialStockType", "SOBKZ")
        if "InventoryStockType" in row and stock_type != "01":
            continue
        if "INSMK" in row and stock_type:
            continue
        if special_stock:
            continue
        if (
            posting_date is None
            or posting_date > snapshot
            or quantity is None
            or quantity < 0
            or debit_credit not in {"S", "H"}
            or not movement_unit
        ):
            invalid_movement = True
            continue
        events.append(
            {
                "stable_key": stable_key,
                "posting_date": posting_date,
                "creation_date": creation_date or posting_date,
                "creation_time": time_key(creation_time),
                "direction": debit_credit,
                "quantity": quantity,
                "unit": movement_unit,
                "batch": _text(row, "Batch", "CHARG"),
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
    movement_units = {str(event["unit"]) for event in events}
    layers_by_batch: dict[str, list[JsonObject]] = {}
    movement_underflow = False
    for event in events:
        batch = str(event["batch"])
        layers = layers_by_batch.setdefault(batch, [])
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
            movement_underflow = True

    remaining_by_batch = {
        batch: sum((Decimal(layer["quantity"]) for layer in layers), Decimal(0))
        for batch, layers in layers_by_batch.items()
    }
    remaining_by_batch = {
        batch: quantity for batch, quantity in remaining_by_batch.items() if quantity != 0
    }
    current_by_batch = {
        batch: quantity for batch, quantity in confirmation_stock.items() if quantity != 0
    }
    stock_changed = movement_requested and (
        initial_stock != confirmation_stock or initial_units != confirmation_units
    )
    history_reconciled = remaining_by_batch == current_by_batch
    movement_unit_complete = not movement_requested or (
        len(movement_units) <= 1 and (not movement_units or movement_units == units)
    )

    aging_gaps: list[str] = []
    if movement_requested:
        if not stock_confirmation_complete:
            aging_gaps.append("stock_confirmation_evidence")
        if not movement_complete:
            aging_gaps.append("movement_evidence")
        if duplicate_movement_key:
            aging_gaps.append("movement_stable_key_evidence")
        if invalid_movement:
            aging_gaps.append("movement_quantity_or_direction_evidence")
        if not movement_unit_complete:
            aging_gaps.append("movement_unit_evidence")
        if movement_underflow or not history_reconciled or stock_changed:
            aging_gaps.append("aging_reconciliation_gap")
    aging_complete = movement_requested and not aging_gaps and stock_complete and unit_complete

    layer_records: list[JsonObject] = []
    for batch, layers in sorted(layers_by_batch.items()):
        for layer in layers:
            quantity = Decimal(layer["quantity"])
            if quantity <= 0:
                continue
            receipt_date = layer["receipt_date"]
            layer_records.append(
                {
                    "batch": batch or None,
                    "receipt_date": receipt_date.isoformat(),
                    "age_days": (snapshot - receipt_date).days,
                    "remaining_quantity": str(quantity),
                    "unit": str(layer["unit"]),
                }
            )

    slow_days = run_input.get("slow_moving_days") if check_slow else None
    obsolete_days = run_input.get("obsolete_days") if check_obsolete else None

    def bucket_id(age_days: int) -> str:
        if isinstance(obsolete_days, int) and age_days >= obsolete_days:
            return "obsolete"
        if isinstance(slow_days, int) and age_days >= slow_days:
            return "slow_moving_only" if isinstance(obsolete_days, int) else "slow_moving"
        if isinstance(slow_days, int):
            return "below_slow_moving"
        return "below_obsolete"

    bucket_specs: list[tuple[str, int | None, int | None]] = []
    if isinstance(slow_days, int) and isinstance(obsolete_days, int):
        bucket_specs = [
            ("below_slow_moving", 0, slow_days - 1),
            ("slow_moving_only", slow_days, obsolete_days - 1),
            ("obsolete", obsolete_days, None),
        ]
    elif isinstance(slow_days, int):
        bucket_specs = [
            ("below_slow_moving", 0, slow_days - 1),
            ("slow_moving", slow_days, None),
        ]
    elif isinstance(obsolete_days, int):
        bucket_specs = [
            ("below_obsolete", 0, obsolete_days - 1),
            ("obsolete", obsolete_days, None),
        ]

    bucket_labels = {
        "below_slow_moving": {"zh": "未达到慢动阈值", "en": "Below slow-moving threshold"},
        "slow_moving_only": {"zh": "慢动但未呆滞", "en": "Slow-moving but not obsolete"},
        "slow_moving": {"zh": "慢动库存", "en": "Slow-moving stock"},
        "below_obsolete": {"zh": "未达到呆滞阈值", "en": "Below obsolete threshold"},
        "obsolete": {"zh": "呆滞库存", "en": "Obsolete stock"},
        "unclassified": {"zh": "未分类库存", "en": "Unclassified stock"},
    }
    bucket_quantities = {name: Decimal(0) for name, _minimum, _maximum in bucket_specs}
    if aging_complete:
        for layer in layer_records:
            name = bucket_id(int(layer["age_days"]))
            bucket_quantities[name] = bucket_quantities.get(name, Decimal(0)) + Decimal(
                str(layer["remaining_quantity"])
            )
            layer["bucket_id"] = name
            layer["bucket_label"] = bucket_labels[name]
    unclassified_quantity = Decimal(0) if aging_complete else (unrestricted or Decimal(0))
    aging_buckets = [
        {
            "bucket_id": name,
            "bucket_label": bucket_labels[name],
            "minimum_age_days": minimum,
            "maximum_age_days": maximum,
            "quantity": str(bucket_quantities.get(name, Decimal(0))) if aging_complete else None,
            "unit": unit or None,
        }
        for name, minimum, maximum in bucket_specs
    ]
    if movement_requested and not aging_complete:
        aging_buckets.append(
            {
                "bucket_id": "unclassified",
                "bucket_label": bucket_labels["unclassified"],
                "minimum_age_days": None,
                "maximum_age_days": None,
                "quantity": str(unclassified_quantity) if unrestricted is not None else None,
                "unit": unit or None,
            }
        )
    below_threshold_quantity = (
        bucket_quantities.get("below_slow_moving", bucket_quantities.get("below_obsolete"))
        if aging_complete
        else None
    )
    slow_moving_only_quantity = (
        bucket_quantities.get("slow_moving_only", bucket_quantities.get("slow_moving"))
        if aging_complete and check_slow
        else None
    )
    obsolete_bucket_quantity = (
        bucket_quantities.get("obsolete") if aging_complete and check_obsolete else None
    )

    movement_dates = [event["posting_date"] for event in events]
    last_movement = max(movement_dates) if movement_dates else None
    days_since_last_movement = (
        (snapshot - last_movement).days if last_movement is not None else None
    )
    oldest_layer_date = (
        min(_date(layer["receipt_date"]) for layer in layer_records if _date(layer["receipt_date"]))
        if layer_records and aging_complete
        else None
    )
    oldest_layer_age = (
        (snapshot - oldest_layer_date).days if oldest_layer_date is not None else None
    )

    slow_quantity = (
        sum(
            (Decimal(layer["remaining_quantity"]) for layer in layer_records if int(layer["age_days"]) >= int(slow_days)),
            Decimal(0),
        )
        if aging_complete and isinstance(slow_days, int)
        else None
    )
    obsolete_quantity = (
        sum(
            (Decimal(layer["remaining_quantity"]) for layer in layer_records if int(layer["age_days"]) >= int(obsolete_days)),
            Decimal(0),
        )
        if aging_complete and isinstance(obsolete_days, int)
        else None
    )

    def movement_status(enabled: bool, quantity: Decimal | None) -> str:
        if not enabled:
            return "not_requested"
        if not aging_complete or quantity is None:
            return "unknown"
        return "candidate" if quantity > 0 else "not_candidate"

    slow_status = movement_status(check_slow, slow_quantity)
    obsolete_status = movement_status(check_obsolete, obsolete_quantity)

    effective_stock_rows = confirmation_stock_rows or initial_stock_rows
    positive_batch_keys: set[tuple[str, str, str]] = set()
    for row in effective_stock_rows:
        if _text(row, "InventoryStockType", "INSME") not in {None, "", "01"}:
            continue
        if _text(row, "InventorySpecialStockType", "SOBKZ") not in {None, ""}:
            continue
        quantity = decimal_or_none(
            row.get("MatlWrhsStkQtyInMatlBaseUnit") or row.get("LABST")
        )
        batch = _text(row, "Batch", "CHARG")
        if quantity is None or quantity <= 0 or not batch:
            continue
        positive_batch_keys.add(
            (
                _text(row, "Material", "MATNR") or str(run_input.get("material") or ""),
                _text(row, "Plant", "WERKS") or str(run_input.get("plant") or ""),
                batch,
            )
        )

    def usable_batch_rows(
        rows: list[JsonObject], key: tuple[str, str, str], *, allow_material_level: bool
    ) -> list[JsonObject]:
        material, plant, batch = key
        candidates = [
            row
            for row in rows
            if _text(row, "Material", "MATNR") == material
            and _text(row, "Batch", "CHARG") == batch
        ]
        exact = [
            row
            for row in candidates
            if _text(row, "BatchIdentifyingPlant", "Plant", "WERKS") == plant
        ]
        if exact or not allow_material_level:
            return exact
        return [
            row
            for row in candidates
            if not _text(row, "BatchIdentifyingPlant", "Plant", "WERKS")
        ]

    expiry_details: list[JsonObject] = []
    expiry_association_complete = True
    expiry_date_complete = True
    expiry_conflict_free = True
    expiry_threshold = int(run_input.get("expiry_days") or 0)
    for key in sorted(positive_batch_keys) if check_expiry else []:
        api_usable = usable_batch_rows(api_batches, key, allow_material_level=True)
        adt_usable = usable_batch_rows(adt_batches, key, allow_material_level=False)
        api_dates = {
            expiry
            for row in api_usable
            if (expiry := _date(row.get("ShelfLifeExpirationDate") or row.get("VFDAT")))
            is not None
        }
        adt_dates = {
            expiry
            for row in adt_usable
            if (expiry := _date(row.get("VFDAT") or row.get("ShelfLifeExpirationDate")))
            is not None
        }
        all_dates = api_dates | adt_dates
        matched = bool(api_usable or adt_usable)
        if not matched:
            status = "unmatched"
            expiry = None
            source = "none"
            expiry_association_complete = False
        elif len(all_dates) > 1:
            status = "conflict"
            expiry = None
            source = "none"
            expiry_conflict_free = False
        elif api_dates:
            expiry = next(iter(api_dates))
            source = "api_batch"
            days_to_expiry = (expiry - snapshot).days
            status = (
                "expired"
                if days_to_expiry < 0
                else "expiring"
                if days_to_expiry <= expiry_threshold
                else "not_due"
            )
        elif adt_dates:
            expiry = next(iter(adt_dates))
            source = "adt_mcha"
            days_to_expiry = (expiry - snapshot).days
            status = (
                "expired"
                if days_to_expiry < 0
                else "expiring"
                if days_to_expiry <= expiry_threshold
                else "not_due"
            )
        else:
            status = "missing_date"
            expiry = None
            source = "none"
            expiry_date_complete = False
        expiry_details.append(
            {
                "batch": key[2],
                "current_quantity": str(current_by_batch.get(key[2], Decimal(0))),
                "unit": unit or None,
                "expiration_date": expiry.isoformat() if expiry is not None else None,
                "days_to_expiry": (expiry - snapshot).days if expiry is not None else None,
                "status": status,
                "evidence_source": source,
            }
        )

    expired_batches = [item for item in expiry_details if item["status"] == "expired"]
    expiring_batches = [item for item in expiry_details if item["status"] == "expiring"]
    missing_expiry_batches = [
        item for item in expiry_details if item["status"] in {"missing_date", "unmatched"}
    ]
    conflicting_expiry_batches = [
        item for item in expiry_details if item["status"] == "conflict"
    ]
    expiry_evidence_complete = (
        (not check_expiry)
        or (
            expiry_complete
            and expiry_association_complete
            and expiry_date_complete
            and expiry_conflict_free
        )
    )
    expiry_risks = expired_batches + expiring_batches
    if not check_expiry:
        expiry_status = "not_requested"
    elif expiry_risks:
        expiry_status = "candidate"
    elif not expiry_evidence_complete:
        expiry_status = "unknown"
    else:
        expiry_status = "not_candidate"

    evidence_gaps: list[str] = []
    if not stock_complete:
        evidence_gaps.append("stock_evidence")
    if invalid_initial_stock or invalid_confirmation_stock:
        evidence_gaps.append("stock_quantity_evidence")
    if not unit_complete:
        evidence_gaps.append("stock_unit_evidence")
    evidence_gaps.extend(aging_gaps)
    if check_expiry and not expiry_complete:
        evidence_gaps.append("batch_expiry_evidence")
    if check_expiry and not expiry_association_complete:
        evidence_gaps.append("batch_expiry_association")
    if check_expiry and not expiry_date_complete:
        evidence_gaps.append("batch_expiry_date_missing")
    if check_expiry and not expiry_conflict_free:
        evidence_gaps.append("batch_expiry_conflict")
    evidence_gaps = sorted(set(evidence_gaps))
    evidence_complete = source_complete and not evidence_gaps
    has_stock = bool(current_by_batch)
    has_risk = any(status == "candidate" for status in (slow_status, obsolete_status, expiry_status))
    if not evidence_complete:
        business_status = "inconclusive"
    elif not has_stock:
        business_status = "no_stock"
    elif not selected_checks:
        business_status = "snapshot_only"
    elif has_risk:
        business_status = "attention"
    else:
        business_status = "normal"

    headlines = {
        "inconclusive": ("已返回部分库存证据，但无法完成所选检查", "Some inventory evidence was returned, but the selected checks are inconclusive"),
        "no_stock": ("当前库存中未找到符合条件的非限制使用库存", "No matching unrestricted-use stock was found in the current snapshot"),
        "snapshot_only": ("已返回当前库存快照；本次未执行健康检查", "Current stock snapshot returned; no health check was requested"),
        "attention": ("当前库存存在需要关注的健康风险", "The current stock has health risks that need attention"),
        "normal": ("所选库存健康检查未发现风险", "No risk was found by the selected inventory-health checks"),
    }
    headline_zh, headline_en = headlines[business_status]
    if business_status == "inconclusive" and expiry_risks:
        unresolved_count = len(missing_expiry_batches) + len(conflicting_expiry_batches)
        headline_zh = (
            f"已确认发现 {len(expiry_risks)} 个批次效期风险"
            + (f"；另有 {unresolved_count} 个批次无法确认" if unresolved_count else "；其他检查证据仍不完整")
        )
        headline_en = (
            f"Confirmed {len(expiry_risks)} batch expiry risk(s)"
            + (f"; {unresolved_count} additional batch(es) remain unresolved" if unresolved_count else "; other check evidence remains incomplete")
        )
    findings: list[JsonObject] = []
    if slow_status == "candidate":
        findings.append({
            "code": "SLOW_MOVING_STOCK_CANDIDATE",
            "severity": "medium",
            "quantity": str(slow_quantity),
            "unit": unit,
            "detail": {
                "zh": f"识别到 {slow_quantity} {unit} 库存达到慢动阈值。",
                "en": f"{slow_quantity} {unit} of stock meets the slow-moving threshold.",
            },
        })
    if obsolete_status == "candidate":
        findings.append({
            "code": "OBSOLETE_STOCK_CANDIDATE",
            "severity": "high",
            "quantity": str(obsolete_quantity),
            "unit": unit,
            "detail": {
                "zh": f"识别到 {obsolete_quantity} {unit} 呆滞库存。",
                "en": f"{obsolete_quantity} {unit} of obsolete stock was identified.",
            },
        })
    findings.extend(
        {"code": "EXPIRED_BATCH_STOCK", "severity": "high", **item}
        for item in expired_batches
    )
    findings.extend(
        {"code": "EXPIRING_BATCH_STOCK", "severity": "medium", **item}
        for item in expiring_batches
    )

    def check_stage(stage_id: str, zh: str, en: str, status: str, detail_zh: str, detail_en: str, count: int = 0) -> JsonObject:
        state = "not_requested" if status == "not_requested" else "unknown" if status == "unknown" else "attention" if status == "candidate" else "confirmed"
        return _stage(stage_id, zh, en, count, state=state, detail_zh=detail_zh, detail_en=detail_en)

    last_movement_text = last_movement.isoformat() if last_movement is not None else None
    selected_check_labels = {
        "slow_moving": {"zh": "慢动检查", "en": "Slow-moving check"},
        "obsolete": {"zh": "呆滞检查", "en": "Obsolete-stock check"},
        "expiry": {"zh": "临期检查", "en": "Expiry check"},
    }
    status_labels = {
        "candidate": {"zh": "风险候选", "en": "Risk candidate"},
        "not_candidate": {"zh": "未发现风险", "en": "No risk found"},
        "unknown": {"zh": "无法确认", "en": "Unknown"},
        "not_requested": {"zh": "未启用", "en": "Not enabled"},
        "matched": {"zh": "数量一致", "en": "Quantities match"},
        "short_receipt": {"zh": "入库数量不足", "en": "Receipt shortfall"},
        "over_receipt": {"zh": "入库数量超出", "en": "Receipt overage"},
        "partial": {"zh": "部分完成", "en": "Partially complete"},
        "not_applicable": {"zh": "不适用", "en": "Not applicable"},
        "documented": {"zh": "凭证已核对", "en": "Documents verified"},
        "reversal_present": {"zh": "存在冲销", "en": "Reversal present"},
        "supporting_only": {"zh": "仅有辅助凭证", "en": "Supporting evidence only"},
        "no_activity": {"zh": "未发现业务活动", "en": "No activity found"},
        "not_assessed": {"zh": "本Agent未评估", "en": "Not assessed by this Agent"},
    }
    records = [{
        "snapshot_date": snapshot.isoformat(),
        "material": str(run_input.get("material") or ""),
        "plant": str(run_input.get("plant") or ""),
        "storage_location": str(run_input.get("storage_location") or ""),
        "current_unrestricted_stock": str(unrestricted) if unrestricted is not None else None,
        "unit": unit or None,
        "selected_checks": selected_checks,
        "last_movement_date": last_movement_text,
        "stock_age_days": days_since_last_movement,
        "stock_age_lower_bound_days": None,
        "aging_method": "fifo_movement_layers" if movement_requested else "not_requested",
        "aging_complete": aging_complete if movement_requested else True,
        "last_movement_activity_date": last_movement_text,
        "days_since_last_movement_activity": days_since_last_movement,
        "oldest_remaining_layer_date": oldest_layer_date.isoformat() if oldest_layer_date else None,
        "oldest_remaining_layer_age_days": oldest_layer_age,
        "classified_stock_quantity": str(unrestricted) if aging_complete and unrestricted is not None else None,
        "unclassified_stock_quantity": str(unclassified_quantity) if movement_requested else None,
        "below_threshold_stock_quantity": str(below_threshold_quantity) if below_threshold_quantity is not None else None,
        "slow_moving_only_stock_quantity": str(slow_moving_only_quantity) if slow_moving_only_quantity is not None else None,
        "obsolete_stock_quantity": str(obsolete_bucket_quantity) if obsolete_bucket_quantity is not None else None,
        "slow_moving_status": slow_status,
        "obsolete_status": obsolete_status,
        "expiry_status": expiry_status,
        "expiry_candidate_count": len(expiring_batches),
        "expired_batch_count": len(expired_batches),
        "expiring_batch_count": len(expiring_batches),
        "missing_expiry_date_batch_count": len(missing_expiry_batches),
        "expiry_evidence_complete": expiry_evidence_complete,
        "batch_expiry_details": expiry_details,
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
    }]
    result = _result(
        inputs,
        business_status=business_status,
        headline_zh=headline_zh,
        headline_en=headline_en,
        overview_zh="只统计当前非限制使用、非特殊库存；留空的检查不会执行，也不会被视为证据缺失。",
        overview_en="Only current unrestricted-use, non-special stock is counted. Blank checks are not executed and are not treated as evidence gaps.",
        stages=[
            _stage("current_stock", "当前库存快照", "Current stock snapshot", len(confirmation_stock_rows or initial_stock_rows), state="confirmed" if stock_complete else "unknown", detail_zh=f"快照日期 {snapshot.isoformat()}；仅统计库存类型 01、非特殊库存。", detail_en=f"Snapshot date {snapshot.isoformat()}; only stock type 01 and non-special stock are counted."),
            _stage("selected_checks", "本次检查条件", "Selected checks", len(selected_checks), state="confirmed", detail_zh=("、".join(selected_check_labels[item]["zh"] for item in selected_checks) if selected_checks else "三项均未启用。"), detail_en=(", ".join(selected_check_labels[item]["en"] for item in selected_checks) if selected_checks else "No health checks were enabled.")),
            check_stage("slow_moving", "慢动检查", "Slow-moving check", slow_status, "未启用。" if not check_slow else f"阈值 {run_input.get('slow_moving_days')} 天；{status_labels[slow_status]['zh']}。", "Not enabled." if not check_slow else f"Threshold {run_input.get('slow_moving_days')} days; {status_labels[slow_status]['en']}."),
            check_stage("obsolete", "呆滞检查", "Obsolete-stock check", obsolete_status, "未启用。" if not check_obsolete else f"阈值 {run_input.get('obsolete_days')} 天；{status_labels[obsolete_status]['zh']}。", "Not enabled." if not check_obsolete else f"Threshold {run_input.get('obsolete_days')} days; {status_labels[obsolete_status]['en']}."),
            check_stage(
                "expiry",
                "批次效期检查",
                "Batch expiry check",
                expiry_status,
                "未启用。"
                if not check_expiry
                else (
                    f"已过期 {len(expired_batches)} 个；未来 {run_input.get('expiry_days')} 天内临期 "
                    f"{len(expiring_batches)} 个；无法确认 {len(missing_expiry_batches) + len(conflicting_expiry_batches)} 个。"
                ),
                "Not enabled."
                if not check_expiry
                else (
                    f"Expired: {len(expired_batches)}; expiring within the next {run_input.get('expiry_days')} days: "
                    f"{len(expiring_batches)}; unresolved: {len(missing_expiry_batches) + len(conflicting_expiry_batches)}."
                ),
                len(expiry_risks),
            ),
            _stage("completeness", "数据完整性", "Data completeness", 1 if evidence_complete else 0, state="confirmed" if evidence_complete else "unknown", detail_zh="所选检查证据完整。" if evidence_complete else "至少一项已启用检查存在查询、单位或批次关联缺口。", detail_en="Evidence for the selected checks is complete." if evidence_complete else "At least one enabled check has a query, unit, or batch-association gap."),
        ],
        findings=findings,
        metrics=[
            {"id": "current_unrestricted_stock", "value": str(unrestricted) if unrestricted is not None else None, "unit": unit or None},
            {"id": "days_since_last_movement_activity", "value": days_since_last_movement},
            {"id": "oldest_remaining_layer_age_days", "value": oldest_layer_age},
            {"id": "classified_stock_quantity", "value": str(unrestricted) if aging_complete and unrestricted is not None else None, "unit": unit or None},
            {"id": "unclassified_stock_quantity", "value": str(unclassified_quantity) if movement_requested else None, "unit": unit or None},
            {"id": "below_threshold_stock_quantity", "value": str(below_threshold_quantity) if below_threshold_quantity is not None else None, "unit": unit or None},
            {"id": "slow_moving_only_stock_quantity", "value": str(slow_moving_only_quantity) if slow_moving_only_quantity is not None else None, "unit": unit or None},
            {"id": "obsolete_stock_quantity", "value": str(obsolete_bucket_quantity) if obsolete_bucket_quantity is not None else None, "unit": unit or None},
            {"id": "expiry_candidate_count", "value": len(expiring_batches)},
            {"id": "expired_batch_count", "value": len(expired_batches)},
            {"id": "expiring_batch_count", "value": len(expiring_batches)},
            {"id": "missing_expiry_date_batch_count", "value": len(missing_expiry_batches)},
        ],
        records=records,
        limitations=evidence_gaps,
        actions_zh=["请库存计划员复核已识别的风险候选。"] if has_risk else [],
        actions_en=["Have inventory planning review the identified risk candidates."] if has_risk else [],
        source_complete_override=source_complete,
    )
    result["rule_id"] = "inventory_health_check_deterministic_v4"
    result["status"] = "complete" if evidence_complete else "inconclusive"
    result["business_complete"] = evidence_complete
    result["evidence_complete"] = evidence_complete
    result["missing_evidence"] = evidence_gaps
    result["business_report"]["missing_evidence"] = evidence_gaps
    evidence_tables: list[JsonObject] = []
    if movement_requested:
        evidence_tables.extend([
            {
                "id": "aging_buckets",
                "title": {"zh": "库存账龄分布", "en": "Inventory age distribution"},
                "columns": [
                    {"key": "bucket_label", "label": {"zh": "账龄分类", "en": "Age category"}},
                    {"key": "minimum_age_days", "label": {"zh": "最小账龄（天）", "en": "Minimum age (days)"}, "format": "integer"},
                    {"key": "maximum_age_days", "label": {"zh": "最大账龄（天）", "en": "Maximum age (days)"}, "format": "integer"},
                    {"key": "quantity", "label": {"zh": "数量", "en": "Quantity"}, "format": "decimal"},
                    {"key": "unit", "label": {"zh": "单位", "en": "Unit"}},
                ],
                "rows": aging_buckets,
            },
            {
                "id": "remaining_fifo_layers",
                "title": {"zh": "FIFO 剩余库存层", "en": "Remaining FIFO inventory layers"},
                "columns": [
                    {"key": "batch", "label": {"zh": "批次", "en": "Batch"}},
                    {"key": "receipt_date", "label": {"zh": "入库层日期", "en": "Receipt-layer date"}, "format": "date"},
                    {"key": "age_days", "label": {"zh": "账龄（天）", "en": "Age (days)"}, "format": "integer"},
                    {"key": "remaining_quantity", "label": {"zh": "剩余数量", "en": "Remaining quantity"}, "format": "decimal"},
                    {"key": "unit", "label": {"zh": "单位", "en": "Unit"}},
                    {"key": "bucket_label", "label": {"zh": "账龄分类", "en": "Age category"}},
                ],
                "rows": layer_records if aging_complete else [],
            },
        ])
    if check_expiry:
        expiry_status_labels = {
            "expired": {"zh": "已过期", "en": "Expired"},
            "expiring": {"zh": "即将到期", "en": "Expiring"},
            "not_due": {"zh": "未到临期窗口", "en": "Outside expiry window"},
            "missing_date": {"zh": "效期缺失", "en": "Expiry date missing"},
            "unmatched": {"zh": "批次主数据未关联", "en": "Batch master not matched"},
            "conflict": {"zh": "效期证据冲突", "en": "Conflicting expiry evidence"},
        }
        expiry_source_labels = {
            "api_batch": {"zh": "SAP Batch API", "en": "SAP Batch API"},
            "adt_mcha": {"zh": "SAP MCHA（只读）", "en": "SAP MCHA (read-only)"},
            "none": {"zh": "未取得", "en": "Unavailable"},
        }
        expiry_table_rows = [
            {
                **item,
                "status_label": expiry_status_labels[str(item["status"])],
                "evidence_source_label": expiry_source_labels[str(item["evidence_source"])],
            }
            for item in expiry_details
        ]
        evidence_tables.append(
            {
                "id": "batch_expiry_details",
                "title": {"zh": "批次效期明细", "en": "Batch expiry details"},
                "columns": [
                    {"key": "batch", "label": {"zh": "批次", "en": "Batch"}},
                    {"key": "current_quantity", "label": {"zh": "当前库存", "en": "Current stock"}, "format": "decimal"},
                    {"key": "unit", "label": {"zh": "单位", "en": "Unit"}},
                    {"key": "expiration_date", "label": {"zh": "保质期", "en": "Expiration date"}, "format": "date"},
                    {"key": "days_to_expiry", "label": {"zh": "距到期天数", "en": "Days to expiry"}, "format": "integer"},
                    {"key": "status_label", "label": {"zh": "状态", "en": "Status"}},
                    {"key": "evidence_source_label", "label": {"zh": "数据来源", "en": "Evidence source"}},
                ],
                "rows": expiry_table_rows,
            }
        )
    if evidence_tables:
        result["business_report"]["evidence_tables"] = evidence_tables
    result["workflow_output"].update(records[0])
    result["workflow_output"]["aging_buckets"] = aging_buckets
    result["workflow_output"]["remaining_fifo_layers"] = layer_records if aging_complete else []
    result["workflow_output"]["business_status"] = business_status
    result["workflow_output"]["source_complete"] = source_complete
    result["workflow_output"]["evidence_complete"] = evidence_complete
    return result


def _intelligent_sourcing_rfq(inputs: JsonObject) -> JsonObject:
    rfq = _rows(inputs, "rfq") + _adt_rows(inputs, "rfq")
    quotation_evidence = _rows(inputs, "quotation", "quotations") + _adt_rows(inputs, "quotation")
    suppliers = _rows(inputs, "supplier", "suppliers") + _adt_rows(inputs, "supplier")
    sources = _rows(inputs, "source", "info_records", "contracts") + _adt_rows(inputs, "source")
    complete, missing = _required_topics(inputs, "rfq", "quotation", "supplier", "source")
    quotation_headers = {
        _text(row, "SupplierQuotation", "PurchasingDocument"): row
        for row in quotation_evidence
        if _text(row, "SupplierQuotation", "PurchasingDocument")
        and not _text(row, "SupplierQuotationItem", "PurchasingDocumentItem")
    }
    quotations = []
    for row in quotation_evidence:
        quotation = _text(row, "SupplierQuotation", "PurchasingDocument")
        if not quotation or not _text(row, "SupplierQuotationItem", "PurchasingDocumentItem"):
            continue
        quotations.append({**quotation_headers.get(quotation, {}), **row})
    active = [
        row for row in quotations
        if not _truthy(row.get("IsDeleted"))
        and str(row.get("QuotationStatus") or row.get("PurchasingDocumentStatus") or "").upper()
        not in {"WITHDRAWN", "EXPIRED", "CANCELLED", "D"}
    ]
    comparable_keys = {
        (
            str(row.get("DocumentCurrency") or row.get("Currency") or ""),
            str(row.get("PurchaseOrderQuantityUnit") or row.get("OrderQuantityUnit") or row.get("Unit") or ""),
            str(row.get("PriceUnitQty") or row.get("PriceUnit") or "1"),
        )
        for row in active
    }
    blocked_suppliers = {
        str(row.get("Supplier") or row.get("BusinessPartner") or "")
        for row in suppliers
        if _truthy(row.get("SupplierIsBlockedForPosting") or row.get("PurchasingIsBlockedForSupplier"))
    }
    eligible = [row for row in active if str(row.get("Supplier") or row.get("Bidder") or "") not in blocked_suppliers]
    comparable = complete and len(comparable_keys) == 1 and bool(eligible)
    prices = [_decimal(row.get("NetPriceAmount") or row.get("QuotationPrice")) for row in eligible]
    positive_prices = [price for price in prices if price > 0]
    best_price = min(positive_prices, default=Decimal(0))
    ranked: list[JsonObject] = []
    if comparable and best_price > 0:
        for row, price in zip(eligible, prices):
            # The lowest comparable positive price receives all 60 price
            # points; higher prices receive a proportional share.  This keeps
            # the documented 60/25/15 weighting on a true 100-point scale.
            price_score = float(best_price / price * Decimal(60)) if price > 0 else 0
            delivery_score = 25.0 if any(
                row.get(field)
                for field in (
                    "ScheduleLineDeliveryDate",
                    "DeliveryDate",
                    "PerformancePeriodStartDate",
                )
            ) else 0.0
            completeness_score = 15.0 if all(row.get(field) not in {None, ""} for field in ("Supplier", "NetPriceAmount")) else 7.5
            ranked.append({
                "supplier": str(row.get("Supplier") or row.get("Bidder") or ""),
                "quotation": str(row.get("SupplierQuotation") or row.get("PurchasingDocument") or ""),
                "score": round(price_score + delivery_score + completeness_score, 2),
            })
        ranked.sort(key=lambda item: (-float(item["score"]), item["supplier"], item["quotation"]))
    findings = [] if comparable else [{"code": "QUOTATIONS_NOT_COMPARABLE", "severity": "high"}]
    score_by_quotation = {
        (str(item.get("quotation") or ""), str(item.get("supplier") or "")): item.get("score")
        for item in ranked
    }
    records = [
        {
            "rfq": _text(row, "RequestForQuotation", "RFQ", "PurchasingDocument"),
            "rfq_item": _text(row, "RequestForQuotationItem", "RFQItem", "PurchasingDocumentItem"),
            "supplier": _text(row, "Supplier", "Bidder"),
            "quotation": _text(row, "SupplierQuotation", "PurchasingDocument"),
            "net_price": _text(row, "NetPriceAmount", "QuotationPrice"),
            "currency": _text(row, "DocumentCurrency", "Currency"),
            "unit": _text(row, "PurchaseOrderQuantityUnit", "OrderQuantityUnit", "Unit"),
            "price_unit": _text(row, "PriceUnitQty", "PriceUnit") or "1",
            "score": score_by_quotation.get(
                (
                    _text(row, "SupplierQuotation", "PurchasingDocument"),
                    _text(row, "Supplier", "Bidder"),
                )
            ),
            "eligible": row in eligible,
        }
        for row in active
        if _text(row, "RequestForQuotation", "RFQ", "PurchasingDocument")
        and _text(row, "RequestForQuotationItem", "RFQItem", "PurchasingDocumentItem")
        and _text(row, "Supplier", "Bidder")
    ]
    return _result(
        inputs,
        business_status="attention" if not comparable or blocked_suppliers else "normal",
        headline_zh=(f"已对 {len(ranked)} 份有效报价完成统一评分" if comparable else "报价币种、单位、价格单位或证据不完整，未生成统一排名"),
        headline_en=(f"Ranked {len(ranked)} valid quotation(s) on a common basis" if comparable else "No unified ranking was produced because currency, unit, price unit, or evidence is incomplete"),
        overview_zh="评分固定为价格 60、交期 25、完整性 15；冻结、撤回和失效报价不参与推荐。",
        overview_en="Scoring is fixed at price 60, delivery 25, and completeness 15; blocked, withdrawn, and expired quotations are excluded.",
        stages=[
            _stage("rfq", "询价单", "RFQ", len(rfq), state="confirmed" if _topic_complete(inputs, "rfq") else "unknown"),
            _stage("quotation", "供应商报价", "Supplier quotations", len(quotations), state="confirmed" if _topic_complete(inputs, "quotation") else "unknown"),
            _stage("supplier", "供应商状态", "Supplier status", len(suppliers), state="confirmed" if _topic_complete(inputs, "supplier") else "unknown"),
            _stage("source", "历史货源", "Existing sources", len(sources), state="confirmed" if _topic_complete(inputs, "source") else "unknown"),
        ],
        findings=findings,
        metrics=[{"id": "eligible_quotations", "value": len(eligible)}, {"id": "ranked_quotations", "value": len(ranked)}, {"id": "ranking", "value": ranked}],
        records=records,
        gaps=_gaps(inputs, *missing),
        actions_zh=["由采购员复核评分、商务条款和供应商资格后在 SAP 中决策。"],
        actions_en=["Have purchasing review scores, commercial terms, and supplier eligibility before deciding in SAP."],
        source_complete_override=complete,
    )


def _supplier_performance_risk(inputs: JsonObject) -> JsonObject:
    schedules = _rows(inputs, "po_schedule", "po_schedules") + _adt_rows(inputs, "po_schedule")
    receipt_evidence = _rows(inputs, "receipt", "receipts", "receipt_dates") + _adt_rows(inputs, "receipt")
    receipt_dates = {
        (_text(row, "MaterialDocumentYear", "MJAHR"), _text(row, "MaterialDocument", "MBLNR")):
            _date_text(row, "PostingDate", "BUDAT")
        for row in receipt_evidence
        if _date_text(row, "PostingDate", "BUDAT")
    }
    receipt_items_by_key: dict[tuple[str, str, str, str], JsonObject] = {}
    for index, row in enumerate(receipt_evidence):
        if not _text(row, "PurchaseOrder", "EBELN") or not _text(row, "PurchaseOrderItem", "EBELP"):
            continue
        document_year = _text(row, "MaterialDocumentYear", "MJAHR")
        document = _text(row, "MaterialDocument", "MBLNR")
        document_item = _text(row, "MaterialDocumentItem", "ZEILE")
        key = (
            document_year,
            document,
            document_item,
            "" if document_year and document and document_item else str(index),
        )
        receipt_items_by_key[key] = {**receipt_items_by_key.get(key, {}), **row}
    receipts = [
        {
            **row,
            "PostingDate": _text(row, "PostingDate", "BUDAT")
            or receipt_dates.get((key[0], key[1]), ""),
        }
        for key, row in receipt_items_by_key.items()
    ]
    suppliers = _rows(inputs, "supplier", "suppliers") + _adt_rows(inputs, "supplier")
    complete, missing = _required_topics(inputs, "po_schedule", "receipt", "supplier")
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    as_of = _date(run_input.get("date_to")) or date.today()
    due = [row for row in schedules if (_date(row.get("ScheduleLineDeliveryDate") or row.get("EINDT")) or date.max) <= as_of]
    on_time = 0
    formal = complete and len(due) >= 5
    if formal:
        for schedule in due:
            po = str(schedule.get("PurchaseOrder") or schedule.get("PurchasingDocument") or schedule.get("EBELN") or "")
            item = str(schedule.get("PurchaseOrderItem") or schedule.get("PurchasingDocumentItem") or schedule.get("EBELP") or "")
            delivery = _date(schedule.get("ScheduleLineDeliveryDate") or schedule.get("EINDT"))
            scheduled_qty = _decimal(schedule.get("ScheduleLineOrderQuantity") or schedule.get("MENGE"))
            matching = [
                row for row in receipts
                if str(row.get("PurchaseOrder") or row.get("EBELN") or "") == po
                and str(row.get("PurchaseOrderItem") or row.get("EBELP") or "") == item
            ]
            net = sum(
                (-_decimal(row.get("QuantityInEntryUnit") or row.get("MENGE")) if str(row.get("DebitCreditCode") or row.get("SHKZG") or "S").upper() in {"H", "C"} else _decimal(row.get("QuantityInEntryUnit") or row.get("MENGE")))
                for row in matching
                if (_date(row.get("PostingDate") or row.get("BUDAT")) or date.max) <= (delivery or date.min)
            )
            if scheduled_qty > 0 and net >= scheduled_qty:
                on_time += 1
    otif = round(on_time / len(due) * 100, 2) if formal and due else None
    blocked = any(_truthy(row.get("SupplierIsBlockedForPosting") or row.get("PurchasingIsBlockedForSupplier")) for row in suppliers)
    findings: list[JsonObject] = []
    if len(due) < 5:
        findings.append({"code": "LOW_SAMPLE_CONFIDENCE", "severity": "medium", "sample_size": len(due)})
    if not complete:
        findings.append({"code": "OTIF_SUPPRESSED_INCOMPLETE_EVIDENCE", "severity": "high"})
    if blocked:
        findings.append({"code": "SUPPLIER_BLOCKED", "severity": "high"})
    records: list[JsonObject] = []
    for schedule in due:
        po = _text(schedule, "PurchaseOrder", "PurchasingDocument", "EBELN")
        item = _text(schedule, "PurchaseOrderItem", "PurchasingDocumentItem", "EBELP")
        line = _text(schedule, "ScheduleLine", "ETENR")
        delivery = _date(schedule.get("ScheduleLineDeliveryDate") or schedule.get("EINDT"))
        scheduled_qty = _decimal(schedule.get("ScheduleLineOrderQuantity") or schedule.get("MENGE"))
        matching = [
            row for row in receipts
            if _text(row, "PurchaseOrder", "EBELN") == po
            and _text(row, "PurchaseOrderItem", "EBELP") == item
        ]
        net = sum(
            (-_decimal(row.get("QuantityInEntryUnit") or row.get("MENGE")) if _text(row, "DebitCreditCode", "SHKZG").upper() in {"H", "C"} else _decimal(row.get("QuantityInEntryUnit") or row.get("MENGE")))
            for row in matching
            if (_date(row.get("PostingDate") or row.get("BUDAT")) or date.max) <= (delivery or date.min)
        )
        if po and item and line:
            records.append(
                {
                    "purchase_order": po,
                    "purchase_order_item": item,
                    "schedule_line": line,
                    "delivery_date": delivery.isoformat() if delivery else "",
                    "scheduled_quantity": str(scheduled_qty),
                    "net_receipt_by_due": str(net),
                    "unit": _text(schedule, "PurchaseOrderQuantityUnit", "MEINS"),
                    "on_time_in_full": bool(scheduled_qty > 0 and net >= scheduled_qty),
                }
            )
    return _result(
        inputs,
        business_status="attention" if findings or (otif is not None and otif < 95) else "normal",
        headline_zh=f"正式准时足量交付率(OTIF)为 {otif}%（到期计划行 {len(due)} 条）" if otif is not None else f"到期计划行 {len(due)} 条；因样本或证据不足未计算正式准时足量交付率(OTIF)",
        headline_en=f"Formal On Time In Full (OTIF) is {otif}% across {len(due)} due schedule line(s)" if otif is not None else f"Found {len(due)} due schedule line(s); formal On Time In Full (OTIF) was suppressed due to sample or evidence limits",
        overview_zh="准时足量交付率(OTIF)按计划行与累计净收货计算；少于 5 条到期行标记低样本，缺少交期或收货日期时不形成正式指标。",
        overview_en="On Time In Full (OTIF) is calculated by schedule line and cumulative net receipts; fewer than five due lines is low-confidence, and missing schedule or receipt dates suppress the formal metric.",
        stages=[
            _stage("schedule", "到期计划行", "Due schedule lines", len(due), state="confirmed" if _topic_complete(inputs, "po_schedule") else "unknown"),
            _stage("receipt", "净收货", "Net receipts", len(receipts), state="confirmed" if _topic_complete(inputs, "receipt") else "unknown"),
            _stage("supplier", "供应商状态", "Supplier status", len(suppliers), state="confirmed" if _topic_complete(inputs, "supplier") else "unknown"),
        ],
        findings=findings,
        metrics=[
            {"id": "due_schedule_lines", "label": {"zh": "到期计划行", "en": "Due schedule lines"}, "value": len(due)},
            {"id": "on_time_in_full", "label": {"zh": "准时足量计划行", "en": "On Time In Full schedule lines"}, "value": on_time if formal else None},
            {"id": "otif_percent", "label": {"zh": "准时足量交付率(OTIF)", "en": "On Time In Full (OTIF)"}, "value": otif},
        ],
        records=records,
        gaps=_gaps(inputs, *missing),
        limitations=["low_sample_confidence"] if len(due) < 5 else [],
        actions_zh=["由采购员复核迟交、退货、冲销和供应商冻结后安排改善。"],
        actions_en=["Have purchasing review late deliveries, returns, reversals, and supplier blocks before taking improvement action."],
        source_complete_override=complete,
    )


def _open_po_quantity(row: JsonObject) -> Decimal:
    if row.get("OpenPurchaseOrderQuantity") not in {None, ""}:
        return _decimal(row.get("OpenPurchaseOrderQuantity"))
    ordered = _decimal(row.get("ScheduleLineOrderQuantity"))
    if row.get("ScheduleLineCommittedQuantity") not in {None, ""}:
        return max(ordered - _decimal(row.get("ScheduleLineCommittedQuantity")), Decimal(0))
    return ordered


def _amount(row: JsonObject) -> Decimal:
    for field in (
        "AmountInCompanyCodeCurrency",
        "AmountInObjectCurrency",
        "AmountInTransactionCurrency",
        "HSL",
        "KSL",
        "TSL",
        "WKG",
        "WTG",
    ):
        if row.get(field) not in {None, ""}:
            return _decimal(row.get(field))
    return Decimal(0)


def _currency(row: JsonObject) -> str:
    for field in (
        "CompanyCodeCurrency",
        "ControllingObjectCurrency",
        "TransactionCurrency",
        "RHCUR",
        "RKCUR",
        "RTCUR",
        "WAERS",
    ):
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def _currency_set(rows: list[JsonObject]) -> set[str]:
    return {value for row in rows for value in [_currency(row)] if value}


def _sum_amount(rows: list[JsonObject]) -> Decimal:
    return sum((_amount(row) for row in rows), Decimal(0))


def _cost_center_expense_anomaly(inputs: JsonObject) -> JsonObject:
    masters = _rows(inputs, "cost_centers") + _adt_rows(inputs, "master")
    actual = _rows(inputs, "actual_items") + _adt_rows(inputs, "actual")
    plan = _rows(inputs, "plan_items") + _adt_rows(inputs, "plan")
    complete, missing = _required_topics(inputs, "master", "actual", "plan")
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    threshold = _decimal(run_input.get("variance_threshold_pct") or 20)
    actual_total = _sum_amount(actual)
    plan_total = _sum_amount(plan)
    plan_available = bool(plan)
    variance = actual_total - plan_total if plan_available else None
    currencies = _currency_set(actual + plan)
    comparable = complete and plan_available and len(currencies) <= 1 and plan_total != 0
    variance_pct = (variance / abs(plan_total) * Decimal(100)) if comparable and variance is not None else None
    findings: list[JsonObject] = []
    blocked = any(
        _truthy(row.get(field))
        for row in masters
        for field in (
            "IsBlkdForPrimaryCostsPosting",
            "IsBlkdForSecondaryCostsPosting",
            "BKZKP",
            "BKZKS",
        )
    )
    if blocked:
        findings.append({"code": "COST_CENTER_POSTING_BLOCK", "severity": "high"})
    if len(currencies) > 1:
        findings.append({"code": "CURRENCY_NOT_COMPARABLE", "severity": "high"})
    if not plan_available:
        findings.append({"code": "PLAN_EVIDENCE_MISSING", "severity": "high"})
    elif plan_total == 0:
        findings.append({"code": "PLAN_BASE_MISSING_OR_ZERO", "severity": "medium"})
    if variance_pct is not None and abs(variance_pct) >= threshold:
        findings.append(
            {
                "code": "EXPENSE_VARIANCE_THRESHOLD_EXCEEDED",
                "severity": "high",
                "variance_pct": str(variance_pct.quantize(Decimal("0.01"))),
            }
        )
    return _result(
        inputs,
        business_status="capability_blocked" if not plan_available else ("attention" if findings else "normal"),
        headline_zh=(
            f"成本中心费用偏差为 {variance_pct.quantize(Decimal('0.01'))}%"
            if variance_pct is not None
            else "成本中心费用证据不可形成可比偏差率"
        ),
        headline_en=(
            f"Cost-center expense variance is {variance_pct.quantize(Decimal('0.01'))}%"
            if variance_pct is not None
            else "Cost-center evidence cannot produce a comparable variance percentage"
        ),
        overview_zh="仅在实际、计划、期间和币种证据完整可比时计算偏差率；完整读取不代表费用一定正常。",
        overview_en="Variance is calculated only when actual, plan, period, and currency evidence is complete and comparable; complete reads do not by themselves prove normal spending.",
        stages=[
            _stage("master", "成本中心主数据", "Cost-center master", len(masters), state="confirmed" if _topic_complete(inputs, "master") else "unknown"),
            _stage("actual", "实际费用", "Actual expense", len(actual), state="confirmed" if _topic_complete(inputs, "actual") else "unknown"),
            _stage("plan", "计划费用", "Planned expense", len(plan), state="confirmed" if _topic_complete(inputs, "plan") else "unknown"),
        ],
        findings=findings,
        metrics=[
            {"id": "actual_amount", "value": str(actual_total)},
            {"id": "plan_amount", "value": str(plan_total) if plan_available else None},
            {"id": "variance_amount", "value": str(variance) if variance is not None else None},
            {"id": "variance_pct", "value": str(variance_pct.quantize(Decimal("0.01"))) if variance_pct is not None else None},
            {"id": "currency", "value": next(iter(currencies), None) if len(currencies) <= 1 else None},
        ],
        gaps=_gaps(inputs, *missing, *([] if plan_available else ["plan_evidence_missing"])),
        limitations=["plan_evidence_missing"] if not plan_available else [],
        actions_zh=["由成本中心负责人按科目和期间复核超阈值偏差、一次性费用及错配成本中心。"],
        actions_en=["Have the cost-center owner review threshold breaches, one-off expenses, and misassigned cost centers by account and period."],
        source_complete_override=complete,
    )


def _co_month_end_allocation_settlement(inputs: JsonObject) -> JsonObject:
    postings = _rows(inputs, "actual_items") + _adt_rows(inputs, "posting")
    cycles = _adt_rows(inputs, "allocation_cycle")
    settlement = _adt_rows(inputs, "settlement_rule")
    objects = _adt_rows(inputs, "object_status")
    complete, missing = _required_topics(
        inputs, "posting", "allocation_cycle", "settlement_rule", "object_status"
    )
    deleted_or_closed = any(
        _truthy(row.get(field))
        for row in objects
        for field in ("LOEKZ", "PHAS3", "OrderIsClosed", "OrderIsMarkedForDeletion")
    )
    ready = complete and bool(cycles) and bool(settlement) and bool(objects) and not deleted_or_closed
    ready_value = ready if complete else None
    findings: list[JsonObject] = []
    if not cycles:
        findings.append({"code": "ALLOCATION_CYCLE_NOT_CONFIRMED", "severity": "high"})
    if not settlement:
        findings.append({"code": "SETTLEMENT_RULE_NOT_CONFIRMED", "severity": "high"})
    if not objects:
        findings.append({"code": "CO_OBJECT_NOT_CONFIRMED", "severity": "high"})
    if deleted_or_closed:
        findings.append({"code": "CO_OBJECT_CLOSED_OR_DELETED", "severity": "high"})
    return _result(
        inputs,
        business_status=("ready" if ready else "blocked") if complete else "capability_blocked",
        headline_zh="分配与结算只读检查具备执行条件" if ready else "分配或结算前置证据不足",
        headline_en="Allocation and settlement evidence is ready" if ready else "Allocation or settlement prerequisites are not confirmed",
        overview_zh="本 Agent 只判断单个 CO 对象和周期的只读准备度，不运行分配、分摊、结算或财务关账。",
        overview_en="This Agent only assesses read-only readiness for one CO object and cycle; it never runs allocation, assessment, settlement, or financial close.",
        stages=[
            _stage("posting", "期间 CO 过账", "Period CO postings", len(postings), state="confirmed" if _topic_complete(inputs, "posting") else "unknown"),
            _stage("cycle", "分配周期", "Allocation cycle", len(cycles), state="confirmed" if _topic_complete(inputs, "allocation_cycle") else "unknown"),
            _stage("settlement", "结算规则", "Settlement rule", len(settlement), state="confirmed" if _topic_complete(inputs, "settlement_rule") else "unknown"),
            _stage("object", "CO 对象状态", "CO object status", len(objects), state="confirmed" if _topic_complete(inputs, "object_status") else "unknown"),
        ],
        findings=findings,
        metrics=[
            {"id": "posting_rows", "value": len(postings)},
            {"id": "allocation_cycle_rows", "value": len(cycles) if _topic_complete(inputs, "allocation_cycle") else None},
            {"id": "settlement_rule_rows", "value": len(settlement) if _topic_complete(inputs, "settlement_rule") else None},
            {"id": "ready", "value": ready_value},
        ],
        gaps=_gaps(inputs, *missing),
        actions_zh=["由 CO 月结负责人确认周期有效期、发送方/接收方规则和结算对象状态后，再在 SAP 中人工执行。"],
        actions_en=["Have the CO close owner confirm cycle validity, sender/receiver rules, and settlement-object status before manually executing in SAP."],
        source_complete_override=_topic_complete(inputs, "posting"),
    )


def _product_cost_variance(inputs: JsonObject) -> JsonObject:
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    requested_order = str(run_input.get("manufacturing_order") or "").strip()
    orders = _rows(inputs, "production_order")
    scope = inputs.get("scope") if isinstance(inputs.get("scope"), dict) else {}
    cost_payload = _fallback(inputs, "production_cost")
    details = [
        dict(row)
        for row in cost_payload.get("cost_element_details") or []
        if isinstance(row, dict)
    ]
    gaps = set(_gaps(inputs))
    order_source_complete = _source_complete(
        {"evidence": {"production_order": (inputs.get("evidence") or {}).get("production_order")}}
    )
    if len(orders) != 1:
        gaps.add("production_order_evidence")
    order = orders[0] if len(orders) == 1 else {}
    if order and _text(order, "ManufacturingOrder") != requested_order:
        gaps.add("production_order_scope_mismatch")

    relationship = (
        cost_payload.get("relationship_evidence")
        if isinstance(cost_payload.get("relationship_evidence"), dict)
        else {}
    )
    order_context = (
        cost_payload.get("order_context")
        if isinstance(cost_payload.get("order_context"), dict)
        else {}
    )
    relationship_complete = bool(
        relationship.get("source_complete") is True
        and relationship.get("source") == "AUFK"
        and str(order_context.get("manufacturing_order") or "").strip() == requested_order
        and str(order_context.get("company_code") or "").strip()
        and str(order_context.get("controlling_area") or "").strip()
        and str(order_context.get("object_number") or "").strip()
    )
    if not relationship_complete:
        gaps.add("production_cost_relationship")
    order_company = _text(order, "CompanyCode")
    skill_company = str(order_context.get("company_code") or "").strip()
    if order_company and skill_company and order_company != skill_company:
        gaps.add("production_cost_relationship_conflict")

    completeness = (
        cost_payload.get("completeness")
        if isinstance(cost_payload.get("completeness"), dict)
        else {}
    )
    cost_complete = bool(
        cost_payload.get("status") == "complete"
        and cost_payload.get("validated") is True
        and cost_payload.get("read_only") is True
        and completeness.get("source_complete") is True
        and completeness.get("evidence_complete") is True
        and completeness.get("paging_complete") is True
        and not cost_payload.get("validation_issues")
    )
    if not cost_complete:
        gaps.add("production_cost_evidence")
        for issue in cost_payload.get("validation_issues") or []:
            if isinstance(issue, dict) and issue.get("code"):
                gaps.add(str(issue["code"]))
    if not scope.get("scope_resolved"):
        gaps.add("production_cost_period_scope")

    normalized_details: list[JsonObject] = []
    currencies: set[str] = set()
    ledgers: set[str] = set()
    roles: set[str] = set()
    plan_total = Decimal(0)
    target_total = Decimal(0)
    actual_total = Decimal(0)
    for row in details:
        plan = _strict_decimal(row.get("plan_cost"))
        target = _strict_decimal(row.get("target_cost"))
        actual = _strict_decimal(row.get("actual_cost"))
        variance = _strict_decimal(row.get("actual_target_variance"))
        cost_element = _text(row, "cost_element")
        currency = _text(row, "currency")
        ledger = _text(row, "ledger")
        role = _text(row, "currency_role")
        if (
            not cost_element
            or not currency
            or not ledger
            or not role
            or None in {plan, target, actual, variance}
        ):
            gaps.add("production_cost_element_evidence_incomplete")
        elif actual - target != variance:
            gaps.add("production_cost_variance_reconciliation_mismatch")
        if plan is not None:
            plan_total += plan
        if target is not None:
            target_total += target
        if actual is not None:
            actual_total += actual
        if currency:
            currencies.add(currency)
        if ledger:
            ledgers.add(ledger)
        if role:
            roles.add(role)
        normalized_details.append(
            {
                **row,
                "plan_cost": str(plan) if plan is not None else None,
                "target_cost": str(target) if target is not None else None,
                "actual_cost": str(actual) if actual is not None else None,
                "actual_target_variance": str(variance) if variance is not None else None,
            }
        )
    if cost_complete and not details:
        gaps.add("production_cost_evidence_empty")
    if len(currencies) > 1 or len(ledgers) > 1 or len(roles) > 1:
        gaps.add("production_cost_scope_not_comparable")

    variance_total = actual_total - target_total if details else None
    tolerance = Decimal("0.01")
    if variance_total is None:
        cost_status = "unknown"
    elif target_total == 0 and actual_total != 0:
        cost_status = "unplanned_cost"
    elif target_total != 0 and actual_total == 0:
        cost_status = "planned_cost_not_consumed"
    elif abs(variance_total) <= tolerance:
        cost_status = "normal"
    elif variance_total > 0:
        cost_status = "unfavorable_variance"
    else:
        cost_status = "favorable_variance"
    variance_percent = (
        variance_total / abs(target_total) * Decimal(100)
        if variance_total is not None and target_total != 0
        else None
    )
    source_complete = bool(order_source_complete and cost_complete)
    evidence_complete = bool(source_complete and relationship_complete and not gaps)
    business_status = (
        "inconclusive"
        if not evidence_complete
        else "normal"
        if cost_status == "normal"
        else "attention"
    )
    findings = [
        {
            "code": cost_status,
            "severity": "medium",
            "actual_target_variance": str(variance_total),
        }
    ] if business_status == "attention" else []
    headline_zh = (
        "生产订单成本证据尚不足，不能形成差异结论"
        if business_status == "inconclusive"
        else f"生产订单实际成本与目标成本差异为 {variance_total} {next(iter(currencies), '')}".strip()
    )
    headline_en = (
        "Production-order cost evidence is insufficient for a variance conclusion"
        if business_status == "inconclusive"
        else f"Production-order actual-to-target cost variance is {variance_total} {next(iter(currencies), '')}".strip()
    )
    result = _result(
        inputs,
        business_status=business_status,
        headline_zh=headline_zh,
        headline_en=headline_en,
        overview_zh="本Agent按生产订单、成本要素、期间、账本和币种比较计划、目标与实际成本；标准单价不作为订单目标成本。",
        overview_en="This Agent compares plan, target, and actual costs by production order, cost element, period, ledger, and currency; standard price is not used as order target cost.",
        stages=[
            _stage("order", "生产订单", "Production order", len(orders), state="confirmed" if len(orders) == 1 else "unknown"),
            _stage("relationship", "订单与成本对象关系", "Order-to-cost-object relationship", 1 if relationship_complete else 0, state="confirmed" if relationship_complete else "unknown"),
            _stage("period", "成本分析期间", "Cost analysis period", 1 if scope.get("scope_resolved") else 0, state="confirmed" if scope.get("scope_resolved") else "unknown"),
            _stage("cost", "计划、目标与实际成本", "Plan, target, and actual costs", len(details), state="confirmed" if cost_complete else "unknown"),
        ],
        findings=findings,
        metrics=[
            {"id": "plan_cost_total", "value": str(plan_total) if details else None, "currency": next(iter(currencies), None)},
            {"id": "target_cost_total", "value": str(target_total) if details else None, "currency": next(iter(currencies), None)},
            {"id": "actual_cost_total", "value": str(actual_total) if details else None, "currency": next(iter(currencies), None)},
            {"id": "actual_target_variance", "value": str(variance_total) if variance_total is not None else None, "currency": next(iter(currencies), None)},
        ],
        gaps=sorted(gaps),
        limitations=sorted(gaps),
        records=normalized_details,
        allow_empty_records=True,
        preserve_business_status_on_gap=True,
        source_complete_override=source_complete,
        actions_zh=["先补齐发布成本CDS或经对账的COSP/COSS证据，再由产品成本会计解释成本要素差异。"] if business_status == "inconclusive" else ["由产品成本会计按成本要素复核差异方向和业务原因。"],
        actions_en=["Complete the released cost CDS or reconciled COSP/COSS evidence before interpreting cost-element variance."] if business_status == "inconclusive" else ["Have product-cost accounting review the direction and business cause by cost element."],
    )
    material = _text(order, "Material")
    plant = _text(order, "ProductionPlant")
    analysis_from = str(scope.get("analysis_period_from") or "")
    analysis_to = str(scope.get("analysis_period_to") or "")
    ledger = next(iter(ledgers), str((cost_payload.get("analysis_scope") or {}).get("ledger") or ""))
    role = next(iter(roles), str((cost_payload.get("analysis_scope") or {}).get("currency_role") or ""))
    result["rule_id"] = "production_order_cost_variance_v2"
    result["business_report"].update(
        {
            "cost_element_details": normalized_details,
            "relationship_evidence": relationship,
            "analysis_scope": {
                "from": analysis_from,
                "to": analysis_to,
                "ledger": ledger,
                "currency_role": role,
                "target_cost_variant": 1,
            },
        }
    )
    result["workflow_output"].update(
        {
            "manufacturing_order": requested_order,
            "company_code": skill_company or order_company,
            "controlling_area": str(order_context.get("controlling_area") or ""),
            "material": material,
            "plant": plant,
            "analysis_period_from": analysis_from,
            "analysis_period_to": analysis_to,
            "ledger": ledger,
            "currency_role": role,
            "target_cost_variant": 1,
            "plan_cost_total": str(plan_total) if details else None,
            "target_cost_total": str(target_total) if details else None,
            "actual_cost_total": str(actual_total) if details else None,
            "actual_target_variance": str(variance_total) if variance_total is not None else None,
            "actual_target_variance_percent": str(variance_percent) if variance_percent is not None else None,
            "cost_status": cost_status,
            "cost_element_details": normalized_details,
            "relationship_evidence": relationship,
            "source_complete": source_complete,
            "evidence_complete": evidence_complete,
            "business_status": business_status,
            "business_report": result["business_report"],
        }
    )
    result["business_status"] = business_status
    result["business_complete"] = evidence_complete
    return result


def _budget_rolling_forecast(inputs: JsonObject) -> JsonObject:
    actual = _rows(inputs, "actual_items") + _adt_rows(inputs, "actual")
    plan = _rows(inputs, "plan_items") + _adt_rows(inputs, "plan")
    complete, missing = _required_topics(inputs, "actual", "plan")
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    current_period = max(1, min(12, int(run_input.get("current_period") or 1)))
    actual_total = _sum_amount(actual)
    plan_total = _sum_amount(plan)
    currencies = _currency_set(actual + plan)
    comparable = complete and len(currencies) <= 1
    average = actual_total / Decimal(current_period) if comparable else None
    forecast = actual_total + average * Decimal(12 - current_period) if average is not None else None
    forecast_variance = forecast - plan_total if forecast is not None else None
    forecast_pct = (
        forecast_variance / abs(plan_total) * Decimal(100)
        if forecast_variance is not None and plan_total != 0
        else None
    )
    threshold = _decimal(run_input.get("risk_threshold_pct") or 10)
    findings: list[JsonObject] = []
    if len(currencies) > 1:
        findings.append({"code": "CURRENCY_NOT_COMPARABLE", "severity": "high"})
    if not plan:
        findings.append({"code": "ANNUAL_PLAN_MISSING", "severity": "high"})
    if forecast_pct is not None and forecast_pct > threshold:
        findings.append({"code": "FORECAST_OVER_PLAN", "severity": "high", "variance_pct": str(forecast_pct.quantize(Decimal("0.01")))})
    return _result(
        inputs,
        business_status=(
            "capability_blocked"
            if not plan or not complete or len(currencies) > 1
            else "attention" if findings else "normal"
        ),
        headline_zh=(f"全年滚动预测相对计划偏差 {forecast_pct.quantize(Decimal('0.01'))}%" if forecast_pct is not None else "滚动预测因证据不可比而被抑制"),
        headline_en=(f"Full-year rolling forecast variance is {forecast_pct.quantize(Decimal('0.01'))}% versus plan" if forecast_pct is not None else "Rolling forecast is suppressed because evidence is not comparable"),
        overview_zh="预测使用截至当前期间的简单月均外推，是透明基线而非机器学习预测；不自动回写预算。",
        overview_en="The forecast is a transparent monthly-average extrapolation through the current period, not an ML forecast, and never writes a budget back to SAP.",
        stages=[
            _stage("actual", "累计实际", "Year-to-date actual", len(actual), state="confirmed" if _topic_complete(inputs, "actual") else "unknown"),
            _stage("plan", "全年计划", "Full-year plan", len(plan), state="confirmed" if _topic_complete(inputs, "plan") else "unknown"),
        ],
        findings=findings,
        metrics=[
            {"id": "actual_ytd", "value": str(actual_total)},
            {"id": "annual_plan", "value": str(plan_total) if plan else None},
            {"id": "full_year_forecast", "value": str(forecast) if forecast is not None else None},
            {"id": "forecast_variance_pct", "value": str(forecast_pct.quantize(Decimal("0.01"))) if forecast_pct is not None else None},
        ],
        gaps=_gaps(inputs, *missing),
        limitations=["budget_evidence_missing"] if not plan else [],
        actions_zh=["由预算负责人复核季节性、一次性项目和计划版本后决定是否调整预测。"],
        actions_en=["Have the budget owner review seasonality, one-off items, and the planning version before adjusting the forecast."],
        source_complete_override=complete,
    )


def _internal_order_project_control(inputs: JsonObject) -> JsonObject:
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    resolved = inputs.get("resolved_object") if isinstance(inputs.get("resolved_object"), dict) else {}
    object_type = str(resolved.get("object_type") or run_input.get("object_type") or "").upper()
    actual = _rows(inputs, "order_actual" if object_type == "INTERNAL_ORDER" else "wbs_actual")
    requested_category = str(run_input.get("planning_category") or "").strip().upper()
    plan_topic = (
        "order_plan" if object_type == "INTERNAL_ORDER" else "wbs_plan"
    ) if requested_category else (
        "order_plan_discovery" if object_type == "INTERNAL_ORDER" else "wbs_plan_discovery"
    )
    all_plan = _rows(inputs, plan_topic)
    budgets = _adt_rows(inputs, "budget")
    fallbacks = inputs.get("fallbacks") if isinstance(inputs.get("fallbacks"), dict) else {}
    commitment_payload = (
        fallbacks.get("commitment")
        if isinstance(fallbacks.get("commitment"), dict)
        else {}
    )
    commitment_details = [
        dict(row)
        for row in commitment_payload.get("commitment_details") or []
        if isinstance(row, dict)
    ]
    commitment_totals_payload = (
        commitment_payload.get("commitment_totals")
        if isinstance(commitment_payload.get("commitment_totals"), dict)
        else {}
    )
    commitment_groups = [
        dict(row)
        for row in commitment_totals_payload.get("groups") or []
        if isinstance(row, dict)
    ]
    config = inputs.get("control_value_types") if isinstance(inputs.get("control_value_types"), dict) else {}
    budget_types = {str(value) for value in config.get("budget", ["41"])}
    accepted_version = str(config.get("version") or "000")
    requested_commitment_types = {
        str(value) for value in config.get("commitment_types", ["21", "22", "24", "26"])
    }
    mode_acceptance = config.get("mode_acceptance") if isinstance(config.get("mode_acceptance"), dict) else {}
    mode_acceptance_status = str(mode_acceptance.get(object_type) or "not_tested").strip().lower()
    mode_accepted = mode_acceptance_status in {"pass", "passed", "match"}
    fiscal_year = str(run_input.get("fiscal_year") or "")
    object_number = str(resolved.get("object_number") or "")

    def payload_complete(value: Any) -> bool:
        flags: list[bool] = []

        def visit(item: Any) -> None:
            if isinstance(item, dict):
                if isinstance(item.get("source_complete"), bool):
                    flags.append(bool(item["source_complete"]))
                completeness = item.get("completeness")
                if isinstance(completeness, dict) and isinstance(completeness.get("paging_complete"), bool):
                    flags.append(bool(completeness["paging_complete"]))
                for child in item.values():
                    visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)

        visit(value)
        return bool(flags) and all(flags)

    evidence = inputs.get("evidence") if isinstance(inputs.get("evidence"), dict) else {}
    actual_payload = evidence.get("order_actual" if object_type == "INTERNAL_ORDER" else "wbs_actual")
    plan_payload = evidence.get(plan_topic)
    master_complete = bool(resolved.get("ready") is True and resolved.get("source_complete") is True)
    actual_complete = master_complete and payload_complete(actual_payload)
    plan_query_complete = master_complete and payload_complete(plan_payload)
    budget_query_complete = master_complete and payload_complete(fallbacks.get("budget"))
    commitment_completeness = (
        commitment_payload.get("completeness")
        if isinstance(commitment_payload.get("completeness"), dict)
        else {}
    )
    commitment_query_complete = bool(
        master_complete
        and commitment_payload.get("status") == "complete"
        and commitment_payload.get("read_only") is True
        and commitment_payload.get("validated") is True
        and commitment_completeness.get("source_complete") is True
        and commitment_completeness.get("paging_complete") is True
        and commitment_completeness.get("scope_complete") is True
        and commitment_completeness.get("evidence_complete") is True
        and isinstance(commitment_payload.get("commitment_totals"), dict)
    )

    categories = sorted(
        {
            str(row.get("PlanningCategory") or "").strip().upper()
            for row in all_plan
            if str(row.get("PlanningCategory") or "").strip()
        }
    )
    if requested_category:
        selected_category = requested_category
        plan = [row for row in all_plan if str(row.get("PlanningCategory") or "").strip().upper() == requested_category]
        plan_status = "confirmed" if plan else "not_available"
    elif len(categories) == 1:
        selected_category = categories[0]
        plan = list(all_plan)
        plan_status = "confirmed"
    elif not categories:
        selected_category = ""
        plan = []
        plan_status = "not_available"
    else:
        selected_category = ""
        plan = []
        plan_status = "ambiguous"

    def strict_total(rows: list[JsonObject], field: str) -> tuple[Decimal | None, bool]:
        if not rows:
            return None, False
        values = [_strict_decimal(row.get(field)) for row in rows]
        if any(value is None for value in values):
            return None, False
        return sum((value for value in values if value is not None), Decimal(0)), True

    actual_values = [_strict_decimal(row.get("AmountInCompanyCodeCurrency")) for row in actual]
    actual_valid = actual_complete and all(value is not None for value in actual_values)
    actual_total = (
        sum((value for value in actual_values if value is not None), Decimal(0))
        if actual_valid
        else None
    )
    plan_total, plan_amount_valid = strict_total(plan, "AmountInCompanyCodeCurrency")

    budget_candidates = [
        row
        for row in budgets
        if str(row.get("OBJNR") or "").strip() == object_number
        and str(row.get("GJAHR") or "").strip() == fiscal_year
        and str(row.get("WRTTP") or "").strip() in budget_types
        and str(row.get("VERSN") or "").strip() == accepted_version
    ]
    configured_budget_ledger = str(config.get("budget_ledger") or "").strip()
    budget_ledgers = {str(row.get("LEDNR") or "").strip() for row in budget_candidates if row.get("LEDNR")}
    budget_ledger_ambiguous = (
        not configured_budget_ledger
        or configured_budget_ledger not in budget_ledgers
        or len({ledger for ledger in budget_ledgers if ledger == configured_budget_ledger}) != 1
    )
    budget_matching = [
        row for row in budget_candidates
        if not configured_budget_ledger or str(row.get("LEDNR") or "").strip() == configured_budget_ledger
    ]
    unsupported_types = sorted(
        {
            f"BPJA:{str(row.get('WRTTP') or '').strip()}"
            for row in budgets
            if str(row.get("WRTTP") or "").strip() not in budget_types
        }
    )
    budget_total, budget_amount_valid = strict_total(budget_matching, "WTJHR")
    commitment_values = [_strict_decimal(row.get("amount")) for row in commitment_groups]
    commitment_types = {
        str(row.get("commitment_type") or "").strip()
        for row in commitment_groups
        if row.get("commitment_type")
    }
    commitment_roles = {
        str(row.get("currency_role") or "").strip().upper()
        for row in commitment_groups
        if row.get("currency_role")
    }
    commitment_status_by_type = {
        commitment_type: (
            "confirmed"
            if commitment_query_complete
            and any(
                str(row.get("commitment_type") or "").strip() == commitment_type
                and _strict_decimal(row.get("amount")) is not None
                and bool(str(row.get("currency") or "").strip())
                and bool(str(row.get("currency_role") or "").strip())
                for row in commitment_groups
            )
            else "unknown"
        )
        for commitment_type in sorted(requested_commitment_types)
    }
    commitment_amount_valid = bool(
        commitment_query_complete
        and commitment_groups
        and all(value is not None for value in commitment_values)
        and len(commitment_roles) == 1
        and commitment_types == requested_commitment_types
        and all(value == "confirmed" for value in commitment_status_by_type.values())
    )
    commitment_total = (
        sum((value for value in commitment_values if value is not None), Decimal(0))
        if commitment_amount_valid
        else None
    )
    if budget_ledger_ambiguous:
        budget_total, budget_amount_valid = None, False

    actual_currencies = {
        str(row.get("CompanyCodeCurrency") or "").strip().upper() for row in actual if row.get("CompanyCodeCurrency")
    }
    plan_currencies = {
        str(row.get("CompanyCodeCurrency") or "").strip().upper() for row in plan if row.get("CompanyCodeCurrency")
    }
    budget_currencies = {str(row.get("TWAER") or "").strip().upper() for row in budget_matching if row.get("TWAER")}
    commitment_currencies = {
        str(row.get("currency") or "").strip().upper()
        for row in commitment_groups
        if row.get("currency")
    }
    currencies = actual_currencies | plan_currencies | budget_currencies | commitment_currencies

    def currency_role(value: Any) -> str:
        normalized = str(value or "").strip().upper()
        return {
            "10": "company_code_currency",
            "COMPANY_CODE": "company_code_currency",
            "COMPANY_CODE_CURRENCY": "company_code_currency",
            "TRANSACTION": "transaction_currency",
            "TRANSACTION_CURRENCY": "transaction_currency",
        }.get(normalized, normalized.lower())

    budget_currency_role = currency_role(config.get("budget_currency_role"))
    commitment_currency_role = (
        currency_role(next(iter(commitment_roles))) if len(commitment_roles) == 1 else ""
    )
    currency_roles = {
        role
        for role in (
            "company_code_currency" if actual_currencies else "",
            "company_code_currency" if plan_currencies else "",
            budget_currency_role if budget_currencies else "",
            commitment_currency_role if commitment_currencies else "",
        )
        if role
    }
    currency_sets_complete = all(
        len(item) == 1
        for item in (actual_currencies, plan_currencies, budget_currencies, commitment_currencies)
    )
    currency_role_complete = bool(budget_currency_role and commitment_currency_role)
    currency_comparable = bool(
        currency_sets_complete
        and len(currencies) == 1
        and currency_role_complete
        and currency_roles == {"company_code_currency"}
    )
    comparison_currency = next(iter(currencies)) if currency_comparable else None

    evidence_gaps: list[str] = []
    findings: list[JsonObject] = []
    if not master_complete:
        evidence_gaps.extend(str(item) for item in resolved.get("issues") or ["master_evidence"])
        evidence_gaps.append("master_evidence")
    if not actual_valid:
        evidence_gaps.append("actual_evidence")
    if not plan_query_complete or plan_status != "confirmed" or not plan_amount_valid:
        evidence_gaps.append("plan_evidence" if plan_status != "ambiguous" else "plan_category_ambiguous")
    if not budget_query_complete or not budget_amount_valid:
        evidence_gaps.append("budget_evidence")
    if budget_ledger_ambiguous:
        evidence_gaps.append("budget_ledger_ambiguous")
        findings.append({"code": "BUDGET_LEDGER_AMBIGUOUS", "severity": "high", "ledgers": sorted(budget_ledgers)})
    if not commitment_query_complete or not commitment_amount_valid:
        evidence_gaps.append("commitment_evidence")
    if mode_acceptance_status not in {"pass", "passed", "match"}:
        evidence_gaps.append(
            "wbs_mode_acceptance" if object_type == "WBS" else "internal_order_mode_acceptance"
        )
    for topic in ("budget", "commitment"):
        payload = fallbacks.get(topic)
        if not isinstance(payload, dict):
            continue
        for issue in payload.get("validation_issues") or []:
            if isinstance(issue, dict) and issue.get("code"):
                evidence_gaps.append(str(issue["code"]))
    if unsupported_types:
        evidence_gaps.append("unsupported_value_type")
        findings.append({"code": "UNSUPPORTED_VALUE_TYPE", "severity": "high", "values": unsupported_types})
    if not currency_comparable:
        evidence_gaps.append("currency_not_comparable")
        findings.append({"code": "CURRENCY_NOT_COMPARABLE", "severity": "high", "currencies": sorted(currencies)})

    source_complete = all(
        (master_complete, actual_complete, plan_query_complete, budget_query_complete, commitment_query_complete)
    )
    evidence_gaps = sorted(set(evidence_gaps))
    evidence_complete = source_complete and not evidence_gaps and mode_accepted
    calculation_ready = bool(
        evidence_complete
        and actual_total is not None
        and plan_total is not None
        and budget_total is not None
        and commitment_total is not None
        and currency_comparable
    )
    eac = actual_total + commitment_total if calculation_ready else None
    remaining = budget_total - eac if budget_total is not None and eac is not None else None
    consumption_pct = (
        eac / abs(budget_total) * Decimal(100)
        if budget_total not in {None, Decimal(0)} and eac is not None
        else None
    )
    if evidence_complete and eac is not None and budget_total is not None and eac > budget_total:
        findings.append({"code": "EAC_EXCEEDS_BUDGET", "severity": "high", "excess": str(eac - budget_total)})
        business_status = "attention"
    elif evidence_complete:
        business_status = "normal"
    elif str(resolved.get("status") or "") == "blocked":
        business_status = "blocked"
    else:
        business_status = "inconclusive"

    def status(complete: bool, available: bool) -> str:
        return "confirmed" if complete and available else "not_available" if complete else "unknown"

    actual_status = status(actual_complete and actual_valid, actual_total is not None)
    budget_status = status(budget_query_complete and budget_amount_valid, budget_total is not None)
    commitment_status = status(commitment_query_complete and commitment_amount_valid, commitment_total is not None)
    metric_values = {
        "actual_amount": str(actual_total) if actual_total is not None else None,
        "plan_amount": str(plan_total) if plan_total is not None else None,
        "budget_amount": str(budget_total) if budget_total is not None else None,
        "commitment_amount": str(commitment_total) if commitment_total is not None else None,
        "estimate_at_completion": str(eac) if eac is not None else None,
        "remaining_budget": str(remaining) if remaining is not None else None,
        "budget_consumption_percent": (
            str(consumption_pct.quantize(Decimal("0.01"))) if consumption_pct is not None else None
        ),
    }
    metrics = [{"id": key, "value": value} for key, value in metric_values.items()]
    headline_zh = (
        f"预计完工成本占预算 {metric_values['budget_consumption_percent']}%"
        if evidence_complete and metric_values["budget_consumption_percent"] is not None
        else "订单/项目控制证据尚不完整"
    )
    headline_en = (
        f"Estimate at completion consumes {metric_values['budget_consumption_percent']}% of budget"
        if evidence_complete and metric_values["budget_consumption_percent"] is not None
        else "Order/project control evidence is incomplete"
    )
    result = _result(
        inputs,
        business_status=business_status,
        headline_zh=headline_zh,
        headline_en=headline_en,
        overview_zh="预计完工成本只按实际成本加未清承诺计算；计划金额用于对比，不参与 EAC。缺失证据不会按零处理。",
        overview_en="EAC is actual cost plus open commitments only; plan is comparative and is not included in EAC. Missing evidence is never treated as zero.",
        stages=[
            _stage("master", "订单/WBS 主数据", "Order/WBS master", 1 if master_complete else 0, state="confirmed" if master_complete else "unknown"),
            _stage("actual", "实际成本", "Actual cost", len(actual), state=actual_status),
            _stage("plan", "计划成本", "Planned cost", len(plan), state="confirmed" if plan_status == "confirmed" else "unknown"),
            _stage("budget", "预算", "Budget", len(budget_matching), state=budget_status),
            _stage("commitment", "承诺", "Commitments", len(commitment_details), state=commitment_status),
        ],
        findings=findings,
        metrics=metrics,
        gaps=evidence_gaps,
        limitations=["no_currency_conversion"] if not currency_comparable else [],
        actions_zh=["补齐报告中列出的计划、预算、承诺或对象主数据证据后重新运行。"] if evidence_gaps else ["由订单或项目负责人复核预计完工成本和剩余预算。"],
        actions_en=["Complete the listed plan, budget, commitment, or object-master evidence and rerun."] if evidence_gaps else ["Have the order or project owner review EAC and remaining budget."],
        source_complete_override=source_complete,
        preserve_business_status_on_gap=True,
    )
    resolved_public = {
        key: resolved.get(key)
        for key in (
            "object_type",
            "external_id",
            "internal_id",
            "object_number",
            "company_code",
            "controlling_area",
            "project_internal_id",
            "project_external_id",
            "order_category",
            "order_type",
        )
    }
    extras: JsonObject = {
        "resolved_object": resolved_public,
        **metric_values,
        "actual_status": actual_status,
        "plan_status": plan_status,
        "selected_planning_category": selected_category or None,
        "available_planning_categories": categories,
        "budget_status": budget_status,
        "commitment_status": commitment_status,
        "commitment_status_by_type": commitment_status_by_type,
        "commitment_currency_role": commitment_currency_role or None,
        "budget_ledger": configured_budget_ledger or None,
        "budget_currency_role": budget_currency_role or None,
        "comparison_currency": comparison_currency,
        "mode_acceptance_status": mode_acceptance_status,
        "evidence_complete": evidence_complete,
        "evidence_gaps": evidence_gaps,
    }
    result.update(extras)
    result["rule_id"] = "internal_order_project_control_deterministic_v4"
    result["status"] = "complete" if evidence_complete else "inconclusive"
    result["business_complete"] = evidence_complete
    result["business_report"].update(
        {
            "source_complete": source_complete,
            "evidence_complete": evidence_complete,
            "resolved_object": resolved_public,
            "selected_planning_category": selected_category or None,
            "available_planning_categories": categories,
            "budget_ledger": configured_budget_ledger or None,
            "budget_currency_role": budget_currency_role or None,
            "commitment_currency_role": commitment_currency_role or None,
            "comparison_currency": comparison_currency,
            "mode_acceptance_status": mode_acceptance_status,
            "evidence_tables": [
                {
                    "id": "object_relationship",
                    "title": {"zh": "控制对象关系", "en": "Control-object relationship"},
                    "columns": ["object_type", "external_id", "internal_id", "object_number", "company_code", "controlling_area"],
                    "rows": [resolved_public],
                },
                {
                    "id": "actual_and_plan",
                    "title": {"zh": "实际与计划", "en": "Actual and plan"},
                    "columns": ["actual_amount", "plan_amount", "planning_category", "currency"],
                    "rows": [{"actual_amount": metric_values["actual_amount"], "plan_amount": metric_values["plan_amount"], "planning_category": selected_category or None, "currency": comparison_currency}],
                },
                {
                    "id": "budget",
                    "title": {"zh": "预算", "en": "Budget"},
                    "columns": ["budget_amount", "ledger", "value_type", "currency", "currency_role"],
                    "rows": [{"budget_amount": metric_values["budget_amount"], "ledger": configured_budget_ledger or None, "value_type": accepted_version and "/".join(sorted(budget_types)), "currency": next(iter(budget_currencies), None), "currency_role": budget_currency_role or None}],
                },
                {
                    "id": "commitments_by_type",
                    "title": {"zh": "按值类型的承诺", "en": "Commitments by value type"},
                    "columns": ["commitment_type", "amount", "currency", "currency_role", "status"],
                    "rows": [{**row, "status": commitment_status_by_type.get(str(row.get("commitment_type") or ""), "unknown")} for row in commitment_groups],
                },
                {
                    "id": "eac_and_remaining_budget",
                    "title": {"zh": "预计完工成本与剩余预算", "en": "EAC and remaining budget"},
                    "columns": ["estimate_at_completion", "remaining_budget", "budget_consumption_percent", "currency"],
                    "rows": [{"estimate_at_completion": metric_values["estimate_at_completion"], "remaining_budget": metric_values["remaining_budget"], "budget_consumption_percent": metric_values["budget_consumption_percent"], "currency": comparison_currency}],
                },
            ],
        }
    )
    result["workflow_output"].update(extras)
    result["workflow_output"]["business_report"] = result["business_report"]
    return result


_EVALUATORS: dict[str, Callable[[JsonObject], JsonObject]] = {
    "ap-payment": _ap_payment,
    "ar-collection": _ar_collection,
    "ar-cash-application": _ar_cash_application,
    "gr-ir-clearing": _grir,
    "month-end-closing": _month_end,
    "billing-block-diagnosis": _billing_block,
    "billing-completeness-check": _billing_completeness,
    "billing-dispute-classification": _billing_dispute,
    "billing-output-monitor": _billing_output,
    "delivered-not-billed": _delivered_not_billed,
    "delivery-delay-prediction": _delivery_delay,
    "due-delivery-prioritization": _due_priority,
    "order-to-cash-anomaly-monitor": _o2c_anomaly,
    "returns-credit-anomaly": _returns_credit,
    "shortage-allocation-advisor": _shortage_allocation,
    "demand-forecast-planning": _demand_forecast,
    "new-sales-demand-coverage": _new_sales_demand_coverage,
    "mrp-exception-analysis": _mrp_exception,
    "production-order-monitoring": _production_monitor,
    "production-scheduling-capacity": _production_schedule,
    "production-variance-analysis": _production_variance,
    "material-shortage-procurement-response": _material_shortage_procurement,
    "inventory-health-balancing": _inventory_health_balancing,
    "intelligent-sourcing-rfq": _intelligent_sourcing_rfq,
    "supplier-performance-risk": _supplier_performance_risk,
    "cost-center-expense-anomaly": _cost_center_expense_anomaly,
    "co-month-end-allocation-settlement": _co_month_end_allocation_settlement,
    "product-cost-variance": _product_cost_variance,
    "budget-rolling-forecast": _budget_rolling_forecast,
    "internal-order-project-control": _internal_order_project_control,
}
