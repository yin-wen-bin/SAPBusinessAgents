"""Human-readable report formatting."""

from __future__ import annotations

from .model import AnalysisResult


def render_markdown(result: AnalysisResult) -> str:
    lines = [
        "# GR/IR 账龄分析",
        "",
        f"- 状态：`{result.status.value}`",
        f"- 公司代码：`{result.query.company_code}`",
        f"- 关键日：`{result.query.key_date.isoformat()}`",
        f"- 证据完整：`{str(result.source_complete).lower()}`",
        f"- 只读：`{str(result.read_only).lower()}`",
        "",
    ]
    if result.findings:
        lines.extend(["## 证据问题", ""])
        lines.extend(f"- `{finding.code}`：{finding.message}" for finding in result.findings)
        lines.append("")
    if result.items:
        lines.extend([
            "## 未结项目",
            "",
            "| PO/项目 | 供应商 | GR 值 | IR 值 | 余额 | 币种 | 最后活动 | 账龄 | 区间 |",
            "|---|---|---:|---:|---:|---|---|---:|---|",
        ])
        for item in result.items:
            lines.append(
                f"| {item.purchase_order}/{item.purchase_order_item} | {item.supplier} | "
                f"{item.gr_value} | {item.ir_value} | {item.residual_amount} | "
                f"{item.company_currency} | {item.last_activity_date.isoformat()} | "
                f"{item.ageing_days} | {item.ageing_bucket} |"
            )
    elif result.status.value == "complete":
        lines.append("在给定范围和阈值内没有未结 GR/IR 项目。")
    return "\n".join(lines)
