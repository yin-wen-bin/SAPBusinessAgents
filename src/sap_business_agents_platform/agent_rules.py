from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Callable


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
    items = [
        row
        for row in _rows(inputs, "collect_ap_evidence", "supplier_items", "clearing_documents")
        if str(row.get("FinancialAccountType") or "K") == "K"
        and _truthy(row.get("IsOpenItemManaged"))
    ]
    cutoff = _date((inputs.get("run_input") or {}).get("as_of"))
    open_items: list[JsonObject] = []
    normalized_records: list[JsonObject] = []
    for row in items:
        clearing_date = _date(row.get("ClearingDate"))
        if cutoff is None or clearing_date is None or clearing_date > cutoff:
            open_items.append(row)
            normalized_records.append(
                {
                    "company_code": str(row.get("CompanyCode") or ""),
                    "fiscal_year": str(row.get("FiscalYear") or ""),
                    "accounting_document": str(row.get("AccountingDocument") or ""),
                    "accounting_document_item": str(row.get("AccountingDocumentItem") or ""),
                    "ledger": str(row.get("Ledger") or ""),
                    "posting_date": (
                        _date(row.get("PostingDate")).isoformat()
                        if _date(row.get("PostingDate")) is not None
                        else ""
                    ),
                    "debit_credit": str(row.get("DebitCreditCode") or ""),
                    "amount": str(row.get("AmountInTransactionCurrency") or ""),
                    "currency": str(row.get("TransactionCurrency") or ""),
                    "as_of_status": (
                        "open_subsequently_cleared" if clearing_date is not None else "open"
                    ),
                    "clearing_date": clearing_date.isoformat() if clearing_date is not None else "",
                    "clearing_document": str(row.get("ClearingAccountingDocument") or ""),
                    "payment_evidence_status": "bank_settlement_not_proven",
                }
            )
    blocked = [row for row in open_items if row.get("PaymentBlockingReason")]
    gaps = _gaps(
        inputs,
        "bank_settlement_not_proven",
        "payment_run_and_bank_master_evidence",
    )
    return _result(
        inputs,
        business_status="attention" if open_items else "partial",
        headline_zh=f"发现 {len(open_items)} 条供应商未清项，其中 {len(blocked)} 条存在付款冻结",
        headline_en=f"Found {len(open_items)} supplier open item(s), including {len(blocked)} payment-blocked item(s)",
        overview_zh="已检查未清、到期、冻结和清账证据；当前不包含完整付款运行和银行主数据。",
        overview_en="Open, due, blocked, and clearing evidence was checked; complete payment-run and bank-master evidence is not included.",
        stages=[_stage("supplier_items", "供应商行项目", "Supplier items", len(items)), _stage("payment_run", "付款运行与银行证据", "Payment run and bank evidence", 0, state="unknown")],
        metrics=[{"id": "open_items", "value": len(open_items)}, {"id": "payment_blocked", "value": len(blocked)}],
        gaps=gaps,
        records=normalized_records,
        record_columns=[
            {"key": "accounting_document", "label": {"zh": "会计凭证", "en": "Accounting document"}},
            {"key": "accounting_document_item", "label": {"zh": "行项目", "en": "Item"}},
            {"key": "posting_date", "label": {"zh": "过账日期", "en": "Posting date"}, "format": "date"},
            {"key": "debit_credit", "label": {"zh": "借贷方向", "en": "Debit/Credit"}},
            {"key": "amount", "label": {"zh": "金额", "en": "Amount"}, "format": "decimal"},
            {"key": "currency", "label": {"zh": "币种", "en": "Currency"}},
            {"key": "as_of_status", "label": {"zh": "截止日状态", "en": "As-of status"}, "format": "status"},
            {"key": "clearing_document", "label": {"zh": "当前清账凭证", "en": "Current clearing document"}},
            {"key": "payment_evidence_status", "label": {"zh": "付款证据", "en": "Payment evidence"}, "format": "status"},
        ],
        actions_zh=["由应付人员复核到期日、付款冻结和付款运行。"],
        actions_en=["Have AP review due dates, payment blocks, and the payment run."],
    )


