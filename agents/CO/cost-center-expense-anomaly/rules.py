from typing import Any


JsonObject = dict[str, Any]

_BUSINESS_STATUSES = {"normal", "attention", "capability_blocked"}
_DECIMAL_METRICS = (
    "actual_amount",
    "plan_amount",
    "variance_amount",
    "variance_pct",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _decimal_metric(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _object(value: Any) -> JsonObject:
    return value if isinstance(value, dict) else {}


def _objects(value: Any) -> list[JsonObject]:
    return [dict(item) for item in value or [] if isinstance(item, dict)]


def _localized(value: Any, zh: str, en: str) -> JsonObject:
    item = _object(value)
    return {
        "zh": _text(item.get("zh")) or zh,
        "en": _text(item.get("en")) or en,
    }


def _metric_map(evaluation: JsonObject) -> JsonObject:
    values: JsonObject = {}
    for item in evaluation.get("metrics") or []:
        if not isinstance(item, dict):
            continue
        metric_id = _text(item.get("id"))
        if metric_id in {*_DECIMAL_METRICS, "currency"}:
            values[metric_id] = item.get("value")
    return values


def evaluate(inputs: JsonObject) -> JsonObject:
    run_input = _object(inputs.get("run_input"))
    evaluation = _object(inputs.get("evaluation"))
    base_report = _object(evaluation.get("business_report"))
    metric_source = _metric_map(evaluation)

    missing_evidence = sorted(
        {_text(item) for item in evaluation.get("missing_evidence") or [] if _text(item)}
    )
    business_status = _text(evaluation.get("business_status"))
    source_complete = evaluation.get("source_complete") is True
    evidence_complete = (
        source_complete
        and evaluation.get("business_complete") is True
        and not missing_evidence
    )
    business_complete = evidence_complete
    if business_status not in _BUSINESS_STATUSES:
        business_status = "capability_blocked"
        source_complete = False
        evidence_complete = False
        business_complete = False
        missing_evidence = sorted({*missing_evidence, "business_status_unavailable"})

    metric_values = {
        name: _decimal_metric(metric_source.get(name))
        for name in _DECIMAL_METRICS
    }
    if not source_complete:
        metric_values = {name: None for name in _DECIMAL_METRICS}
    currency = _text(metric_source.get("currency")) or None
    if not source_complete:
        currency = None

    record = {
        "company_code": _text(run_input.get("company_code")),
        "controlling_area": _text(run_input.get("controlling_area")),
        "cost_center": _text(run_input.get("cost_center")),
        "fiscal_year": _text(run_input.get("fiscal_year")),
        "period_from": _text(run_input.get("period_from")),
        "period_to": _text(run_input.get("period_to")),
        "business_status": business_status,
        "currency": currency,
    }
    records = [record]

    limitations = sorted(
        {_text(item) for item in base_report.get("limitations") or [] if _text(item)}
    )
    if "plan_evidence_missing" in missing_evidence:
        limitations = sorted({*limitations, "plan_evidence_missing"})

    metrics = [
        {"id": name, "value": metric_values[name]}
        for name in _DECIMAL_METRICS
    ]
    metrics.append({"id": "currency", "value": currency})
    headline = _localized(
        base_report.get("headline"),
        "成本中心费用评估未形成可用标题",
        "Cost-center expense assessment has no available headline",
    )
    overview = _localized(
        base_report.get("overview"),
        "仅展示确定性规则基于完整只读证据形成的结果。",
        "Only results derived by the deterministic rule from complete read-only evidence are shown.",
    )
    summary = _localized(base_report.get("summary"), headline["zh"], headline["en"])
    next_actions = _object(base_report.get("next_actions"))
    report = {
        "tone": _text(base_report.get("tone")) or "info",
        "headline": headline,
        "overview": overview,
        "stages": _objects(base_report.get("stages")),
        "findings": _objects(base_report.get("findings")),
        "metrics": metrics,
        "missing_evidence": missing_evidence,
        "limitations": limitations,
        "next_actions": {
            "zh": [_text(item) for item in next_actions.get("zh") or [] if _text(item)],
            "en": [_text(item) for item in next_actions.get("en") or [] if _text(item)],
        },
        "summary": summary,
        "records": records,
        "record_columns": [
            {"key": "company_code", "label": {"zh": "公司代码", "en": "Company code"}},
            {"key": "controlling_area", "label": {"zh": "控制范围", "en": "Controlling area"}},
            {"key": "cost_center", "label": {"zh": "成本中心", "en": "Cost center"}},
            {"key": "fiscal_year", "label": {"zh": "会计年度", "en": "Fiscal year"}},
            {"key": "period_from", "label": {"zh": "起始期间", "en": "Period from"}},
            {"key": "period_to", "label": {"zh": "结束期间", "en": "Period to"}},
            {"key": "business_status", "label": {"zh": "业务状态", "en": "Business status"}},
            {"key": "currency", "label": {"zh": "币种", "en": "Currency"}},
        ],
    }
    workflow_output = {
        "records": records,
        "business_status": business_status,
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "business_complete": business_complete,
        **metric_values,
        "evidence_gap_codes": missing_evidence,
        "business_report": report,
    }
    return {
        "rule_id": "cost_center_expense_anomaly_acceptance_projection_v1",
        "status": "complete" if business_complete else "inconclusive",
        "business_status": business_status,
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "business_complete": business_complete,
        "missing_evidence": missing_evidence,
        "findings": report["findings"],
        "metrics": metrics,
        "business_report": report,
        "reason": overview,
        "summary": summary,
        "workflow_output": workflow_output,
    }
