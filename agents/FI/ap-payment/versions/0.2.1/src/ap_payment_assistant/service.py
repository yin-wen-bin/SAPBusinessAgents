"""AP Payment Assistant orchestration and structured answer construction."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Sequence

from .adapter import PayablesFilter, SapApDataAdapter
from .intent import ApIntentParser
from .models import (
    AssistantResponse,
    Intent,
    ParsedQuery,
    PayableItem,
    QueryParameters,
    RiskFinding,
    Severity,
    to_primitive,
)
from .risk import PaymentRiskEngine


class ApPaymentAssistant:
    def __init__(
        self,
        adapter: SapApDataAdapter,
        *,
        parser: ApIntentParser | None = None,
        risk_engine: PaymentRiskEngine | None = None,
    ) -> None:
        self.adapter = adapter
        self.parser = parser or ApIntentParser()
        self.risk_engine = risk_engine or PaymentRiskEngine()

    def ask(self, query: str, *, as_of: date | None = None) -> AssistantResponse:
        parsed = self.parser.parse(query, as_of=as_of)
        parsed = self._apply_defaults(parsed)
        errors = self._validate(parsed)
        if errors:
            return AssistantResponse(
                ok=False,
                query=parsed.text,
                intent=parsed.intent,
                parameters=parsed.parameters,
                summary={"matched_items": 0, "risk_count": 0},
                answer="无法执行查询：" + "；".join(errors),
                errors=tuple(errors),
                trace=self._trace(parsed, (), ()),
            )

        parameters = parsed.parameters
        selected = tuple(self.adapter.search_payables(self._filters(parsed)))
        vendor_id = parameters.vendor_id or (selected[0].vendor_id if selected else None)
        profile = self.adapter.get_vendor_profile(vendor_id) if vendor_id else None
        bank_accounts = self.adapter.get_vendor_bank_accounts(vendor_id) if vendor_id else ()

        context = selected
        if vendor_id:
            context = tuple(
                self.adapter.search_payables(
                    PayablesFilter(
                        vendor_id=vendor_id,
                        company_code=parameters.company_code,
                        include_cleared=False,
                    )
                )
            )
        risks = self.risk_engine.evaluate(
            items=selected,
            context_items=context,
            profile=profile,
            bank_accounts=bank_accounts,
            as_of=parameters.as_of,
        )
        item_rows = tuple(self._item_row(item, parameters.as_of) for item in selected)
        summary = self._summary(selected, risks, parameters.as_of)
        return AssistantResponse(
            ok=True,
            query=parsed.text,
            intent=parsed.intent,
            parameters=parameters,
            summary=summary,
            items=item_rows,
            risks=risks,
            answer=self._answer(parsed, selected, risks, profile.name if profile else None),
            trace=self._trace(parsed, selected, risks),
        )

    @staticmethod
    def _apply_defaults(parsed: ParsedQuery) -> ParsedQuery:
        parameters = parsed.parameters
        notes = list(parsed.extraction_notes)
        if parsed.intent == Intent.UPCOMING_DUE and not parameters.due_from:
            parameters = replace(
                parameters,
                due_from=parameters.as_of,
                due_to=parameters.as_of + timedelta(days=30),
            )
            notes.append("未指定到期范围，默认未来30天")
        return replace(parsed, parameters=parameters, extraction_notes=tuple(notes))

    @staticmethod
    def _validate(parsed: ParsedQuery) -> list[str]:
        if not parsed.text:
            return ["查询文本不能为空"]
        if parsed.intent == Intent.UNKNOWN:
            return ["无法识别查询意图，请说明供应商、发票或付款风险"]
        if parsed.intent == Intent.INVOICE_STATUS:
            if not (
                parsed.parameters.invoice_reference or parsed.parameters.accounting_document
            ):
                return ["发票状态查询需要发票参考号或会计凭证号"]
            return []
        if not parsed.parameters.vendor_id:
            return ["该查询需要供应商编号"]
        return []

    @staticmethod
    def _filters(parsed: ParsedQuery) -> PayablesFilter:
        parameters = parsed.parameters
        use_due_window = parsed.intent in (Intent.UPCOMING_DUE, Intent.PAYMENT_RISK)
        return PayablesFilter(
            vendor_id=parameters.vendor_id,
            company_code=parameters.company_code,
            invoice_reference=parameters.invoice_reference,
            accounting_document=parameters.accounting_document,
            fiscal_year=parameters.fiscal_year,
            due_from=parameters.due_from if use_due_window else None,
            due_to=parameters.due_to if use_due_window else None,
            include_cleared=parsed.intent == Intent.INVOICE_STATUS,
        )

    @staticmethod
    def _item_row(item: PayableItem, as_of: date) -> dict[str, object]:
        return to_primitive(
            {
                "vendor_id": item.vendor_id,
                "company_code": item.company_code,
                "accounting_document": item.accounting_document,
                "fiscal_year": item.fiscal_year,
                "line_item": item.line_item,
                "invoice_reference": item.invoice_reference,
                "invoice_date": item.invoice_date,
                "due_date": item.due_date,
                "amount": item.amount,
                "currency": item.currency,
                "status": item.payment_status(as_of),
                "payment_block": item.payment_block,
                "clearing_document": item.clearing_document,
                "clearing_date": item.clearing_date,
                "payment_run_id": item.payment_run_id,
                "purchase_order": item.purchase_order,
                "source_objects": item.source_objects,
            }
        )

    @staticmethod
    def _summary(
        items: Sequence[PayableItem], risks: Sequence[RiskFinding], as_of: date
    ) -> dict[str, object]:
        amounts: dict[str, Decimal] = defaultdict(Decimal)
        statuses: Counter[str] = Counter()
        for item in items:
            amounts[item.currency] += item.amount
            statuses[item.payment_status(as_of)] += 1
        risk_counts = Counter(risk.severity.value for risk in risks)
        highest = next(
            (level for level in ("high", "medium", "low") if risk_counts.get(level)), None
        )
        return to_primitive(
            {
                "matched_items": len(items),
                "amount_by_currency": dict(sorted(amounts.items())),
                "status_counts": dict(sorted(statuses.items())),
                "risk_count": len(risks),
                "risk_by_severity": dict(sorted(risk_counts.items())),
                "highest_risk": highest,
            }
        )

    def _trace(
        self,
        parsed: ParsedQuery,
        items: Sequence[PayableItem],
        risks: Sequence[RiskFinding],
    ) -> dict[str, object]:
        source_objects = sorted({source for item in items for source in item.source_objects})
        return {
            "adapter": self.adapter.health(),
            "intent_confidence": parsed.confidence,
            "extraction_notes": list(parsed.extraction_notes),
            "source_objects": source_objects,
            "matched_records": len(items),
            "evaluated_findings": len(risks),
        }

    @staticmethod
    def _answer(
        parsed: ParsedQuery,
        items: Sequence[PayableItem],
        risks: Sequence[RiskFinding],
        vendor_name: str | None,
    ) -> str:
        parameters = parsed.parameters
        vendor_label = parameters.vendor_id or (items[0].vendor_id if items else "未知供应商")
        if vendor_name:
            vendor_label = f"{vendor_name}（{vendor_label}）"
        if not items:
            return f"未找到 {vendor_label} 符合条件的应付项目。"

        if parsed.intent == Intent.INVOICE_STATUS and len(items) == 1:
            item = items[0]
            status_label = {
                "paid": "已付款并清账",
                "blocked": "仍未付款，且存在付款冻结",
                "overdue": "逾期未付款",
                "scheduled": "已进入付款运行，尚未清账",
                "open": "未付款",
            }[item.payment_status(parameters.as_of)]
            return (
                f"发票 {item.invoice_reference} {status_label}；金额 {item.amount} {item.currency}，"
                f"到期日 {item.due_date}。发现 {len(risks)} 项相关风险。"
            )

        totals: dict[str, Decimal] = defaultdict(Decimal)
        for item in items:
            totals[item.currency] += item.amount
        amount_text = "、".join(f"{amount} {currency}" for currency, amount in sorted(totals.items()))
        window = ""
        if parameters.due_from and parameters.due_to:
            window = f"，到期范围 {parameters.due_from} 至 {parameters.due_to}"
        high_count = sum(risk.severity == Severity.HIGH for risk in risks)
        return (
            f"{vendor_label}{window}，共有 {len(items)} 笔应付项目，合计 {amount_text}。"
            f"发现 {len(risks)} 项风险，其中高风险 {high_count} 项。"
        )