def _ar_collection(inputs: JsonObject) -> JsonObject:
    items = [
        row
        for row in _rows(inputs, "customer_items", "clearing_documents")
        if str(row.get("FinancialAccountType") or "D") == "D"
        and _truthy(row.get("IsOpenItemManaged"))
    ]
    cutoff = _date((inputs.get("run_input") or {}).get("as_of"))
    dunning_master = _rows(inputs, "customer_dunning")
    open_items = []
    records: list[JsonObject] = []
    dunned_items = 0
    historical_dunning_unknown = 0
    for row in items:
        posting_date = _date(row.get("PostingDate"))
        clearing_date = _date(row.get("ClearingDate"))
        if cutoff is not None and posting_date is not None and posting_date > cutoff:
            continue
        if cutoff is None or clearing_date is None or clearing_date > cutoff:
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
            elif last_dunning_date is not None and cutoff is not None and last_dunning_date > cutoff:
                dunning_status = "historical_status_unknown"
                historical_dunning_unknown += 1
            else:
                # The current line-item snapshot cannot prove that a blank
                # last-dunning date also was blank at the historical cutoff.
                # Without complete dunning-run history, fail closed.
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
    current_master_has_only_later_state = any(
        cutoff is not None
        and (last_dunned := _date(row.get("LastDunnedOn"))) is not None
        and last_dunned > cutoff
        for row in dunning_master
    )
    gaps = _gaps(inputs)
    if current_master_has_only_later_state or historical_dunning_unknown:
        gaps = sorted({*gaps, "historical_dunning_evidence"})
    return _result(
        inputs,
        business_status="inconclusive" if gaps else ("attention" if open_items else "complete"),
        headline_zh=f"发现 {len(open_items)} 条客户未清应收",
        headline_en=f"Found {len(open_items)} customer open receivable item(s)",
        overview_zh="已按清账日期重建截止日未清状态，并区分逐项催款日期证据与当前客户催款主数据；当前主数据不能替代历史快照。",
        overview_en="As-of open status was reconstructed from clearing dates, with item-level dunning dates separated from the current customer dunning master; current master data is not a historical snapshot.",
        stages=[_stage("receivables", "客户应收", "Customer receivables", len(items)), _stage("customer_dunning", "客户催款主数据", "Customer dunning master", len(dunning_master))],
        metrics=[
            {"id": "open_items", "value": len(open_items)},
            {"id": "dunned_items", "value": dunned_items},
            {"id": "historical_dunning_unknown", "value": historical_dunning_unknown},
        ],
        records=records,
        gaps=gaps,
        actions_zh=["按到期日和金额安排催收；对缺少截止日历史催款快照的项目复核催款日志。"],
        actions_en=["Prioritize collection by due date and amount; review the dunning log where an as-of historical snapshot is unavailable."],
    )


