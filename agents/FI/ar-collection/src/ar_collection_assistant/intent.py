from __future__ import annotations

from .models import CustomerAccount, QueryIntent


def detect_intent(query: str) -> QueryIntent:
    normalized = query.casefold()
    if any(term in normalized for term in ("未匹配", "未认领", "银行到账", "unmatched", "unapplied")):
        return QueryIntent.LIST_UNMATCHED_RECEIPTS
    if any(term in normalized for term in ("账龄", "未清项", "未清项目", "aging", "open item")):
        return QueryIntent.GET_AGING
    return QueryIntent.LIST_WEEKLY_COLLECTIONS


def resolve_customer(query: str, accounts: tuple[CustomerAccount, ...]) -> str | None:
    normalized = query.casefold()
    for account in accounts:
        if account.customer_id.casefold() in normalized or account.name.casefold() in normalized:
            return account.customer_id
    return None

