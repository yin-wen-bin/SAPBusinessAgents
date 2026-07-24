"""Explainable AP payment risk rules."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Iterable, Sequence

from .models import (
    PayableItem,
    RiskFinding,
    Severity,
    VendorBankAccount,
    VendorProfile,
)


class PaymentRiskEngine:
    """Run deterministic rules and return evidence suitable for audit/review."""

    def __init__(self, *, duplicate_amount_window_days: int = 30) -> None:
        self.duplicate_amount_window_days = duplicate_amount_window_days

    def evaluate(
        self,
        *,
        items: Sequence[PayableItem],
        context_items: Sequence[PayableItem],
        profile: VendorProfile | None,
        bank_accounts: Sequence[VendorBankAccount],
        as_of: date,
    ) -> tuple[RiskFinding, ...]:
        scope_keys = {item.document_key for item in items}
        findings: list[RiskFinding] = []
        findings.extend(self._duplicate_invoice(context_items, scope_keys))
        findings.extend(self._duplicate_amount(context_items, scope_keys))
        findings.extend(self._item_level(items, as_of))
        findings.extend(self._abnormal_bank(items, profile, bank_accounts, as_of))
        severity_order = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
        return tuple(
            sorted(
                findings,
                key=lambda finding: (
                    severity_order[finding.severity],
                    finding.rule_id,
                    finding.related_documents,
                ),
            )
        )

    @staticmethod
    def _duplicate_invoice(
        context_items: Sequence[PayableItem], scope_keys: set[str]
    ) -> Iterable[RiskFinding]:
        groups: dict[tuple[str, str, str], list[PayableItem]] = defaultdict(list)
        for item in context_items:
            if item.is_cleared:
                continue
            normalized_reference = re.sub(r"[^A-Z0-9]", "", item.invoice_reference.upper())
            if normalized_reference:
                groups[(item.vendor_id, item.company_code, normalized_reference)].append(item)

        for (_, _, normalized_reference), group in groups.items():
            if len(group) < 2 or not any(item.document_key in scope_keys for item in group):
                continue
            yield RiskFinding(
                rule_id="DUPLICATE_INVOICE_REFERENCE",
                severity=Severity.HIGH,
                title="疑似重复发票",
                explanation="同一供应商和公司代码下存在多个未清项目使用相同发票参考号。",
                related_documents=tuple(sorted(item.document_key for item in group)),
                evidence={
                    "normalized_invoice_reference": normalized_reference,
                    "invoice_references": sorted({item.invoice_reference for item in group}),
                    "count": len(group),
                },
                recommended_action="在 FB03/MIR4 核对原始发票与冲销记录，确认前暂停付款。",
            )

    def _duplicate_amount(
        self, context_items: Sequence[PayableItem], scope_keys: set[str]
    ) -> Iterable[RiskFinding]:
        groups: dict[tuple[str, str, str, str], list[PayableItem]] = defaultdict(list)
        for item in context_items:
            if not item.is_cleared:
                groups[
                    (item.vendor_id, item.company_code, item.currency, str(abs(item.amount)))
                ].append(item)

        for (_, _, currency, amount), group in groups.items():
            references = {re.sub(r"[^A-Z0-9]", "", item.invoice_reference.upper()) for item in group}
            dates = [item.invoice_date for item in group]
            if (
                len(group) < 2
                or len(references) < 2
                or (max(dates) - min(dates)).days > self.duplicate_amount_window_days
                or not any(item.document_key in scope_keys for item in group)
            ):
                continue
            yield RiskFinding(
                rule_id="DUPLICATE_AMOUNT",
                severity=Severity.MEDIUM,
                title="短期内重复金额",
                explanation=(
                    f"{self.duplicate_amount_window_days} 天窗口内存在不同发票参考号但金额相同的未清项目。"
                ),
                related_documents=tuple(sorted(item.document_key for item in group)),
                evidence={
                    "amount": amount,
                    "currency": currency,
                    "invoice_dates": sorted(str(value) for value in dates),
                    "invoice_references": sorted(item.invoice_reference for item in group),
                },
                recommended_action="核对采购订单、收货和发票影像，确认不是重复录入或拆单异常。",
            )

    @staticmethod
    def _item_level(items: Sequence[PayableItem], as_of: date) -> Iterable[RiskFinding]:
        for item in items:
            if item.payment_block:
                yield RiskFinding(
                    rule_id="PAYMENT_BLOCK",
                    severity=Severity.HIGH,
                    title="付款冻结",
                    explanation="未清项目设置了付款冻结，自动付款运行不会正常支付。",
                    related_documents=(item.document_key,),
                    evidence={"payment_block": item.payment_block, "due_date": str(item.due_date)},
                    recommended_action="在解除冻结前核对审批、发票差异和供应商主数据。",
                )
            if not item.is_cleared and item.due_date < as_of:
                overdue_days = (as_of - item.due_date).days
                yield RiskFinding(
                    rule_id="OVERDUE_PAYMENT",
                    severity=Severity.MEDIUM,
                    title="逾期未付款",
                    explanation=f"项目已逾期 {overdue_days} 天且仍未清账。",
                    related_documents=(item.document_key,),
                    evidence={"due_date": str(item.due_date), "overdue_days": overdue_days},
                    recommended_action="核查付款条件、争议状态和最近一次 F110 付款建议。",
                )

    @staticmethod
    def _abnormal_bank(
        items: Sequence[PayableItem],
        profile: VendorProfile | None,
        bank_accounts: Sequence[VendorBankAccount],
        as_of: date,
    ) -> Iterable[RiskFinding]:
        accounts = {account.account_id: account for account in bank_accounts}
        related_by_account: dict[str, list[str]] = defaultdict(list)
        for item in items:
            if item.bank_account_id:
                related_by_account[item.bank_account_id].append(item.document_key)

        for account_id, related_documents in related_by_account.items():
            account = accounts.get(account_id)
            if account is None:
                yield RiskFinding(
                    rule_id="BANK_ACCOUNT_NOT_FOUND",
                    severity=Severity.HIGH,
                    title="付款银行账户无法验证",
                    explanation="应付项目引用的银行账户不在当前供应商银行主数据快照中。",
                    related_documents=tuple(sorted(related_documents)),
                    evidence={"account_id": account_id},
                    recommended_action="暂停付款并由供应商主数据团队复核银行账户。",
                )
                continue

            country_mismatch = bool(profile and account.bank_country != profile.country)
            if account.is_verified and not country_mismatch:
                continue
            days_since_change = (as_of - account.changed_on).days if account.changed_on else None
            reasons = []
            if not account.is_verified:
                reasons.append("账户未完成验证")
            if country_mismatch:
                reasons.append("银行国家与供应商国家不一致")
            yield RiskFinding(
                rule_id="ABNORMAL_BANK_ACCOUNT",
                severity=Severity.HIGH if not account.is_verified else Severity.MEDIUM,
                title="异常银行账户",
                explanation="；".join(reasons) + "。",
                related_documents=tuple(sorted(related_documents)),
                evidence={
                    "account_id": account.account_id,
                    "masked_account": account.masked_account,
                    "vendor_country": profile.country if profile else None,
                    "bank_country": account.bank_country,
                    "is_verified": account.is_verified,
                    "changed_on": str(account.changed_on) if account.changed_on else None,
                    "days_since_change": days_since_change,
                },
                recommended_action="暂停付款并通过独立回拨流程验证银行变更，不使用发票上的联系方式。",
            )