def _grir(inputs: JsonObject) -> JsonObject:
    pos = _rows(inputs, "purchase_orders", "purchase_order_items")
    receipts = _rows(inputs, "material_documents")
    invoices = _rows(inputs, "supplier_invoice_items")
    gl = _rows(inputs, "gl_items")
    attention = bool(gl) or len(receipts) != len(invoices)
    po_items = [
        row
        for row in pos
        if _text(row, "PurchaseOrder") and _text(row, "PurchaseOrderItem")
    ]
    records: list[JsonObject] = []
    for row in po_items:
        po = _text(row, "PurchaseOrder")
        item = _text(row, "PurchaseOrderItem")
        item_receipts = [
            value
            for value in receipts
            if _text(value, "PurchaseOrder") == po
            and _text(value, "PurchaseOrderItem") == item
        ]
        item_invoices = [
            value
            for value in invoices
            if _text(value, "PurchaseOrder") == po
            and _text(value, "PurchaseOrderItem") == item
        ]
        records.append(
            {
                "purchase_order": po,
                "purchase_order_item": item,
                "material": _text(row, "Material"),
                "receipt_rows": len(item_receipts),
                "invoice_rows": len(item_invoices),
                "receipt_quantity": str(
                    sum(
                        (_decimal(value.get("QuantityInEntryUnit") or value.get("QuantityInBaseUnit")))
                        for value in item_receipts
                    )
                ),
                "invoice_quantity": str(
                    sum(
                        (_decimal(value.get("QuantityInPurchaseOrderUnit")))
                        for value in item_invoices
                    )
                ),
                "unit": _text(
                    row,
                    "PurchaseOrderQuantityUnit",
                    "OrderQuantityUnit",
                    "OrderPriceUnit",
                ),
            }
        )
    return _result(
        inputs,
        business_status="attention" if attention else "normal",
        headline_zh=f"GR/IR 范围包含 {len(pos)} 条采购证据、{len(receipts)} 条收货和 {len(invoices)} 条发票",
        headline_en=f"GR/IR scope contains {len(pos)} purchasing, {len(receipts)} receipt, and {len(invoices)} invoice evidence row(s)",
        overview_zh="规则按采购项目保留收货、冲销、发票和总账证据，用于净额和账龄分析。",
        overview_en="Receipt, reversal, invoice, and G/L evidence is retained by purchase-order item for netting and aging.",
        stages=[_stage("purchase_order", "采购订单", "Purchase orders", len(pos)), _stage("receipt", "收货与冲销", "Receipts and reversals", len(receipts)), _stage("invoice", "供应商发票", "Supplier invoices", len(invoices)), _stage("gl", "GR/IR 总账", "GR/IR G/L", len(gl))],
        metrics=[{"id": "receipt_rows", "value": len(receipts)}, {"id": "invoice_rows", "value": len(invoices)}, {"id": "gl_rows", "value": len(gl)}],
        records=records,
        actions_zh=["复核收货与发票不一致的采购项目。"] if attention else [],
        actions_en=["Review purchase-order items whose receipt and invoice evidence does not align."] if attention else [],
    )


def _month_end(inputs: JsonObject) -> JsonObject:
    fi = _rows(inputs, "fi_period_items")
    gaps = _gaps(inputs, "period_control_asset_depreciation_and_specialized_closing_checks")
    return _result(
        inputs,
        business_status="capability_blocked",
        headline_zh="已取得部分月结证据，但不能确认可以关账",
        headline_en="Partial month-end evidence was collected, but closing readiness cannot be confirmed",
        overview_zh="当前只覆盖已发布的 FI 行项目；期间控制、固定资产折旧和专项关账检查缺失。",
        overview_en="Only published FI line items are covered; period control, asset depreciation, and specialized closing checks are missing.",
        stages=[_stage("fi", "FI 期间凭证", "FI period documents", len(fi)), _stage("closing_checks", "完整关账检查清单", "Complete closing checklist", 0, state="unknown")],
        metrics=[{"id": "fi_rows", "value": len(fi)}],
        gaps=gaps,
        actions_zh=["补充公司代码和期间绑定的只读关账检查接口后再判断。"],
        actions_en=["Add company-code and period-bound read-only closing checks before assessing readiness."],
    )


