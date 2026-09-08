"""Deterministic natural-language parameter extraction."""

from __future__ import annotations

import re

from .model import QueryParameters


class ParameterExtractionError(ValueError):
    """Raised when a question cannot identify one PO unambiguously."""


_EXPLICIT_PO = re.compile(
    r"(?i)(?:采购订单|采购单|purchase\s+order|\bpo)\s*(?:号|number|no\.?|#|:|：)?\s*(\d{6,12})"
)
_GENERIC_PO = re.compile(r"(?<!\d)(\d{10})(?!\d)")
_ITEM = re.compile(
    r"(?i)(?:行项目|项目|item)\s*(?:号|number|no\.?|#|:|：)?\s*(\d{1,5})(?!\d)"
)


def extract_query_parameters(question: str) -> QueryParameters:
    """Extract a PO and optional item from Chinese or English text."""

    if not question or not question.strip():
        raise ParameterExtractionError("问题为空；请提供采购订单号。")

    explicit = _EXPLICIT_PO.findall(question)
    candidates = explicit or _GENERIC_PO.findall(question)
    unique = list(dict.fromkeys(candidates))
    if not unique:
        raise ParameterExtractionError("未识别到采购订单号；请提供 10 位 PO，例如 4500001234。")
    if len(unique) > 1:
        raise ParameterExtractionError(
            f"识别到多个采购订单号（{', '.join(unique)}）；一次查询只支持一个 PO。"
        )

    item_match = _ITEM.search(question)
    item_number = item_match.group(1).zfill(5) if item_match else None
    return QueryParameters(po_number=unique[0], item_number=item_number)

