from __future__ import annotations

import csv
import re
from io import StringIO
from typing import Any

from .models import RunMode, RunResult


_STATUS_PRIORITY = {
    "blocked": 0,
    "inconclusive": 1,
    "attention": 2,
    "in_progress": 3,
    "not_found": 4,
    "complete": 5,
    "normal": 5,
}

def _localized(zh: str, en: str) -> dict[str, str]:
    return {"zh": zh, "en": en}


_GAP_LABELS = {
    "payment_run_evidence_incomplete": _localized(
        "尚未取得付款运行证据", "Payment-run evidence is not available"
    ),
    "bank_master_evidence_incomplete": _localized(
        "尚未完整核验银行主数据", "Bank-master evidence is incomplete"
    ),
    "bank_settlement_not_proven": _localized(
        "尚未单独核验银行实际扣款", "The actual bank debit was not independently verified"
    ),
    "ap_payment_scope_unavailable": _localized(
        "没有可供AP付款准备度复核的证据分组",
        "No AP evidence scope is available for payment-readiness review",
    ),
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value or [] if isinstance(item, dict)]


def _safe_id(value: Any) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(value or "").strip())
    return normalized.strip("-") or "item"


def _display(value: Any, locale: str = "zh") -> str:
    if isinstance(value, dict):
        return str(value.get(locale) or value.get("zh") or value.get("en") or "")
    if isinstance(value, list):
        return "; ".join(_display(item, locale) for item in value)
    if isinstance(value, bool):
        return "是" if value else "否"
    return "" if value is None else str(value)


def _gap_label(code: Any, locale: str = "zh") -> str:
    normalized = str(code or "")
    label = _GAP_LABELS.get(normalized)
    return _display(label, locale) if label else normalized


def _merge_actions(*values: Any) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for value in values:
        for item in value or []:
            marker = repr(item)
            if marker in seen:
                continue
            seen.add(marker)
            merged.append(item)
    return merged


def _node_output(result: RunResult, *, collection: str) -> tuple[dict[str, Any], dict[str, Any]]:
    for node in result.node_results:
        if not isinstance(node, dict):
            continue
        output = _dict(node.get("output"))
        candidate = output.get(collection)
        if isinstance(candidate, list):
            return node, output
    return {}, {}


def _report_table(
    table: dict[str, Any], *, table_id: str, inline_limit: int | None
) -> dict[str, Any] | None:
    columns = _list_of_dicts(table.get("columns"))
    rows = _list_of_dicts(table.get("rows"))
    if not columns:
        return None
    visible_rows = rows if inline_limit is None else rows[:inline_limit]
    return {
        "table_id": table_id,
        "id": str(table.get("id") or table_id),
        "title": _dict(table.get("title")) or _localized(str(table.get("id") or ""), str(table.get("id") or "")),
        "columns": columns,
        "rows": visible_rows,
        "total_rows": len(rows),
        "display_truncated": len(visible_rows) < len(rows),
        "source_complete": table.get("source_complete"),
    }


def _order_view(
    item: dict[str, Any], *, inline_limit: int | None
) -> dict[str, Any]:
    purchase_order = str(item.get("purchase_order") or "").strip()
    report = _dict(item.get("business_report"))
    tables: list[dict[str, Any]] = []
    for index, table in enumerate(_list_of_dicts(report.get("evidence_tables"))):
        normalized = _report_table(
            table,
            table_id=(
                f"order:{_safe_id(purchase_order)}:evidence:"
                f"{_safe_id(table.get('id') or index + 1)}"
            ),
            inline_limit=inline_limit,
        )
        if normalized:
            tables.append(normalized)
    for index, table in enumerate(_list_of_dicts(report.get("action_tables"))):
        normalized = _report_table(
            table,
            table_id=(
                f"order:{_safe_id(purchase_order)}:action:"
                f"{_safe_id(table.get('id') or index + 1)}"
            ),
            inline_limit=inline_limit,
        )
        if normalized:
            tables.append(normalized)
    records = _list_of_dicts(report.get("records"))
    return {
        "purchase_order": purchase_order,
        "company_code": str(item.get("company_code") or ""),
        "supplier": str(item.get("supplier") or ""),
        "business_status": str(item.get("business_status") or "inconclusive"),
        "source_complete": item.get("source_complete") is True,
        "evidence_complete": item.get("evidence_complete") is True,
        "gr_ir_match_status": str(item.get("gr_ir_match_status") or "unknown"),
        "ap_clearing_status": str(item.get("ap_clearing_status") or "unknown"),
        "payment_document_status": str(item.get("payment_document_status") or "unknown"),
        "bank_settlement_status": str(item.get("bank_settlement_status") or "not_assessed"),
        "headline": _dict(report.get("headline")),
        "overview": _dict(report.get("overview")),
        "stages": _list_of_dicts(report.get("stages")),
        "records": records,
        "record_columns": _list_of_dicts(report.get("record_columns")),
        "evidence_tables": tables,
        "ap_scope_refs": [],
        "missing_evidence": [str(value) for value in report.get("missing_evidence") or [] if str(value)],
        "next_actions": report.get("next_actions") or [],
    }


