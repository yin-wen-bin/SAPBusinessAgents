"""Auditable rules for net GR/IR residuals and ageing."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from .evidence import EvidenceSnapshot, parse_sap_date
from .model import AnalysisResult, BucketTotal, Finding, GrIrItem, QueryParameters, ResultStatus


ZERO = Decimal("0")
GR_DOCUMENT_TYPES = {"WE", "WA"}
IR_DOCUMENT_TYPES = {"RE", "KG", "KR"}
GR_REFERENCE_TYPES = {"MKPF", "RMRP_GR"}
IR_REFERENCE_TYPES = {"RMRP", "RMRP_IV"}


class GrIrAnalysisError(ValueError):
    """Raised for invalid query parameters or unsafe evidence."""


def _text(row: Mapping[str, Any], field: str) -> str:
    return str(row.get(field, "") or "").strip()


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0").replace(",", ""))
    except InvalidOperation as exc:
        raise GrIrAnalysisError(f"Invalid decimal value: {value!r}") from exc


def _truthy(value: Any) -> bool:
    return _text({"value": value}, "value").upper() in {"1", "TRUE", "X", "YES", "Y"}


def _signed(row: Mapping[str, Any], field: str) -> Decimal:
    value = abs(_decimal(row.get(field)))
    sign = _text(row, "DebitCreditCode").upper()
    # Reversal and return accounting lines already carry the reversing debit or
    # credit sign. Applying IsReversal a second time would double-reverse them.
    return -value if sign in {"H", "C", "CREDIT"} else value


def _source_kind(row: Mapping[str, Any]) -> str:
    explicit = _text(row, "GrIrSourceType").upper()
    if explicit in {"GR", "IR"}:
        return explicit
    reference = _text(row, "OriginalReferenceDocumentType").upper()
    document_type = _text(row, "AccountingDocumentType").upper()
    if reference in GR_REFERENCE_TYPES or document_type in GR_DOCUMENT_TYPES:
        return "GR"
    if reference in IR_REFERENCE_TYPES or document_type in IR_DOCUMENT_TYPES or _text(row, "SupplierInvoice"):
        return "IR"
    return ""


def _bucket(days: int, boundaries: tuple[int, ...]) -> str:
    lower = 0
    for upper in boundaries:
        if days <= upper:
            return f"{lower}-{upper}"
        lower = upper + 1
    return f"{lower}+"


class GrIrAnalyzer:
    def analyze(self, snapshot: EvidenceSnapshot, query: QueryParameters) -> AnalysisResult:
        if not query.company_code.strip():
            raise GrIrAnalysisError("company_code is required")
        if query.ageing_threshold < 0:
            raise GrIrAnalysisError("ageing_threshold cannot be negative")
        if tuple(sorted(set(query.ageing_buckets))) != query.ageing_buckets or any(v < 0 for v in query.ageing_buckets):
            raise GrIrAnalysisError("ageing_buckets must be unique ascending non-negative integers")

        global_findings: list[Finding] = []
        for field, values in (("Plant", query.plants), ("GLAccount", query.gl_accounts)):
            if values and snapshot.rows and all(not _text(row, field) for row in snapshot.rows):
                global_findings.append(Finding(
                    "FILTER_FIELD_MISSING",
                    f"请求按 {field} 筛选，但 evidence 未返回该字段，无法证明范围完整。",
                    "error",
                ))
        selected = [row for row in snapshot.rows if self._selected(row, query)]
        grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
        for row in selected:
            kind = _source_kind(row)
            if not kind:
                key = self._document_key(row)
                global_findings.append(Finding("UNKNOWN_SOURCE_TYPE", "无法将会计行可靠归类为 GR 或 IR。", "error", (key,)))
                continue
            grouped[(_text(row, "CompanyCode"), _text(row, "PurchasingDocument"), _text(row, "PurchasingDocumentItem").zfill(5))].append(row)

        items: list[GrIrItem] = []
        for key, rows in sorted(grouped.items()):
            item, finding = self._analyze_item(key, rows, query)
            if finding:
                global_findings.append(finding)
            elif item is not None and abs(item.residual_amount) > ZERO and item.ageing_days >= query.ageing_threshold:
                items.append(item)

        status = ResultStatus.INCONCLUSIVE if any(f.severity == "error" for f in global_findings) else ResultStatus.COMPLETE
        totals_map: dict[tuple[str, str], tuple[Decimal, int]] = {}
        if status is ResultStatus.COMPLETE:
            for item in items:
                key = (item.company_currency, item.ageing_bucket)
                amount, count = totals_map.get(key, (ZERO, 0))
                totals_map[key] = (amount + item.residual_amount, count + 1)
        totals = tuple(BucketTotal(currency, bucket, amount, count) for (currency, bucket), (amount, count) in sorted(totals_map.items()))
        return AnalysisResult(status, query, tuple(items) if status is ResultStatus.COMPLETE else (), totals, tuple(global_findings), snapshot.complete)

    def _selected(self, row: Mapping[str, Any], query: QueryParameters) -> bool:
        posting_date = parse_sap_date(row.get("PostingDate"))
        return bool(
            _text(row, "CompanyCode") == query.company_code
            and posting_date
            and posting_date <= query.key_date
            and (not query.purchasing_documents or _text(row, "PurchasingDocument") in query.purchasing_documents)
            and (not query.plants or _text(row, "Plant") in query.plants)
            and (not query.gl_accounts or _text(row, "GLAccount") in query.gl_accounts)
        )

    def _analyze_item(self, key: tuple[str, str, str], rows: list[Mapping[str, Any]], query: QueryParameters) -> tuple[GrIrItem | None, Finding | None]:
        currencies = {_text(row, "CompanyCodeCurrency") for row in rows}
        if len(currencies) != 1 or "" in currencies:
            return None, Finding("MIXED_COMPANY_CURRENCY", "同一 PO 项目存在多个或缺失公司代码币种，无法安全合计。", "error", tuple(self._document_key(row) for row in rows))
        transaction_currencies = {_text(row, "TransactionCurrency") for row in rows if _text(row, "TransactionCurrency")}
        units = {_text(row, "PurchaseOrderQuantityUnit") or _text(row, "BaseUnit") for row in rows if _text(row, "PurchaseOrderQuantityUnit") or _text(row, "BaseUnit")}
        service = any(_truthy(row.get("IsServicePurchaseOrder")) or _text(row, "ProductType").upper() == "2" for row in rows)
        if len(units) > 1 and not service:
            return None, Finding("MIXED_QUANTITY_UNIT", "同一 PO 项目存在多个数量单位，且没有可验证换算。", "error", tuple(self._document_key(row) for row in rows))

        gr_rows = [row for row in rows if _source_kind(row) == "GR"]
        ir_rows = [row for row in rows if _source_kind(row) == "IR"]
        gr_value = sum((_signed(row, "AmountInCompanyCodeCurrency") for row in gr_rows), ZERO)
        ir_value = sum((_signed(row, "AmountInCompanyCodeCurrency") for row in ir_rows), ZERO)
        # GR/IR clearing lines normally carry opposite debit/credit signs. The
        # residual is therefore their signed net balance.
        residual = gr_value + ir_value
        if service and len(units) > 1:
            gr_quantity = ZERO
            ir_quantity = ZERO
            findings = [Finding("SERVICE_QUANTITY_NOT_COMPARABLE", "服务采购订单存在多个数量单位；仅输出金额余额。", "warning")]
        else:
            gr_quantity = sum((_signed(row, "PurchaseOrderQuantity") for row in gr_rows), ZERO)
            ir_quantity = sum((_signed(row, "PurchaseOrderQuantity") for row in ir_rows), ZERO)
            findings = []
        last_date = max(parse_sap_date(row.get("PostingDate")) for row in rows)
        assert last_date is not None
        age = max((query.key_date - last_date).days, 0)
        if not gr_rows:
            findings.append(Finding("IR_WITHOUT_GR", "截至关键日存在 IR，但未找到 GR 会计行。", "warning"))
        if not ir_rows:
            findings.append(Finding("GR_WITHOUT_IR", "截至关键日存在 GR，但未找到 IR 会计行。", "warning"))
        if len(transaction_currencies) > 1:
            findings.append(Finding("MIXED_TRANSACTION_CURRENCY", "交易币种不一致；余额仅按公司代码币种计算。", "warning"))
        return GrIrItem(
            company_code=key[0], purchase_order=key[1], purchase_order_item=key[2],
            supplier=next((_text(row, "Supplier") for row in rows if _text(row, "Supplier")), ""),
            material=next((_text(row, "Material") for row in rows if _text(row, "Material")), ""),
            plant=next((_text(row, "Plant") for row in rows if _text(row, "Plant")), ""),
            service_item=service, gr_quantity=gr_quantity, ir_quantity=ir_quantity,
            quantity_unit="MULTI" if service and len(units) > 1 else next(iter(units), ""), gr_value=gr_value, ir_value=ir_value,
            residual_amount=residual, company_currency=next(iter(currencies)),
            transaction_currency=next(iter(transaction_currencies)) if len(transaction_currencies) == 1 else "MULTI",
            last_activity_date=last_date, ageing_days=age, ageing_bucket=_bucket(age, query.ageing_buckets),
            source_documents=tuple(dict.fromkeys(reference for row in rows for reference in self._source_references(row))), findings=tuple(findings),
        ), None

    @staticmethod
    def _document_key(row: Mapping[str, Any]) -> str:
        return "/".join(_text(row, field) for field in ("CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem"))

    @classmethod
    def _source_references(cls, row: Mapping[str, Any]) -> tuple[str, ...]:
        references = [f"FI:{cls._document_key(row)}"]
        for label, number_field, year_field in (
            ("ORIGINAL", "OriginalReferenceDocument", "OriginalReferenceDocumentFiscalYear"),
            ("MATERIAL", "MaterialDocument", "MaterialDocumentYear"),
            ("INVOICE", "SupplierInvoice", "SupplierInvoiceFiscalYear"),
        ):
            number = _text(row, number_field)
            if number:
                year = _text(row, year_field)
                references.append(f"{label}:{number}/{year}" if year else f"{label}:{number}")
        return tuple(references)
