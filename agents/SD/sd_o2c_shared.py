"""Shared, read-only runtime contract for SAP SD Order-to-Cash agents."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Sequence


JsonObject = dict[str, Any]
Analyzer = Callable[[list[JsonObject], date], tuple[str, int | None, list[JsonObject], list[str], list[str], list[JsonObject]]]


class EvidenceError(ValueError):
    """Raised when fixture or live evidence violates the read-only contract."""


@dataclass(frozen=True)
class AgentSpec:
    slug: str
    command: str
    title: str
    analyzer: Analyzer


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError) as exc:
        raise EvidenceError(f"invalid numeric value: {value!r}") from exc


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise EvidenceError(f"invalid ISO date: {value!r}") from exc


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "x", "complete", "completed", "c"}


def _complete(value: Any) -> bool:
    return str(value or "").strip().lower() in {"c", "complete", "completed", "fully_billed", "paid", "sent", "success"}


def _finding(code: str, severity: str, message: str, ref: str = "") -> JsonObject:
    result: JsonObject = {"code": code, "severity": severity, "message": message}
    if ref:
        result["evidence_ref"] = ref
    return result


def _identifier(row: JsonObject, index: int) -> str:
    for name in ("id", "sales_order", "delivery", "billing_document", "customer_return", "credit_memo_request"):
        if row.get(name):
            return str(row[name])
    return f"record-{index + 1}"


def _delivered_not_billed(records: list[JsonObject], as_of: date):
    findings: list[JsonObject] = []
    for index, row in enumerate(records):
        if not _truthy(row.get("goods_movement_complete")) or _complete(row.get("billing_status")):
            continue
        movement_date = _date(row.get("actual_goods_movement_date"))
        age = max((as_of - movement_date).days, 0) if movement_date else None
        severity = "high" if age is not None and age > 7 else "medium"
        ref = _identifier(row, index)
        findings.append(_finding("DELIVERED_NOT_BILLED", severity, f"{ref} 已发货但未完全开票" + (f"，已滞留 {age} 天" if age is not None else ""), ref))
    return ("attention" if findings else "complete", None, findings, [], ["复核开票到期清单并解除业务冻结"] if findings else [], [])


def _billing_block(records: list[JsonObject], as_of: date):
    del as_of
    findings: list[JsonObject] = []
    mapping = {
        "header_billing_block": "订单抬头开票冻结",
        "item_billing_block": "订单项目开票冻结",
        "delivery_billing_block": "交货开票冻结",
        "delivery_block": "交货冻结",
        "credit_status": "信用检查",
        "incompletion_status": "不完整状态",
    }
    for index, row in enumerate(records):
        ref = _identifier(row, index)
        for field, label in mapping.items():
            value = str(row.get(field) or "").strip()
            if value and value.lower() not in {"none", "ok", "complete", "c"}:
                findings.append(_finding(field.upper(), "high" if "block" in field else "medium", f"{ref}: {label}={value}", ref))
    actions = ["按冻结层级分派至SD、信用或主数据责任团队"] if findings else []
    return ("blocked" if any(x["severity"] == "high" for x in findings) else "attention" if findings else "complete", None, findings, [], actions, [])


def _billing_completeness(records: list[JsonObject], as_of: date):
    del as_of
    findings: list[JsonObject] = []
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(records):
        ref = _identifier(row, index)
        key = (str(row.get("reference_document") or ""), str(row.get("billing_document") or ""))
        if key in seen and all(key):
            findings.append(_finding("DUPLICATE_BILLING", "high", f"{ref}: 检测到重复开票引用", ref))
        seen.add(key)
        if _decimal(row.get("billed_quantity")) > _decimal(row.get("delivered_quantity")):
            findings.append(_finding("OVERBILLED_QUANTITY", "high", f"{ref}: 开票数量超过交货数量", ref))
        if row.get("expected_currency") and row.get("billing_currency") != row.get("expected_currency"):
            findings.append(_finding("CURRENCY_MISMATCH", "high", f"{ref}: 开票币种不一致", ref))
        if _decimal(row.get("expected_net_amount")) != _decimal(row.get("billing_net_amount")):
            findings.append(_finding("NET_AMOUNT_MISMATCH", "medium", f"{ref}: 净额与来源凭证不一致", ref))
        if _decimal(row.get("tax_amount")) != 0 and not row.get("tax_code"):
            findings.append(_finding("MISSING_TAX_CODE", "high", f"{ref}: 存在税额但缺少税码", ref))
        if _truthy(row.get("cancelled")):
            findings.append(_finding("CANCELLED_BILLING", "medium", f"{ref}: 开票凭证已取消", ref))
    return ("attention" if findings else "complete", None, findings, [], ["复核数量、价格、币种与税务条件"] if findings else [], [])


def _billing_output(records: list[JsonObject], as_of: date):
    del as_of
    if not records or all(not row.get("output_status") for row in records):
        return ("blocked", None, [], ["缺少Output Management、VF31或SOST只读证据"], ["通过SAPSkillhub补充发票输出状态取证"], [])
    findings = []
    for index, row in enumerate(records):
        status = str(row.get("output_status") or "unknown").lower()
        if status not in {"sent", "success", "completed"}:
            ref = _identifier(row, index)
            findings.append(_finding("OUTPUT_NOT_SENT", "high" if status == "failed" else "medium", f"{ref}: 发票输出状态={status}", ref))
    return ("attention" if findings else "complete", None, findings, [], ["重处理失败输出并验证客户接收"] if findings else [], [])


def _delivery_delay(records: list[JsonObject], as_of: date):
    findings: list[JsonObject] = []
    components: list[JsonObject] = []
    max_score = 0
    for index, row in enumerate(records):
        score = 0
        due = _date(row.get("requested_delivery_date") or row.get("planned_goods_issue_date"))
        completed = _truthy(row.get("goods_movement_complete"))
        detail: list[str] = []
        if due and due < as_of and not completed:
            score += 40; detail.append("已逾期 +40")
        elif due and 0 <= (due - as_of).days <= 2 and not completed:
            score += 20; detail.append("两日内到期且未完成 +20")
        if row.get("business_block"): score += 20; detail.append("业务冻结 +20")
        if str(row.get("credit_status") or "").lower() not in {"", "ok", "complete", "c"}: score += 10; detail.append("信用风险 +10")
        if row.get("incompletion_status"): score += 10; detail.append("不完整状态 +10")
        score = min(score, 100); max_score = max(max_score, score)
        ref = _identifier(row, index)
        components.append({"evidence_ref": ref, "score": score, "components": detail})
        if score:
            findings.append(_finding("DELIVERY_DELAY_RISK", "high" if score >= 60 else "medium", f"{ref}: 延期风险分 {score}", ref))
    return ("attention" if findings else "complete", max_score, findings, [], ["优先处理高分交货并复核承诺日期"] if findings else [], components)


def _due_priority(records: list[JsonObject], as_of: date):
    ranked: list[JsonObject] = []
    for index, row in enumerate(records):
        due = _date(row.get("requested_delivery_date"))
        overdue = max((as_of - due).days, 0) if due else 0
        overdue_score = min(overdue / 30, 1) * 35
        priority = max(min(int(row.get("delivery_priority") or 5), 9), 1)
        priority_score = (10 - priority) / 9 * 25
        proximity = 20 if due and (due - as_of).days <= 2 else 0
        releasable = 10 if row.get("block_releasable") else 0
        coverage = min(max(float(row.get("stock_coverage") or 0), 0), 1) * 10
        score = round(overdue_score + priority_score + proximity + releasable + coverage, 2)
        ranked.append({"evidence_ref": _identifier(row, index), "score": score, "requested_delivery_date": str(due or ""), "sales_order": str(row.get("sales_order") or ""), "item": str(row.get("item") or "")})
    ranked.sort(key=lambda x: (-x["score"], x["requested_delivery_date"], x["sales_order"], x["item"]))
    findings = [_finding("DELIVERY_PRIORITY", "medium", f"{row['evidence_ref']}: 优先级分 {row['score']}", row["evidence_ref"]) for row in ranked]
    return ("attention" if ranked else "unknown", int(ranked[0]["score"]) if ranked else None, findings, [] if ranked else ["没有可排序的到期交货需求"], ["按排名处理到期交货清单"] if ranked else [], ranked)


def _shortage_allocation(records: list[JsonObject], as_of: date):
    del as_of
    ordered = sorted(records, key=lambda r: (int(r.get("delivery_priority") or 9), str(r.get("requested_delivery_date") or ""), str(r.get("sales_order") or ""), str(r.get("item") or "")))
    available: dict[tuple[str, str], Decimal] = {}
    allocations: list[JsonObject] = []
    findings: list[JsonObject] = []
    for index, row in enumerate(ordered):
        key = (str(row.get("material") or ""), str(row.get("plant") or ""))
        available.setdefault(key, _decimal(row.get("available_quantity")))
        requested = max(_decimal(row.get("requested_quantity")) - _decimal(row.get("confirmed_quantity")), Decimal(0))
        allocated = min(requested, available[key]); available[key] -= allocated
        ref = _identifier(row, index)
        allocations.append({"evidence_ref": ref, "material": key[0], "plant": key[1], "requested_shortage": str(requested), "recommended_allocation": str(allocated), "remaining_available": str(available[key])})
        if allocated < requested:
            findings.append(_finding("UNALLOCATED_SHORTAGE", "high", f"{ref}: 仍有 {requested - allocated} 未分配", ref))
    return ("attention" if findings else "complete", None, findings, [], ["人工确认分配建议；本Agent不会回写ATP或确认数量"], allocations)


DISPUTE_RULES = [
    ("price", ("price", "pricing", "价格", "单价")),
    ("quantity", ("quantity", "short", "数量", "短装")),
    ("tax", ("tax", "vat", "税", "发票税")),
    ("pod", ("pod", "proof of delivery", "签收", "送货证明")),
    ("duplicate", ("duplicate", "重复")),
    ("damage", ("damage", "broken", "货损", "破损")),
    ("output_format", ("format", "edi", "格式", "未收到")),
    ("payment_terms", ("payment term", "due date", "付款条件", "到期日")),
]


def _billing_dispute(records: list[JsonObject], as_of: date):
    del as_of
    if not records or all(not str(row.get("text") or "").strip() for row in records):
        return ("blocked", None, [], ["缺少客户拒票或争议文本证据"], ["通过SAPSkillhub补充只读争议案件/沟通文本"], [])
    classified: list[JsonObject] = []
    findings: list[JsonObject] = []
    for index, row in enumerate(records):
        text = str(row.get("text") or "").lower()
        category = "insufficient_evidence"
        for candidate, keywords in DISPUTE_RULES:
            if any(keyword in text for keyword in keywords):
                category = candidate; break
        ref = _identifier(row, index)
        classified.append({"evidence_ref": ref, "category": category})
        findings.append(_finding("DISPUTE_CLASSIFIED", "medium", f"{ref}: 争议分类={category}", ref))
    return ("attention", None, findings, [], ["按分类路由责任团队并补齐争议证据"], classified)


def _returns_credit(records: list[JsonObject], as_of: date):
    del as_of
    findings: list[JsonObject] = []
    seen: set[tuple[str, str, str]] = set()
    for index, row in enumerate(records):
        ref = _identifier(row, index)
        key = (str(row.get("reference_document") or ""), str(row.get("customer") or ""), str(row.get("credit_amount") or ""))
        if key in seen and all(key): findings.append(_finding("DUPLICATE_CLAIM", "high", f"{ref}: 疑似重复退货/贷项", ref))
        seen.add(key)
        if _decimal(row.get("return_quantity")) > _decimal(row.get("original_quantity")): findings.append(_finding("RETURN_EXCEEDS_ORIGINAL", "high", f"{ref}: 退货数量超过原单", ref))
        if _decimal(row.get("credit_amount")) > _decimal(row.get("original_amount")): findings.append(_finding("CREDIT_EXCEEDS_ORIGINAL", "high", f"{ref}: 贷项金额超过原单", ref))
        if _truthy(row.get("refund_issued")) and not _truthy(row.get("return_received")): findings.append(_finding("REFUND_BEFORE_RECEIPT", "medium", f"{ref}: 未收退货已退款", ref))
        if not row.get("reference_document"): findings.append(_finding("MISSING_REFERENCE", "medium", f"{ref}: 缺少原始销售凭证引用", ref))
    return ("attention" if findings else "complete", None, findings, [], ["复核异常退货、贷项及审批证据"] if findings else [], [])


def _o2c_anomaly(records: list[JsonObject], as_of: date):
    del as_of
    rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    ordered = sorted(records, key=lambda r: (-rank.get(str(r.get("severity") or "low").lower(), 0), -float(r.get("amount_impact") or 0), -int(r.get("age_days") or 0), str(r.get("id") or "")))
    findings = [_finding(str(r.get("code") or "O2C_ANOMALY"), str(r.get("severity") or "medium"), str(r.get("message") or "O2C流程异常"), _identifier(r, i)) for i, r in enumerate(ordered)]
    completeness = sum(float(r.get("evidence_completeness") or 0) for r in ordered) / len(ordered) if ordered else 1
    blockers = ["聚合证据完整度低于80%"] if completeness < 0.8 else []
    return ("attention" if findings else "complete", None, findings, blockers, ["按严重度、金额和老化天数处理统一待办"] if findings else [], ordered)


def _o2c_status(records: list[JsonObject], as_of: date):
    del as_of
    if not records:
        return ("unknown", None, [], ["未找到订单到现金凭证链"], ["确认销售订单、客户PO、交货或发票标识"], [])
    row = records[0]
    stages = [
        ("sales_order", "订单"), ("delivery", "交货"), ("goods_issue", "PGI"),
        ("billing_document", "开票"), ("accounting_document", "FI"), ("clearing_document", "清账/回款"),
    ]
    timeline = [{"stage": label, "complete": bool(row.get(field)), "evidence_ref": str(row.get(field) or "")} for field, label in stages]
    blockers = [str(x) for x in row.get("blockers", []) if str(x)]
    last_complete = next((item["stage"] for item in reversed(timeline) if item["complete"]), "未开始")
    findings = [_finding("O2C_CURRENT_STAGE", "info", f"当前已完成至：{last_complete}")]
    status = "blocked" if blockers else "complete" if all(item["complete"] for item in timeline) else "attention"
    actions = ["处理阻塞后重新追踪凭证链"] if blockers else ["继续跟踪下一业务阶段"] if status == "attention" else []
    return status, None, findings, blockers, actions, timeline


SPECS: dict[str, AgentSpec] = {
    "delivered-not-billed": AgentSpec("delivered-not-billed", "delivered-not-billed", "Delivered-not-Billed Monitor", _delivered_not_billed),
    "billing-block-diagnosis": AgentSpec("billing-block-diagnosis", "billing-block-diagnosis", "Billing Block Diagnosis", _billing_block),
    "billing-completeness-check": AgentSpec("billing-completeness-check", "billing-completeness-check", "Billing Completeness Check", _billing_completeness),
    "billing-output-monitor": AgentSpec("billing-output-monitor", "billing-output-monitor", "Billing Output Monitor", _billing_output),
    "delivery-delay-prediction": AgentSpec("delivery-delay-prediction", "delivery-delay-prediction", "Delivery Delay Prediction", _delivery_delay),
    "due-delivery-prioritization": AgentSpec("due-delivery-prioritization", "due-delivery-prioritization", "Due Delivery Prioritization", _due_priority),
    "shortage-allocation-advisor": AgentSpec("shortage-allocation-advisor", "shortage-allocation-advisor", "Shortage Allocation Advisor", _shortage_allocation),
    "billing-dispute-classification": AgentSpec("billing-dispute-classification", "billing-dispute-classification", "Billing Dispute Classification", _billing_dispute),
    "returns-credit-anomaly": AgentSpec("returns-credit-anomaly", "returns-credit-anomaly", "Returns and Credit Anomaly Monitor", _returns_credit),
    "order-to-cash-anomaly-monitor": AgentSpec("order-to-cash-anomaly-monitor", "order-to-cash-anomaly-monitor", "Order-to-Cash Anomaly Monitor", _o2c_anomaly),
    "order-to-cash-status": AgentSpec("order-to-cash-status", "order-to-cash-status", "Order-to-Cash Status", _o2c_status),
}


def load_evidence(path: str | Path, *, live: bool = False) -> JsonObject:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read evidence: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise EvidenceError("evidence requires an object with a records array")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise EvidenceError("evidence requires metadata")
    if live and metadata.get("read_only") is not True:
        raise EvidenceError("live evidence must declare read_only=true")
    if metadata.get("schema_version") != "1.0":
        raise EvidenceError("evidence metadata schema_version must be 1.0")
    return payload


def analyze(slug: str, question: str, payload: JsonObject, *, as_of: date) -> JsonObject:
    if slug not in SPECS:
        raise EvidenceError(f"unknown agent slug: {slug}")
    metadata = payload["metadata"]
    records = payload["records"]
    status, score, findings, blockers, actions, details = SPECS[slug].analyzer(records, as_of)
    issues = list(metadata.get("validation_issues") or [])
    if issues and status == "complete":
        status = "attention"
    return {
        "schema_version": "1.0",
        "run_id": str(metadata.get("run_id") or f"{slug}-{datetime.now().strftime('%Y%m%d%H%M%S')}"),
        "as_of": as_of.isoformat(),
        "agent": slug,
        "question": question,
        "scope": payload.get("scope") or {},
        "status": status,
        "score": score,
        "findings": findings,
        "blockers": blockers,
        "recommended_actions": actions,
        "details": details,
        "evidence_refs": [_identifier(row, index) for index, row in enumerate(records)],
        "data_sources": list(metadata.get("data_sources") or [metadata.get("source", "fixture")]),
        "completeness": str(metadata.get("completeness") or "complete"),
        "validation_issues": issues,
        "pagination": metadata.get("pagination") or {"complete": True},
        "read_only": bool(metadata.get("read_only", True)),
    }


def render_markdown(report: JsonObject) -> str:
    lines = [
        f"# {report['agent']}", "", f"- 状态：{report['status']}",
        f"- 基准日：{report['as_of']}", f"- Run ID：{report['run_id']}",
    ]
    if report.get("score") is not None:
        lines.append(f"- 评分：{report['score']}")
    lines.extend(["", "## 发现"])
    lines.extend([f"- [{item['severity']}] {item['message']}" for item in report["findings"]] or ["- 无异常发现"])
    if report["blockers"]:
        lines.extend(["", "## 阻塞"] + [f"- {item}" for item in report["blockers"]])
    if report["recommended_actions"]:
        lines.extend(["", "## 建议动作"] + [f"- {item}" for item in report["recommended_actions"]])
    return "\n".join(lines) + "\n"


def build_parser(slug: str, default_fixture: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=SPECS[slug].command, description=SPECS[slug].title)
    parser.add_argument("question", nargs="+", help="自然语言业务问题")
    parser.add_argument("--source", choices=("fixture", "evidence"), default="fixture")
    parser.add_argument("--fixture", type=Path, default=default_fixture)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def run_cli(slug: str, default_fixture: Path, argv: Sequence[str] | None = None) -> int:
    parser = build_parser(slug, default_fixture)
    args = parser.parse_args(argv)
    if args.source == "evidence" and args.evidence is None:
        parser.error("--source evidence requires --evidence PATH")
    source = args.evidence if args.source == "evidence" else args.fixture
    try:
        payload = load_evidence(source, live=args.source == "evidence")
        report = analyze(slug, " ".join(args.question), payload, as_of=args.as_of)
    except EvidenceError as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.as_json else render_markdown(report), end="" if not args.as_json else "\n")
    return 0


def extract_business_identifiers(question: str) -> JsonObject:
    """Conservative helper used by tests and adapters; ambiguity stays explicit."""
    patterns = {
        "sales_orders": r"(?<!\d)\d{4,10}(?!\d)",
        "customer_po": r"(?:PO|采购订单|客户订单)\s*[:#-]?\s*([A-Za-z0-9-]{4,35})",
    }
    return {name: sorted(set(re.findall(pattern, question, flags=re.IGNORECASE))) for name, pattern in patterns.items()}