def _scope_view(item: dict[str, Any]) -> dict[str, Any]:
    gaps: list[str] = []
    checks = {
        "payment_run_evidence_complete": "payment_run_evidence_incomplete",
        "bank_master_evidence_complete": "bank_master_evidence_incomplete",
        "bank_settlement_evidence_complete": "bank_settlement_not_proven",
    }
    for field, gap in checks.items():
        if item.get(field) is not True:
            gaps.append(gap)
    return {
        **item,
        "scope_id": str(item.get("scope_id") or ""),
        "company_code": str(item.get("company_code") or ""),
        "supplier": str(item.get("supplier") or ""),
        "purchase_orders": [str(value) for value in item.get("purchase_orders") or []],
        "business_status": str(item.get("business_status") or "inconclusive"),
        "source_complete": item.get("source_complete") is True,
        "evidence_complete": item.get("evidence_complete") is True,
        "payment_run_evidence_complete": item.get("payment_run_evidence_complete") is True,
        "bank_master_evidence_complete": item.get("bank_master_evidence_complete") is True,
        "bank_settlement_evidence_complete": item.get("bank_settlement_evidence_complete") is True,
        "bank_settlement_status": str(item.get("bank_settlement_status") or "not_assessed"),
        "missing_evidence": gaps,
    }