def _demand_forecast(inputs: JsonObject) -> JsonObject:
    demand = _rows(inputs, "sales_demand")
    planned = _rows(inputs, "planned_orders")
    gaps = _gaps(inputs, "pir_evidence")
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    demand_period_attributable = not demand or all(
        _date_text(row, "RequirementDate", "RequestedDeliveryDate")
        for row in demand
    )
    attributable_demand = demand if demand_period_attributable else []
    grouped: dict[tuple[str, str, str], JsonObject] = {}
    for row, kind in [(row, "demand") for row in attributable_demand] + [(row, "planned") for row in planned]:
        key = (
            _text(row, "Material"),
            _text(row, "Plant", "ProductionPlant"),
            _date_text(
                row,
                "RequirementDate",
                "PlannedOrderOpeningDate",
                "PlannedOrderEndDate",
                "PlndOrderPlannedStartDate",
                "PlndOrderPlannedEndDate",
            )
            or str(run_input.get("date_from") or ""),
        )
        if not all(key):
            continue
        record = grouped.setdefault(
            key,
            {
                "material": key[0],
                "plant": key[1],
                "requirement_date": key[2],
                "demand_quantity": "0",
                "planned_quantity": "0",
                "unit": _text(row, "RequestedQuantityUnit", "ProductionUnit", "BaseUnit"),
            },
        )
        field = "demand_quantity" if kind == "demand" else "planned_quantity"
        record[field] = str(
            _decimal(record[field])
            + _decimal(
                row.get("RequestedQuantity")
                or row.get("OrderQuantity")
                or row.get("TotalQuantity")
            )
        )
    return _result(
        inputs,
        business_status="capability_blocked",
        headline_zh="已读取销售需求和计划订单，但不能完成 PIR 计划比较",
        headline_en="Sales demand and planned orders were read, but PIR comparison cannot be completed",
        overview_zh="缺少同物料、工厂和期间的 PIR 只读证据；本次不训练模型也不写回 PIR。",
        overview_en="Read-only PIR evidence for the same material, plant, and period is missing; no model is trained and no PIR is written.",
        stages=[_stage("demand", "销售需求", "Sales demand", len(demand)), _stage("planned", "计划订单", "Planned orders", len(planned)), _stage("pir", "独立需求 PIR", "Planned independent requirements", 0, state="unknown")],
        metrics=[
            {"id": "demand_rows", "value": len(demand) if demand_period_attributable else None},
            {"id": "planned_order_rows", "value": len(planned)},
        ],
        gaps=gaps,
        limitations=["sales_demand_period_evidence"],
        records=list(grouped.values()),
        actions_zh=["增加经过审批的 PBIM/PBED 只读 Skill 后再运行。"],
        actions_en=["Add an approved PBIM/PBED read-only Skill and rerun."],
    )


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
    items = _rows(inputs, "production_order_items")
    operations = _rows(inputs, "production_operations")
    components = _rows(inputs, "production_components")
    movements = _rows(inputs, "material_documents")
    costs = _rows(inputs, "cost_items")
    gaps = _gaps(inputs, *("production_cost_evidence",) if not costs else ())
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    records = [
        {
            "manufacturing_order": _text(row, "ManufacturingOrder", "OrderID") or str(run_input.get("manufacturing_order") or ""),
            "cost_element": _text(row, "CostElement", "GLAccount"),
            "actual_amount": _text(row, "AmountInCompanyCodeCurrency", "AmountInObjectCurrency"),
            "currency": _text(row, "CompanyCodeCurrency", "ControllingObjectCurrency"),
        }
        for row in costs
        if _text(row, "CostElement", "GLAccount")
    ]
    return _result(
        inputs,
        business_status="attention" if movements or costs else "partial",
        headline_zh="生产偏差证据已按数量、工序、用料和成本分维度检查",
        headline_en="Production variance evidence was checked separately for quantity, operations, material use, and cost",
        overview_zh="没有实际活动时只报告“尚无过账证据”，不会解释为零偏差或表现正常。",
        overview_en="No actual activity is reported as no posting evidence, not as zero variance or good performance.",
        stages=[_stage("quantity", "订单数量", "Order quantity", len(items)), _stage("operations", "工序确认", "Operation confirmation", len(operations)), _stage("materials", "组件与领料", "Components and issues", len(components) + len(movements)), _stage("costs", "成本行项目", "Cost items", len(costs), state="confirmed" if costs else "unknown")],
        metrics=[
            {"id": "operation_rows", "value": len(operations)},
            {"id": "movement_rows", "value": len(movements)},
            {"id": "cost_rows", "value": len(costs)},
        ],
        gaps=gaps,
        records=records,
        allow_empty_records=True,
        actions_zh=["对缺少实际活动或成本证据的订单补充过账与结算核查。"],
        actions_en=["Add posting and settlement checks for orders without actual-activity or cost evidence."],
    )


