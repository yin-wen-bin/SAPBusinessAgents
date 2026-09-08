"""Deterministic Chinese/English intent recognition and parameter extraction."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, timedelta

from .models import Intent, ParsedQuery, QueryParameters


_VENDOR_PATTERNS = (
    re.compile(r"(?:供应商|vendor)\s*(?:编号|代码|号|id)?\s*[:：#]?\s*(\d{6,10})", re.I),
    re.compile(r"(?:客商|vendor)\s*[:：#]?\s*(\d{6,10})", re.I),
)
_COMPANY_PATTERNS = (
    re.compile(r"(?:公司代码|company\s*code|bukrs)\s*[:：#]?\s*([A-Z0-9]{4})", re.I),
)
_ACCOUNTING_DOCUMENT_PATTERNS = (
    re.compile(r"(?:会计凭证|付款凭证|document)\s*(?:号)?\s*[:：#]?\s*(\d{8,12})", re.I),
)
_INVOICE_PATTERNS = (
    re.compile(r"(?:发票|invoice)\s*(?:编号|号码|号|reference|ref)?\s*[:：#]?\s*([A-Z0-9][A-Z0-9_./-]{2,30})", re.I),
)
_YEAR_PATTERN = re.compile(r"(?:财年|会计年度|fiscal\s*year)\s*[:：]?\s*(20\d{2})", re.I)
_EXPLICIT_DATE_RANGE = re.compile(
    r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?\s*(?:到|至|~|—|-)\s*"
    r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?"
)
_FUTURE_DAYS = re.compile(r"(?:未来|接下来)\s*(\d{1,3})\s*天")


class ApIntentParser:
    def parse(self, text: str, *, as_of: date | None = None) -> ParsedQuery:
        normalized = " ".join(text.strip().split())
        reference_date = as_of or date.today()
        notes: list[str] = []

        parameters = QueryParameters(
            vendor_id=self._first_group(_VENDOR_PATTERNS, normalized),
            company_code=self._first_group(_COMPANY_PATTERNS, normalized),
            invoice_reference=self._first_group(_INVOICE_PATTERNS, normalized),
            accounting_document=self._first_group(_ACCOUNTING_DOCUMENT_PATTERNS, normalized),
            fiscal_year=self._extract_year(normalized),
            as_of=reference_date,
        )
        parameters, date_note = self._extract_due_window(normalized, parameters)
        if date_note:
            notes.append(date_note)

        intent, confidence = self._classify(normalized, parameters)
        return ParsedQuery(
            text=normalized,
            intent=intent,
            confidence=confidence,
            parameters=parameters,
            extraction_notes=tuple(notes),
        )

    @staticmethod
    def _first_group(patterns: tuple[re.Pattern[str], ...], text: str) -> str | None:
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return match.group(1).upper()
        return None

    @staticmethod
    def _extract_year(text: str) -> int | None:
        match = _YEAR_PATTERN.search(text)
        return int(match.group(1)) if match else None

    def _extract_due_window(
        self, text: str, parameters: QueryParameters
    ) -> tuple[QueryParameters, str | None]:
        explicit = _EXPLICIT_DATE_RANGE.search(text)
        if explicit:
            values = [int(value) for value in explicit.groups()]
            start = date(values[0], values[1], values[2])
            end = date(values[3], values[4], values[5])
            if start > end:
                start, end = end, start
            return replace(parameters, due_from=start, due_to=end), "使用明确日期范围"

        as_of = parameters.as_of
        if "下周" in text:
            next_monday = as_of + timedelta(days=7 - as_of.weekday())
            return (
                replace(parameters, due_from=next_monday, due_to=next_monday + timedelta(days=6)),
                "下周按周一至周日解析",
            )
        if "本周" in text or "这周" in text:
            monday = as_of - timedelta(days=as_of.weekday())
            return (
                replace(parameters, due_from=monday, due_to=monday + timedelta(days=6)),
                "本周按周一至周日解析",
            )
        future = _FUTURE_DAYS.search(text)
        if future:
            days = int(future.group(1))
            return (
                replace(parameters, due_from=as_of, due_to=as_of + timedelta(days=days)),
                f"未来{days}天含查询日",
            )
        if "今天" in text or "今日" in text:
            return replace(parameters, due_from=as_of, due_to=as_of), "今天"
        if "明天" in text or "明日" in text:
            tomorrow = as_of + timedelta(days=1)
            return replace(parameters, due_from=tomorrow, due_to=tomorrow), "明天"
        return parameters, None

    @staticmethod
    def _classify(text: str, parameters: QueryParameters) -> tuple[Intent, float]:
        lower = text.lower()
        if any(word in lower for word in ("风险", "检查", "重复", "冻结", "异常银行", "逾期")):
            return Intent.PAYMENT_RISK, 0.96
        if (parameters.invoice_reference or parameters.accounting_document) and any(
            word in lower for word in ("付款", "支付", "状态", "付了吗", "paid")
        ):
            return Intent.INVOICE_STATUS, 0.96
        if parameters.due_from or any(
            word in lower for word in ("到期", "应付款", "待付款", "未来", "due")
        ):
            return Intent.UPCOMING_DUE, 0.94
        if any(word in lower for word in ("未清", "未付款", "欠款", "open item", "余额")):
            return Intent.OPEN_ITEMS, 0.92
        if parameters.vendor_id:
            return Intent.OPEN_ITEMS, 0.65
        return Intent.UNKNOWN, 0.0