def compose_workflow_presentation(
    result: RunResult, *, inline_limit: int | None = 20
) -> dict[str, Any] | None:
    """Build a deterministic business view from persisted workflow outputs only."""

    if result.mode != RunMode.workflow:
        return None
    p2p_node, p2p_output = _node_output(result, collection="po_results")
    ap_node, ap_output = _node_output(result, collection="scope_results")
    po_items = _list_of_dicts(p2p_output.get("po_results"))
    if not po_items:
        return None

    orders = [_order_view(item, inline_limit=inline_limit) for item in po_items]
    scopes = [_scope_view(item) for item in _list_of_dicts(ap_output.get("scope_results"))]
    scopes_by_order: dict[str, list[str]] = {}
    for scope in scopes:
        for purchase_order in scope["purchase_orders"]:
            scopes_by_order.setdefault(purchase_order, []).append(scope["scope_id"])
    for order in orders:
        order["ap_scope_refs"] = scopes_by_order.get(order["purchase_order"], [])

    orders.sort(
        key=lambda item: (
            _STATUS_PRIORITY.get(str(item.get("business_status")), 1),
            str(item.get("purchase_order") or ""),
        )
    )
    status_counts = {
        status: sum(1 for order in orders if order["business_status"] == status)
        for status in ("blocked", "inconclusive", "attention", "in_progress", "not_found", "complete")
    }
    completed_count = status_counts["complete"] + sum(
        1 for order in orders if order["business_status"] == "normal"
    )
    source_complete = bool(orders) and all(order["source_complete"] for order in orders)
    if "source_complete" in p2p_output:
        source_complete = source_complete and p2p_output.get("source_complete") is True
    if scopes:
        source_complete = source_complete and all(scope["source_complete"] for scope in scopes)
        if "source_complete" in ap_output:
            source_complete = source_complete and ap_output.get("source_complete") is True
    else:
        source_complete = False
    evidence_complete = source_complete and all(order["evidence_complete"] for order in orders)
    if "evidence_complete" in p2p_output:
        evidence_complete = evidence_complete and p2p_output.get("evidence_complete") is True
    if scopes:
        evidence_complete = evidence_complete and all(
            scope["evidence_complete"]
            and scope["payment_run_evidence_complete"]
            and scope["bank_master_evidence_complete"]
            and scope["bank_settlement_evidence_complete"]
            for scope in scopes
        )
    else:
        evidence_complete = False

    missing = {
        str(value)
        for value in (result.completeness.missing_evidence if result.completeness else [])
        if str(value)
    }
    p2p_report = _dict(p2p_output.get("business_report"))
    ap_report = _dict(ap_output.get("business_report"))
    for report in (p2p_report, ap_report):
        missing.update(str(value) for value in report.get("missing_evidence") or [] if str(value))
    for order in orders:
        missing.update(order["missing_evidence"])
    for scope in scopes:
        missing.update(scope["missing_evidence"])
    if not scopes:
        missing.add("ap_payment_scope_unavailable")
    if {
        "payment_run_evidence_incomplete",
        "bank_master_evidence_incomplete",
    }.intersection(missing):
        missing.discard("payment_run_and_bank_master_evidence")

    risky = [
        order
        for order in orders
        if order["business_status"] not in {"complete", "normal"}
        or not order["source_complete"]
        or not order["evidence_complete"]
    ]
    if any(order["business_status"] == "blocked" for order in orders):
        overall_status = "blocked"
    elif not source_complete or not evidence_complete:
        overall_status = "inconclusive"
    elif risky:
        overall_status = "attention"
    else:
        overall_status = "complete"
    tone = "success" if overall_status == "complete" else "warning"

    if len(orders) == 1:
        order = orders[0]
        if order["payment_document_status"] == "confirmed" and not evidence_complete:
            title = _localized(
                "SAP付款链路已确认，仍有付款运行或银行证据未核验",
                "The SAP payment chain is confirmed; payment-run or bank evidence remains unverified",
            )
        else:
            title = order["headline"] or _localized(
                f"采购订单 {order['purchase_order']} 已完成核验",
                f"Purchase order {order['purchase_order']} was reviewed",
            )
    else:
        title = _localized(
            f"已核验 {len(orders)} 张采购订单；{completed_count} 张完成，{len(risky)} 张需要关注或无法确认",
            f"Reviewed {len(orders)} purchase orders; {completed_count} complete and {len(risky)} requiring attention or confirmation",
        )
    overview = _localized(
        "采购订单、收货、发票、GR/IR、应付清账和SAP付款凭证分别核验；付款运行、银行主数据和银行扣款保持独立证据状态。",
        "Purchase orders, receipts, invoices, GR/IR, AP clearing, and SAP payment documents were checked separately; payment-run, bank-master, and bank-debit evidence remain independent.",
    )
    if not scopes:
        overview = _localized(
            "已保留采购订单到付款主链事实；由于没有可复核的AP证据分组，付款准备阶段未执行。",
            "The purchase-order-to-payment facts were retained; payment-readiness review was not executed because no AP evidence scope was available.",
        )

    return {
        "schema_version": "1.0",
        "kind": "p2p_ap",
        "layout": "single" if len(orders) == 1 else "multi",
        "title": title,
        "overview": overview,
        "tone": tone,
        "overall_status": overall_status,
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "summary_metrics": [
            {"id": "orders", "label": _localized("采购订单", "Purchase orders"), "value": len(orders)},
            {"id": "complete", "label": _localized("已完成", "Complete"), "value": completed_count},
            {"id": "attention", "label": _localized("需关注", "Attention"), "value": status_counts["attention"]},
            {"id": "in_progress", "label": _localized("处理中", "In progress"), "value": status_counts["in_progress"]},
            {"id": "not_found", "label": _localized("未找到", "Not found"), "value": status_counts["not_found"]},
            {"id": "inconclusive", "label": _localized("无法确认", "Inconclusive"), "value": status_counts["inconclusive"] + status_counts["blocked"]},
        ],
        "order_results": orders,
        "ap_scope_results": scopes,
        "ap_report": {
            "executed": bool(ap_node) and str(ap_node.get("status")) != "skipped",
            "status": str(ap_node.get("status") or "skipped"),
            "headline": _dict(ap_report.get("headline")),
            "overview": _dict(ap_report.get("overview")),
            "stages": _list_of_dicts(ap_report.get("stages")),
            "missing_evidence": [str(value) for value in ap_report.get("missing_evidence") or [] if str(value)],
            "next_actions": ap_report.get("next_actions") or [],
        },
        "missing_evidence": sorted(missing),
        "next_actions": _merge_actions(
            p2p_report.get("next_actions"), ap_report.get("next_actions")
        ),
        "source_nodes": {
            "p2p": str(p2p_node.get("node_id") or ""),
            "ap": str(ap_node.get("node_id") or ""),
        },
    }


def workflow_presentation_table_page(
    result: RunResult, table_id: str, *, offset: int, limit: int
) -> dict[str, Any]:
    view = compose_workflow_presentation(result, inline_limit=None)
    if not view:
        raise ValueError("Run result has no P2P-to-AP workflow presentation.")
    for order in view["order_results"]:
        for table in order["evidence_tables"]:
            if table["table_id"] != table_id:
                continue
            rows = table["rows"]
            page = rows[offset : offset + limit]
            return {
                "table_id": table_id,
                "offset": offset,
                "limit": limit,
                "rows": page,
                "total_rows": len(rows),
                "has_next": offset + len(page) < len(rows),
            }
    raise ValueError("Workflow presentation table was not found.")