def _material_shortage_procurement(inputs: JsonObject) -> JsonObject:
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    as_of = _date(run_input.get("as_of")) or date.today()
    master = _rows(inputs, "mrp_master")
    # MaterialCoverages is the authoritative shortage aggregate for this Agent.
    # SupplyDemandItems may corroborate the situation, but its receipt and stock
    # rows must never be added to the reported shortage quantity.
    mrp = _rows(inputs, "mrp", "mrp_coverage") + _adt_rows(inputs, "mrp")
    requisitions = _rows(inputs, "pr", "purchase_requisitions") + _adt_rows(inputs, "pr")
    orders = _rows(inputs, "po_schedule", "purchase_orders") + _adt_rows(inputs, "po_schedule")
    sources = _rows(inputs, "source", "info_records", "contracts", "suppliers") + _adt_rows(inputs, "source")
    topic_complete, missing = _required_topics(inputs, "mrp", "pr", "po_schedule", "source")
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
    pending_pr = [
        row for row in requisitions
        if not _truthy(row.get("IsDeleted"))
        and not _truthy(row.get("IsClosed"))
        and str(row.get("ProcessingStatus") or "").upper() == "N"
        and str(row.get("PurReqnReleaseStatus") or row.get("ReleaseStatus") or "").upper()
        not in {"05", "08", "C", "RELEASED", "COMPLETED"}
    ]
    expediting = [
        row for row in orders
        if (_date(row.get("ScheduleLineDeliveryDate") or row.get("DeliveryDate")) or date.max) < as_of
        and _open_po_quantity(row) > 0
    ]
    valid_sources = [
        row
        for row in sources
        if not _truthy(row.get("IsMarkedForDeletion"))
        and _truthy(row.get("IsRelevantForAutomSrcg"))
    ]
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
        for row in pending_pr:
            material = _text(row, "Material") or str(run_input.get("material") or "")
            plant = _text(row, "Plant") or str(run_input.get("plant") or "")
            requisition = _text(row, "PurchaseRequisition", "BANFN")
            item = _text(row, "PurchaseRequisitionItem", "BNFPO")
            if material and plant and requisition and item:
                records.append(
                    {
                        "material": material,
                        "plant": plant,
                        "requirement_id": f"PR:{requisition}/{item}",
                        "requirement_date": _date_text(row, "DeliveryDate", "RequirementDate", "PurchaseRequisitionReleaseDate"),
                        "mrp_element_type": "purchase_requisition",
                        "shortage_quantity": "",
                        "unit": _text(row, "BaseUnit", "PurchaseRequisitionQuantityUnit"),
                    }
                )
    return _result(
        inputs,
        business_status=(
            "capability_blocked"
            if not complete
            else "attention"
            if shortage or pending_pr or expediting
            else "normal"
        ),
        headline_zh=(
            f"识别到 {len(pending_pr)} 条待处理 PR、{len(expediting)} 条催交 PO；确定缺口为 {shortage}"
            if comparable else
            f"识别到 {len(pending_pr)} 条待处理 PR、{len(expediting)} 条催交 PO；缺口数量无法确定"
        ),
        headline_en=(
            f"Found {len(pending_pr)} pending PR(s), {len(expediting)} PO line(s) to expedite; confirmed shortage is {shortage}"
            if comparable else
            f"Found {len(pending_pr)} pending PR(s), {len(expediting)} PO line(s) to expedite; shortage quantity is inconclusive"
        ),
        overview_zh="MRP、PR、PO 交期和货源证据均完整且单位可比时才计算确定缺口；本 Agent 只给出处置建议。",
        overview_en="A confirmed shortage is calculated only when MRP, PR, PO schedule, and source evidence are complete and units are comparable; the Agent is advisory only.",
        stages=[
            _stage("mrp", "MRP 供需", "MRP supply and demand", len(mrp), state="confirmed" if _topic_complete(inputs, "mrp") else "unknown"),
            _stage("pr", "采购申请", "Purchase requisitions", len(requisitions), state="confirmed" if _topic_complete(inputs, "pr") else "unknown"),
            _stage("po", "采购订单交期", "PO schedules", len(orders), state="confirmed" if _topic_complete(inputs, "po_schedule") else "unknown"),
            _stage("source", "有效货源", "Valid sources", len(valid_sources), state="confirmed" if _topic_complete(inputs, "source") else "unknown"),
        ],
        findings=findings,
        metrics=[
            {"id": "shortage_quantity", "value": str(shortage) if comparable else None},
            {"id": "pending_pr", "value": len(pending_pr)},
            {"id": "expedite_po", "value": len(expediting)},
            {"id": "valid_source_candidates", "value": len(valid_sources)},
        ],
        records=records,
        gaps=_gaps(inputs, *missing),
        actions_zh=["释放或转换合格 PR，并由采购员复核逾期 PO 与有效货源。"],
        actions_en=["Release or convert eligible PRs and have purchasing review overdue POs and valid sources."],
        source_complete_override=complete,
    )


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
        headline_zh=f"正式 OTIF 为 {otif}%（到期计划行 {len(due)} 条）" if otif is not None else f"到期计划行 {len(due)} 条；因样本或证据不足未计算正式 OTIF",
        headline_en=f"Formal OTIF is {otif}% across {len(due)} due schedule line(s)" if otif is not None else f"Found {len(due)} due schedule line(s); formal OTIF was suppressed due to sample or evidence limits",
        overview_zh="OTIF 按 schedule line 与累计净收货计算；少于 5 条到期行标记低样本，缺少交期或收货日期时不形成正式指标。",
        overview_en="OTIF is calculated by schedule line and cumulative net receipts; fewer than five due lines is low-confidence, and missing schedule or receipt dates suppress the formal metric.",
        stages=[
            _stage("schedule", "到期计划行", "Due schedule lines", len(due), state="confirmed" if _topic_complete(inputs, "po_schedule") else "unknown"),
            _stage("receipt", "净收货", "Net receipts", len(receipts), state="confirmed" if _topic_complete(inputs, "receipt") else "unknown"),
            _stage("supplier", "供应商状态", "Supplier status", len(suppliers), state="confirmed" if _topic_complete(inputs, "supplier") else "unknown"),
        ],
        findings=findings,
        metrics=[{"id": "due_schedule_lines", "value": len(due)}, {"id": "on_time_in_full", "value": on_time if formal else None}, {"id": "otif_percent", "value": otif}],
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
    orders = _rows(inputs, "production_orders") + _adt_rows(inputs, "order")
    actual = _rows(inputs, "actual_cost_items") + _adt_rows(inputs, "actual_cost")
    costing = _adt_rows(inputs, "standard_cost")
    complete, missing = _required_topics(inputs, "order", "actual_cost", "standard_cost")
    actual_total = _sum_amount(actual)
    actual_available = bool(actual)
    periodic_prices = [
        _decimal(row.get("PVPRS"))
        for row in costing
        if row.get("PVPRS") not in {None, ""}
    ]
    standard_prices = [
        _decimal(row.get("STPRS"))
        for row in costing
        if row.get("STPRS") not in {None, ""}
    ]
    periodic = sum(periodic_prices, Decimal(0)) if periodic_prices else None
    standard = sum(standard_prices, Decimal(0)) if standard_prices else None
    price_variance = periodic - standard if periodic is not None and standard is not None else None
    findings: list[JsonObject] = []
    if not orders:
        findings.append({"code": "PRODUCTION_ORDER_NOT_CONFIRMED", "severity": "high"})
    if price_variance is None:
        findings.append({"code": "STANDARD_PERIODIC_PRICE_NOT_COMPARABLE", "severity": "high"})
    elif price_variance != 0:
        findings.append({"code": "PRODUCT_COST_VARIANCE", "severity": "medium", "value": str(price_variance)})
    return _result(
        inputs,
        business_status=(
            "capability_blocked"
            if not complete or not orders or standard is None or periodic is None
            else "attention" if findings else "normal"
        ),
        headline_zh=(f"周期价格与标准价格差异为 {price_variance}" if price_variance is not None else "产品成本差异证据不可比较"),
        headline_en=(f"Periodic-to-standard price variance is {price_variance}" if price_variance is not None else "Product-cost variance evidence is not comparable"),
        overview_zh="产品成本结论区分订单实际发生额与物料分类账单位价格；两者不在单位和归属完整前混合汇总。",
        overview_en="Product-cost conclusions keep order actual amounts separate from Material Ledger unit prices until units and attribution are complete.",
        stages=[
            _stage("order", "生产订单", "Production order", len(orders), state="confirmed" if _topic_complete(inputs, "order") else "unknown"),
            _stage("actual", "订单实际成本", "Order actual cost", len(actual), state="confirmed" if _topic_complete(inputs, "actual_cost") else "unknown"),
            _stage("standard", "标准与周期成本", "Standard and periodic cost", len(costing), state="confirmed" if _topic_complete(inputs, "standard_cost") else "unknown"),
        ],
        findings=findings,
        metrics=[
            {"id": "order_actual_amount", "value": str(actual_total) if actual_available else None},
            {"id": "standard_unit_price", "value": str(standard) if standard is not None else None},
            {"id": "periodic_unit_price", "value": str(periodic) if periodic is not None else None},
            {"id": "unit_price_variance", "value": str(price_variance) if price_variance is not None else None},
        ],
        gaps=_gaps(inputs, *missing),
        limitations=["standard_cost_evidence"] if standard is None or periodic is None else [],
        actions_zh=["由产品成本会计复核物料分类账期间、价格单位、订单归属和结算状态。"],
        actions_en=["Have product-cost accounting review the Material Ledger period, price unit, order attribution, and settlement status."],
        source_complete_override=(
            _topic_complete(inputs, "order") and _topic_complete(inputs, "actual_cost")
        ),
    )


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


