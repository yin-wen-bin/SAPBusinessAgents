"""Human-readable, item-level report rendering."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from .model import ItemStatusResult, P2PReport


def _number(value: Decimal) -> str:
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _docs(item: ItemStatusResult) -> str:
    groups = (
        ("GR", item.documents.material_documents),
        ("IV", item.documents.invoice_documents),
        ("FI", item.documents.accounting_documents),
        ("PAY", item.documents.clearing_documents),
    )
    rendered = [f"{label}: {', '.join(values)}" for label, values in groups if values]
    return "；".join(rendered) if rendered else "无后续凭证"


def render_markdown(report: P2PReport) -> str:
    """Render a concise overview followed by evidence for every PO item."""

    counts = Counter(item.status_label for item in report.items)
    summary = "，".join(f"{label} {count} 项" for label, count in counts.items())
    lines = [
        f"采购订单 **{report.po_number}**：{summary}。",
        f"公司代码 `{report.company_code or '-'}`；供应商 `{report.vendor or '-'}`；截至 {report.as_of.isoformat()}。",
        "",
        "| 项目 | 物料 / 描述 | 订单数量 | 净收货 | 已过账发票数量 | 发票金额 | 已付款 | 未清 | 状态 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in report.items:
        material = item.material or "-"
        description = item.description.replace("|", "\\|") or "-"
        lines.append(
            "| {item} | {material} / {description} | {ordered} {unit} | {received} {unit} | "
            "{invoiced} {unit} | {invoice_amount} {currency} | {paid} {currency} | "
            "{open_amount} {currency} | **{status}** |".format(
                item=item.item_number,
                material=material,
                description=description,
                ordered=_number(item.ordered_quantity),
                received=_number(item.received_quantity),
                invoiced=_number(item.invoiced_quantity),
                unit=item.unit,
                invoice_amount=_number(item.invoiced_amount),
                paid=_number(item.paid_amount),
                open_amount=_number(item.open_amount),
                currency=item.currency,
                status=item.status_label,
            )
        )

    lines.extend(["", "逐项目判断："])
    for item in report.items:
        lines.extend(
            [
                "",
                f"- **{item.item_number} · {item.status_label}**：{item.explanation}",
                f"  - 凭证链：{_docs(item)}",
            ]
        )
        if item.findings:
            for finding in item.findings:
                evidence = f"（证据：{', '.join(finding.evidence)}）" if finding.evidence else ""
                lines.append(f"  - [{finding.severity}] {finding.message}{evidence}")
        else:
            lines.append("  - 未发现额外阻塞或数据异常。")
    for warning in report.warnings:
        lines.extend(["", f"> 数据警告：{warning}"])
    return "\n".join(lines)