def workflow_orders_csv(view: dict[str, Any]) -> str:
    buffer = StringIO()
    fields = [
        "purchase_order", "company_code", "supplier", "business_status",
        "order_quantity", "net_received_quantity", "net_invoiced_quantity", "unit",
        "gr_ir_match_status", "ap_clearing_status", "payment_document_status",
        "bank_settlement_status", "source_complete", "evidence_complete",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for order in view.get("order_results") or []:
        record = next(iter(order.get("records") or []), {})
        writer.writerow({field: order.get(field, record.get(field, "")) for field in fields})
    return buffer.getvalue()


def workflow_ap_scopes_csv(view: dict[str, Any]) -> str:
    buffer = StringIO()
    fields = [
        "scope_id", "company_code", "supplier", "purchase_orders", "business_status",
        "open_item_count", "payment_blocked_count", "source_complete", "evidence_complete",
        "payment_run_evidence_complete", "bank_master_evidence_complete",
        "bank_settlement_evidence_complete", "bank_settlement_status",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for scope in view.get("ap_scope_results") or []:
        row = {field: scope.get(field, "") for field in fields}
        row["purchase_orders"] = ";".join(scope.get("purchase_orders") or [])
        writer.writerow(row)
    return buffer.getvalue()


def workflow_stages_csv(view: dict[str, Any]) -> str:
    buffer = StringIO()
    fields = ["purchase_order", "stage", "status", "business_explanation"]
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for order in view.get("order_results") or []:
        for stage in order.get("stages") or []:
            label = _dict(stage.get("label"))
            state_label = _dict(stage.get("state_label"))
            detail = _dict(stage.get("detail"))
            writer.writerow(
                {
                    "purchase_order": order.get("purchase_order", ""),
                    "stage": label.get("zh") or label.get("en") or stage.get("id", ""),
                    "status": state_label.get("zh") or state_label.get("en") or stage.get("state", ""),
                    "business_explanation": detail.get("zh") or detail.get("en") or "",
                }
            )
    return buffer.getvalue()


def workflow_markdown_report(view: dict[str, Any]) -> str:
    title = _dict(view.get("title"))
    overview = _dict(view.get("overview"))
    lines = ["# 组合工作流业务结果", "", f"**{title.get('zh') or title.get('en') or ''}**", ""]
    if overview.get("zh") or overview.get("en"):
        lines.extend([overview.get("zh") or overview.get("en") or "", ""])
    lines.extend(
        [
            "## 完整性",
            "",
            f"- 查询完整性：{'完整' if view.get('source_complete') else '不完整或无法确认'}",
            f"- 证据完整性：{'完整' if view.get('evidence_complete') else '存在缺口'}",
            "",
        ]
    )
    for order in view.get("order_results") or []:
        lines.extend([f"## 采购订单 {order.get('purchase_order', '')}", ""])
        headline = _dict(order.get("headline"))
        if headline:
            lines.extend([headline.get("zh") or headline.get("en") or "", ""])
        lines.extend(["### 业务阶段", "", "| 阶段 | 状态 | 说明 |", "| --- | --- | --- |"])
        for stage in order.get("stages") or []:
            label = _dict(stage.get("label"))
            state = _dict(stage.get("state_label"))
            detail = _dict(stage.get("detail"))
            lines.append(
                f"| {label.get('zh') or stage.get('id', '')} | "
                f"{state.get('zh') or stage.get('state', '')} | "
                f"{detail.get('zh') or ''} |"
            )
        lines.append("")
        for table in order.get("evidence_tables") or []:
            table_title = _dict(table.get("title"))
            columns = table.get("columns") or []
            rows = table.get("rows") or []
            lines.extend([f"### {table_title.get('zh') or table_title.get('en') or table.get('id', '')}", ""])
            lines.append("| " + " | ".join((_dict(column.get("label")).get("zh") or column.get("key", "")) for column in columns) + " |")
            lines.append("| " + " | ".join("---" for _ in columns) + " |")
            for row in rows:
                lines.append(
                    "| "
                    + " | ".join(
                        _display(row.get(column.get("key"), ""), "zh").replace("|", "\\|")
                        for column in columns
                    )
                    + " |"
                )
            lines.append("")
    lines.extend(["## AP付款准备度", ""])
    for scope in view.get("ap_scope_results") or []:
        lines.append(
            f"- {scope.get('scope_id', '')}：状态 {scope.get('business_status', '')}；"
            f"未清项 {scope.get('open_item_count', 0)}；付款冻结 {scope.get('payment_blocked_count', 0)}；"
            f"银行扣款 {scope.get('bank_settlement_status', 'not_assessed')}"
        )
    if view.get("missing_evidence"):
        lines.extend(["", "## 尚未取得的证据", ""])
        lines.extend(f"- {_gap_label(gap, 'zh')} (`{gap}`)" for gap in view["missing_evidence"])
    return "\n".join(lines).rstrip() + "\n"