def _sum_period_fields(rows: list[JsonObject]) -> Decimal:
    total = Decimal(0)
    for row in rows:
        period_fields = [key for key in row if key.startswith(("WTG", "WKG")) and key[3:].isdigit()]
        if period_fields:
            total += sum((_decimal(row.get(key)) for key in period_fields), Decimal(0))
        else:
            total += _amount(row)
    return total


def _internal_order_project_control(inputs: JsonObject) -> JsonObject:
    actual = _rows(inputs, "order_actual", "wbs_actual") + _adt_rows(inputs, "actual")
    plan = _rows(inputs, "order_plan", "wbs_plan") + _adt_rows(inputs, "plan")
    masters = _adt_rows(inputs, "master")
    budgets = _adt_rows(inputs, "budget")
    commitments = _adt_rows(inputs, "commitment")
    complete, missing = _required_topics(inputs, "actual", "plan", "master", "budget", "commitment")
    actual_total = _sum_amount(actual)
    plan_total = _sum_amount(plan)
    budget_total = _sum_period_fields(budgets)
    commitment_total = _sum_period_fields(commitments)
    plan_available = bool(plan)
    budget_available = bool(budgets)
    commitment_available = bool(commitments)
    eac = actual_total + commitment_total if commitment_available else None
    variance = budget_total - eac if budget_available and eac is not None else None
    currencies = _currency_set(actual + plan + budgets + commitments)
    comparable = complete and budget_available and eac is not None and len(currencies) <= 1 and budget_total != 0
    consumption_pct = eac / abs(budget_total) * Decimal(100) if comparable and eac is not None else None
    findings: list[JsonObject] = []
    business_gaps: list[str] = []
    if not masters:
        findings.append({"code": "CONTROL_OBJECT_NOT_CONFIRMED", "severity": "high"})
        business_gaps.append("master_evidence")
        business_gaps.append("control_object_not_found")
    if len(currencies) > 1:
        findings.append({"code": "CURRENCY_NOT_COMPARABLE", "severity": "high"})
    if not plan_available:
        findings.append({"code": "PLAN_EVIDENCE_MISSING", "severity": "high"})
        business_gaps.append("plan_evidence")
    if not commitment_available:
        findings.append({"code": "COMMITMENT_EVIDENCE_MISSING", "severity": "high"})
        business_gaps.append("commitment_evidence")
    if not budget_available:
        findings.append({"code": "BUDGET_EVIDENCE_MISSING", "severity": "high"})
        business_gaps.append("budget_evidence")
    elif budget_total == 0:
        findings.append({"code": "BUDGET_MISSING_OR_ZERO", "severity": "high"})
    if comparable and eac is not None and eac > budget_total:
        findings.append({"code": "EAC_EXCEEDS_BUDGET", "severity": "high", "variance": str(-variance)})
    return _result(
        inputs,
        business_status="capability_blocked" if not complete else ("attention" if findings else "normal"),
        headline_zh=(f"预计完工成本占预算 {consumption_pct.quantize(Decimal('0.01'))}%" if consumption_pct is not None else "订单/项目预算控制证据不可比较"),
        headline_en=(f"Estimate at completion consumes {consumption_pct.quantize(Decimal('0.01'))}% of budget" if consumption_pct is not None else "Order/project budget-control evidence is not comparable"),
        overview_zh="EAC 仅按实际加承诺计算；计划、预算、币种或对象归属不完整时不输出确定超预算结论。",
        overview_en="EAC is calculated only as actual plus commitments; no confirmed over-budget conclusion is emitted when plan, budget, currency, or object attribution is incomplete.",
        stages=[
            _stage("master", "订单/WBS 主数据", "Order/WBS master", len(masters), state="confirmed" if _topic_complete(inputs, "master") else "unknown"),
            _stage("actual", "实际成本", "Actual cost", len(actual), state="confirmed" if _topic_complete(inputs, "actual") else "unknown"),
            _stage("plan", "计划成本", "Planned cost", len(plan), state="confirmed" if _topic_complete(inputs, "plan") else "unknown"),
            _stage("budget", "预算", "Budget", len(budgets), state="confirmed" if _topic_complete(inputs, "budget") else "unknown"),
            _stage("commitment", "承诺", "Commitments", len(commitments), state="confirmed" if _topic_complete(inputs, "commitment") else "unknown"),
        ],
        findings=findings,
        metrics=[
            {"id": "actual_amount", "value": str(actual_total)},
            {"id": "plan_amount", "value": str(plan_total) if plan_available else None},
            {"id": "budget_amount", "value": str(budget_total) if budget_available else None},
            {"id": "commitment_amount", "value": str(commitment_total) if commitment_available else None},
            {"id": "estimate_at_completion", "value": str(eac) if eac is not None else None},
            {"id": "remaining_budget", "value": str(variance) if variance is not None else None},
            {"id": "budget_consumption_pct", "value": str(consumption_pct.quantize(Decimal("0.01"))) if consumption_pct is not None else None},
        ],
        gaps=_gaps(inputs, *missing, *business_gaps),
        actions_zh=["由内部订单或项目负责人复核承诺、未过账成本、预算补充和结算计划。"],
        actions_en=["Have the internal-order or project owner review commitments, unposted cost, budget supplements, and settlement plans."],
        source_complete_override=complete,
    )


_EVALUATORS: dict[str, Callable[[JsonObject], JsonObject]] = {
    "ap-payment": _ap_payment,
    "ar-collection": _ar_collection,
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
