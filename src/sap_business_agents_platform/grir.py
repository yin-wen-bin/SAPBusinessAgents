from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Callable, Mapping, Sequence


class HistoryEventType(StrEnum):
    GOODS_RECEIPT = "goods_receipt"
    GOODS_RECEIPT_REVERSAL = "goods_receipt_reversal"
    GOODS_RETURN = "goods_return"
    INVOICE_RECEIPT = "invoice_receipt"
    INVOICE_REVERSAL = "invoice_reversal"
    CREDIT_MEMO = "credit_memo"


class ReasonCode(StrEnum):
    GR_WITHOUT_IR = "gr_without_ir"
    IR_WITHOUT_GR = "ir_without_gr"
    QUANTITY_DIFFERENCE = "quantity_difference"
    PRICE_DIFFERENCE = "price_difference"
    RETURN_PENDING = "return_pending"
    LONG_OUTSTANDING = "long_outstanding"
    UNIT_CONFLICT = "unit_conflict"
    CURRENCY_CONFLICT = "currency_conflict"
    EVIDENCE_INCOMPLETE = "evidence_incomplete"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, order=True)
class PurchaseOrderKey:
    po_number: str
    po_item: str


@dataclass(frozen=True)
class PurchaseOrderItem:
    key: PurchaseOrderKey
    company_code: str
    plant: str
    vendor: str
    currency: str
    ordered_quantity: Decimal
    net_price: Decimal
    price_unit: Decimal = Decimal("1")
    material: str = ""
    description: str = ""
    purchasing_group: str = ""

    def __post_init__(self) -> None:
        if self.price_unit <= 0:
            raise ValueError("price_unit must be greater than zero")


@dataclass(frozen=True)
class PurchaseOrderHistoryEvent:
    key: PurchaseOrderKey
    event_id: str
    event_type: HistoryEventType
    posting_date: date
    quantity: Decimal
    amount: Decimal
    currency: str
    document_number: str
    fiscal_year: str = ""
    movement_type: str = ""
    reference: str = ""

    @property
    def is_gr_side(self) -> bool:
        return self.event_type in {
            HistoryEventType.GOODS_RECEIPT,
            HistoryEventType.GOODS_RECEIPT_REVERSAL,
            HistoryEventType.GOODS_RETURN,
        }


@dataclass(frozen=True)
class AnalysisCriteria:
    as_of_date: date
    company_code: str | None = None
    plant: str | None = None
    po_number: str | None = None
    activity_from: date | None = None
    activity_to: date | None = None

    def __post_init__(self) -> None:
        if self.activity_from and self.activity_to and self.activity_from > self.activity_to:
            raise ValueError("activity_from must not be after activity_to")
        if self.activity_from and self.activity_from > self.as_of_date:
            raise ValueError("activity_from must not be after as_of_date")
        if self.activity_to and self.activity_to > self.as_of_date:
            raise ValueError("activity_to must not be after as_of_date")


@dataclass(frozen=True)
class GrirException:
    po: PurchaseOrderItem
    primary_reason: ReasonCode
    reasons: tuple[ReasonCode, ...]
    gr_quantity: Decimal
    ir_quantity: Decimal
    quantity_difference: Decimal
    gr_amount: Decimal
    ir_amount: Decimal
    amount_difference: Decimal
    oldest_open_date: date
    age_days: int
    responsibility: str
    recommendation: str
    severity: Severity
    history_documents: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AnalysisSummary:
    examined_po_items: int
    exception_count: int
    total_absolute_amount_difference: Decimal
    counts_by_reason: dict[str, int]
    counts_by_responsibility: dict[str, int]


@dataclass(frozen=True)
class AnalysisReport:
    criteria: AnalysisCriteria
    source_name: str
    items: tuple[GrirException, ...]
    summary: AnalysisSummary
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class RuleConfig:
    quantity_tolerance: Decimal = Decimal("0.001")
    amount_tolerance: Decimal = Decimal("0.01")
    long_outstanding_days: int = 90
    high_severity_days: int = 180

    def __post_init__(self) -> None:
        if self.quantity_tolerance < 0 or self.amount_tolerance < 0:
            raise ValueError("tolerances must be non-negative")
        if self.long_outstanding_days < 0 or self.high_severity_days < 0:
            raise ValueError("aging thresholds must be non-negative")


_RESPONSIBILITY_ZH = {
    ReasonCode.GR_WITHOUT_IR: "供应商 / 应付账款",
    ReasonCode.IR_WITHOUT_GR: "收货部门 / 采购",
    ReasonCode.QUANTITY_DIFFERENCE: "收货部门 / 采购 / 应付账款",
    ReasonCode.PRICE_DIFFERENCE: "采购 / 应付账款",
    ReasonCode.RETURN_PENDING: "采购 / 供应商 / 应付账款",
    ReasonCode.LONG_OUTSTANDING: "总账会计 / 采购",
}

_RESPONSIBILITY_EN = {
    ReasonCode.GR_WITHOUT_IR: "Supplier / Accounts Payable",
    ReasonCode.IR_WITHOUT_GR: "Receiving / Purchasing",
    ReasonCode.QUANTITY_DIFFERENCE: "Receiving / Purchasing / Accounts Payable",
    ReasonCode.PRICE_DIFFERENCE: "Purchasing / Accounts Payable",
    ReasonCode.RETURN_PENDING: "Purchasing / Supplier / Accounts Payable",
    ReasonCode.LONG_OUTSTANDING: "General Ledger / Purchasing",
}

_RECOMMENDATION_ZH = {
    ReasonCode.GR_WITHOUT_IR: "核实供应商发票状态；应收未收则催票并过账，确定不再开票时按审批政策评估MR11。",
    ReasonCode.IR_WITHOUT_GR: "核实实物和入库凭证；已收货则补做收货，未收货或发票错误则冲销或更正发票。",
    ReasonCode.QUANTITY_DIFFERENCE: "逐笔核对交货、收货和发票数量，补录缺失凭证或冲销错误业务。",
    ReasonCode.PRICE_DIFFERENCE: "核对采购订单条件和发票价格；只对确认不会再发生后续业务的尾差评估MR11。",
    ReasonCode.RETURN_PENDING: "核对退货凭证和供应商贷项；补录贷项或冲销错误退货。",
    ReasonCode.LONG_OUTSTANDING: "发起跨部门清理；确认不会再发生后续收货或发票并完成审批后再评估MR11。",
}

_RECOMMENDATION_EN = {
    ReasonCode.GR_WITHOUT_IR: "Confirm the supplier-invoice status; obtain and post the invoice, or assess MR11 under approval policy only when no invoice will follow.",
    ReasonCode.IR_WITHOUT_GR: "Confirm the physical receipt and receipt document; post the missing receipt or reverse/correct the invoice.",
    ReasonCode.QUANTITY_DIFFERENCE: "Reconcile delivery, receipt, and invoice quantities document by document, then post or reverse the incorrect business document.",
    ReasonCode.PRICE_DIFFERENCE: "Reconcile purchase-order conditions and invoice price; assess MR11 only for an approved residual with no further business expected.",
    ReasonCode.RETURN_PENDING: "Reconcile the return and supplier credit memo, then post the missing credit or reverse the incorrect return.",
    ReasonCode.LONG_OUTSTANDING: "Start cross-functional cleanup and assess MR11 only after confirming that no further receipt or invoice will occur and approval is complete.",
}


class GrirAnalyzer:
    def __init__(self, config: RuleConfig | None = None) -> None:
        self.config = config or RuleConfig()

    def analyze_item(
        self,
        po: PurchaseOrderItem,
        history: Sequence[PurchaseOrderHistoryEvent],
        as_of_date: date,
    ) -> GrirException | None:
        relevant = tuple(event for event in history if event.posting_date <= as_of_date)
        gr_events = tuple(event for event in relevant if event.is_gr_side)
        ir_events = tuple(event for event in relevant if not event.is_gr_side)
        gr_quantity = sum((event.quantity for event in gr_events), Decimal("0"))
        ir_quantity = sum((event.quantity for event in ir_events), Decimal("0"))
        gr_amount = sum((event.amount for event in gr_events), Decimal("0"))
        ir_amount = sum((event.amount for event in ir_events), Decimal("0"))
        quantity_difference = gr_quantity - ir_quantity
        amount_difference = gr_amount - ir_amount
        primary = self.classify_totals(
            has_return=any(event.event_type == HistoryEventType.GOODS_RETURN for event in relevant),
            gr_quantity=gr_quantity,
            ir_quantity=ir_quantity,
            quantity_difference=quantity_difference,
            amount_difference=amount_difference,
        )
        if primary is None:
            return None

        use_quantity = abs(quantity_difference) > self.config.quantity_tolerance
        contribution: Callable[[PurchaseOrderHistoryEvent], Decimal]
        if use_quantity:
            contribution = lambda event: event.quantity if event.is_gr_side else -event.quantity
            tolerance = self.config.quantity_tolerance
        else:
            contribution = lambda event: event.amount if event.is_gr_side else -event.amount
            tolerance = self.config.amount_tolerance
        oldest_open_date = self._current_open_cycle_start(relevant, contribution, tolerance)
        age_days = max(0, (as_of_date - oldest_open_date).days)
        reasons = [primary]
        if age_days >= self.config.long_outstanding_days and primary != ReasonCode.LONG_OUTSTANDING:
            reasons.append(ReasonCode.LONG_OUTSTANDING)
        severity = self.severity(primary, age_days)
        recommendation = _RECOMMENDATION_ZH[primary]
        if ReasonCode.LONG_OUTSTANDING in reasons and primary != ReasonCode.LONG_OUTSTANDING:
            recommendation = f"{recommendation} 因账龄已超阈值，同时执行长期挂账复核。"
        return GrirException(
            po=po,
            primary_reason=primary,
            reasons=tuple(reasons),
            gr_quantity=gr_quantity,
            ir_quantity=ir_quantity,
            quantity_difference=quantity_difference,
            gr_amount=gr_amount,
            ir_amount=ir_amount,
            amount_difference=amount_difference,
            oldest_open_date=oldest_open_date,
            age_days=age_days,
            responsibility=_RESPONSIBILITY_ZH[primary],
            recommendation=recommendation,
            severity=severity,
            history_documents=tuple(dict.fromkeys(event.document_number for event in relevant)),
        )

    def classify_totals(
        self,
        *,
        has_return: bool,
        gr_quantity: Decimal,
        ir_quantity: Decimal,
        quantity_difference: Decimal,
        amount_difference: Decimal,
    ) -> ReasonCode | None:
        qtol = self.config.quantity_tolerance
        atol = self.config.amount_tolerance
        if has_return and ir_quantity - gr_quantity > qtol:
            return ReasonCode.RETURN_PENDING
        if gr_quantity > qtol and abs(ir_quantity) <= qtol:
            return ReasonCode.GR_WITHOUT_IR
        if ir_quantity > qtol and abs(gr_quantity) <= qtol:
            return ReasonCode.IR_WITHOUT_GR
        if abs(quantity_difference) > qtol:
            return ReasonCode.QUANTITY_DIFFERENCE
        if abs(amount_difference) > atol:
            return ReasonCode.PRICE_DIFFERENCE
        return None

    def severity(self, primary: ReasonCode, age_days: int) -> Severity:
        if primary == ReasonCode.RETURN_PENDING or age_days >= self.config.high_severity_days:
            return Severity.HIGH
        if age_days >= self.config.long_outstanding_days or primary in {
            ReasonCode.IR_WITHOUT_GR,
            ReasonCode.QUANTITY_DIFFERENCE,
        }:
            return Severity.MEDIUM
        return Severity.LOW

    @staticmethod
    def _current_open_cycle_start(
        history: Sequence[PurchaseOrderHistoryEvent],
        contribution: Callable[[PurchaseOrderHistoryEvent], Decimal],
        tolerance: Decimal,
    ) -> date:
        if not history:
            raise ValueError("cannot calculate aging without PO history")
        balance = Decimal("0")
        anchor: date | None = None
        ordered = sorted(history, key=lambda event: (event.posting_date, event.event_id))
        for event in ordered:
            previous = balance
            balance += contribution(event)
            if abs(balance) <= tolerance:
                balance = Decimal("0")
                anchor = None
            elif abs(previous) <= tolerance or previous * balance < 0:
                anchor = event.posting_date
        return anchor or ordered[0].posting_date


def evaluate_odata_grir(
    *,
    analysis_date: date,
    po_items: Sequence[Mapping[str, Any]],
    material_documents: Sequence[Mapping[str, Any]],
    material_document_headers: Sequence[Mapping[str, Any]],
    supplier_invoice_items: Sequence[Mapping[str, Any]],
    supplier_invoice_headers: Sequence[Mapping[str, Any]],
    gl_items: Sequence[Mapping[str, Any]],
    candidate_gl_items: Sequence[Mapping[str, Any]],
    source_complete: bool,
    incomplete_steps: Sequence[str] = (),
    config: RuleConfig | None = None,
) -> dict[str, Any]:
    analyzer = GrirAnalyzer(config)
    config = analyzer.config
    header_by_key = {
        (_text(row, "SupplierInvoice"), _text(row, "FiscalYear")): row
        for row in supplier_invoice_headers
        if _text(row, "SupplierInvoice") and _text(row, "FiscalYear")
    }
    material_header_by_key = {
        (_text(row, "MaterialDocument"), _text(row, "MaterialDocumentYear")): row
        for row in material_document_headers
        if _text(row, "MaterialDocument") and _text(row, "MaterialDocumentYear")
    }
    po_by_key = {
        _po_key(row, "PurchaseOrder", "PurchaseOrderItem"): row
        for row in po_items
        if _po_key(row, "PurchaseOrder", "PurchaseOrderItem") is not None
    }
    candidate_keys = {
        key
        for row in candidate_gl_items
        if (key := _po_key(row, "PurchasingDocument", "PurchasingDocumentItem")) is not None
    }
    if not candidate_keys:
        candidate_keys = {
            key
            for row in gl_items
            if (key := _po_key(row, "PurchasingDocument", "PurchasingDocumentItem")) is not None
        }
    candidate_keys.update(po_by_key)
    materials_by_key = _group_rows(material_documents, "PurchaseOrder", "PurchaseOrderItem")
    invoices_by_key = _group_rows(supplier_invoice_items, "PurchaseOrder", "PurchaseOrderItem")
    gl_by_key = _group_rows(gl_items, "PurchasingDocument", "PurchasingDocumentItem")

    records: list[dict[str, Any]] = []
    global_gaps = {str(value) for value in incomplete_steps if str(value)}
    for key in sorted(candidate_keys):
        po_row = po_by_key.get(key)
        material_rows = list(materials_by_key.get(key, ()))
        invoice_rows = list(invoices_by_key.get(key, ()))
        accounting_rows = list(gl_by_key.get(key, ()))
        local_gaps: set[str] = set()
        if po_row is None:
            local_gaps.add("purchase_order_item_missing")
            po_row = {}
        if not accounting_rows:
            local_gaps.add("grir_gl_history_missing")

        receipt_quantity = Decimal("0")
        receipt_units: set[str] = set()
        receipt_dates: list[date] = []
        has_return = False
        material_refs: list[str] = []
        for row in material_rows:
            header_key = (_text(row, "MaterialDocument"), _text(row, "MaterialDocumentYear"))
            posting_date = _date_value(
                row.get("PostingDate") or (material_header_by_key.get(header_key) or {}).get("PostingDate")
            )
            if posting_date is None:
                local_gaps.add("material_document_posting_date_missing")
            elif posting_date > analysis_date:
                continue
            quantity = _decimal_value(row.get("QuantityInEntryUnit", row.get("QuantityInBaseUnit")))
            signed = _signed_value(quantity, row.get("DebitCreditCode"))
            if signed is None:
                local_gaps.add("material_document_quantity_or_direction_missing")
                continue
            receipt_quantity += signed
            unit = _text(row, "EntryUnit", "MaterialBaseUnit", "BaseUnit")
            if unit:
                receipt_units.add(unit.upper())
            else:
                local_gaps.add("receipt_unit_missing")
            movement = _text(row, "GoodsMovementType")
            if movement in {"122", "161"}:
                has_return = True
            if posting_date:
                receipt_dates.append(posting_date)
            material_refs.append(
                _document_ref(row, "MaterialDocument", "MaterialDocumentYear", "MaterialDocumentItem")
            )

        invoice_quantity = Decimal("0")
        invoice_units: set[str] = set()
        invoice_dates: list[date] = []
        invoice_refs: list[str] = []
        for row in invoice_rows:
            header_key = (_text(row, "SupplierInvoice"), _text(row, "FiscalYear"))
            header = header_by_key.get(header_key)
            posting_date = _date_value((header or {}).get("PostingDate"))
            if posting_date is None:
                local_gaps.add("supplier_invoice_header_or_posting_date_missing")
            elif posting_date > analysis_date:
                continue
            quantity = _decimal_value(row.get("QuantityInPurchaseOrderUnit"))
            amount = _decimal_value(row.get("SupplierInvoiceItemAmount"))
            credit_flag = _bool_value((header or {}).get("SupplierInvoiceIsCreditMemo"))
            if quantity is None:
                local_gaps.add("supplier_invoice_quantity_missing")
                continue
            if credit_flag is None and (amount is None or amount >= 0):
                local_gaps.add("supplier_invoice_credit_indicator_missing")
                sign = Decimal("1")
            else:
                sign = Decimal("-1") if credit_flag is True or (amount is not None and amount < 0) else Decimal("1")
            invoice_quantity += abs(quantity) * sign
            unit = _text(row, "PurchaseOrderQuantityUnit")
            if unit:
                invoice_units.add(unit.upper())
            else:
                local_gaps.add("invoice_unit_missing")
            if posting_date:
                invoice_dates.append(posting_date)
            invoice_refs.append(
                _document_ref(row, "SupplierInvoice", "FiscalYear", "SupplierInvoiceItem")
            )

        open_amount = Decimal("0")
        gl_currencies: set[str] = set()
        accounting_refs: list[str] = []
        dated_amounts: list[tuple[date, Decimal, str]] = []
        for row in accounting_rows:
            posting_date = _date_value(row.get("PostingDate"))
            if posting_date is None:
                local_gaps.add("grir_posting_date_missing")
            elif posting_date > analysis_date:
                continue
            amount = _decimal_value(row.get("AmountInCompanyCodeCurrency"))
            signed_amount = _signed_value(amount, row.get("DebitCreditCode"))
            if signed_amount is None:
                local_gaps.add("grir_amount_or_direction_missing")
                continue
            open_amount += signed_amount
            currency = _text(row, "CompanyCodeCurrency")
            if currency:
                gl_currencies.add(currency.upper())
            else:
                local_gaps.add("grir_currency_missing")
            ref = _document_ref(
                row, "AccountingDocument", "FiscalYear", "AccountingDocumentItem"
            )
            accounting_refs.append(ref)
            if posting_date:
                dated_amounts.append((posting_date, signed_amount, ref))

        po_unit = _text(po_row, "PurchaseOrderQuantityUnit", "OrderQuantityUnit").upper()
        units = {value for value in {*receipt_units, *invoice_units, po_unit} if value}
        if len(units) > 1:
            local_gaps.add("unit_conflict")
        if len(gl_currencies) > 1:
            local_gaps.add("currency_conflict")
        quantity_difference = receipt_quantity - invoice_quantity
        primary = analyzer.classify_totals(
            has_return=has_return,
            gr_quantity=receipt_quantity,
            ir_quantity=invoice_quantity,
            quantity_difference=quantity_difference,
            amount_difference=open_amount,
        )
        oldest_open_date = _oldest_open_date(
            dated_amounts,
            [*receipt_dates, *invoice_dates],
            open_amount,
            quantity_difference,
            config,
        )
        age_days = max(0, (analysis_date - oldest_open_date).days) if oldest_open_date else None
        reasons: list[ReasonCode] = [primary] if primary else []
        if age_days is not None and age_days >= config.long_outstanding_days and primary:
            reasons.append(ReasonCode.LONG_OUTSTANDING)
        confirmed_action = primary is not None
        complete_for_record = source_complete and not local_gaps and not global_gaps
        reconciliation_status = (
            "requires_action"
            if confirmed_action
            else "matched"
            if complete_for_record
            else "unknown"
        )
        severity = analyzer.severity(primary, age_days or 0) if primary else Severity.LOW
        if not primary and ("unit_conflict" in local_gaps or "currency_conflict" in local_gaps):
            severity = Severity.MEDIUM
        reason_code = primary.value if primary else (
            "unit_conflict"
            if "unit_conflict" in local_gaps
            else "currency_conflict"
            if "currency_conflict" in local_gaps
            else "evidence_incomplete"
            if local_gaps or global_gaps
            else "matched"
        )
        responsibility = (
            {"zh": _RESPONSIBILITY_ZH[primary], "en": _RESPONSIBILITY_EN[primary]}
            if primary
            else {"zh": "财务 / 采购", "en": "Finance / Purchasing"}
        )
        recommendation = (
            {"zh": _RECOMMENDATION_ZH[primary], "en": _RECOMMENDATION_EN[primary]}
            if primary
            else {
                "zh": "补齐缺失证据后重新核对，不能将当前记录视为已清理。",
                "en": "Complete the missing evidence and reconcile again; do not treat the current item as cleared.",
            }
            if reconciliation_status == "unknown"
            else {"zh": "无需后续处理。", "en": "No follow-up is required."}
        )
        records.append(
            {
                "purchase_order": _text(po_row, "PurchaseOrder") or key.po_number,
                "purchase_order_item": _text(po_row, "PurchaseOrderItem") or key.po_item,
                "material": _text(po_row, "Material"),
                "plant": _text(po_row, "Plant"),
                "receipt_quantity": _decimal_text(receipt_quantity),
                "invoice_quantity": _decimal_text(invoice_quantity),
                "unit": next(iter(units), "") if len(units) == 1 else " / ".join(sorted(units)),
                "quantity_difference": _decimal_text(quantity_difference),
                "gr_ir_open_amount": _decimal_text(open_amount),
                "currency": next(iter(gl_currencies), "") if len(gl_currencies) == 1 else " / ".join(sorted(gl_currencies)),
                "oldest_open_date": oldest_open_date.isoformat() if oldest_open_date else "",
                "age_days": age_days,
                "reconciliation_status": reconciliation_status,
                "primary_reason": reason_code,
                "reason_codes": [reason.value for reason in dict.fromkeys(reasons)],
                "severity": severity.value,
                "responsible_team": responsibility,
                "recommended_action": recommendation,
                "material_document_refs": "; ".join(_unique_nonempty(material_refs)),
                "supplier_invoice_refs": "; ".join(_unique_nonempty(invoice_refs)),
                "accounting_document_refs": "; ".join(_unique_nonempty(accounting_refs)),
                "evidence_complete": complete_for_record,
                "evidence_gaps": sorted(local_gaps | global_gaps),
                "business_status": (
                    "attention"
                    if reconciliation_status == "requires_action"
                    else "normal"
                    if reconciliation_status == "matched"
                    else "inconclusive"
                ),
            }
        )

    unassigned_gl = [
        row
        for row in candidate_gl_items
        if _po_key(row, "PurchasingDocument", "PurchasingDocumentItem") is None
    ]
    if unassigned_gl:
        global_gaps.add("unassigned_grir_gl_rows")
        for row in unassigned_gl:
            records.append(
                {
                    "purchase_order": "",
                    "purchase_order_item": "",
                    "material": "",
                    "plant": "",
                    "receipt_quantity": "0",
                    "invoice_quantity": "0",
                    "unit": "",
                    "quantity_difference": "0",
                    "gr_ir_open_amount": _decimal_text(
                        _signed_value(
                            _decimal_value(row.get("AmountInCompanyCodeCurrency")),
                            row.get("DebitCreditCode"),
                        )
                        or Decimal("0")
                    ),
                    "currency": _text(row, "CompanyCodeCurrency"),
                    "oldest_open_date": (_date_value(row.get("PostingDate")) or analysis_date).isoformat(),
                    "age_days": None,
                    "reconciliation_status": "unknown",
                    "primary_reason": "evidence_incomplete",
                    "reason_codes": ["evidence_incomplete"],
                    "severity": "medium",
                    "responsible_team": {"zh": "总账会计", "en": "General Ledger"},
                    "recommended_action": {
                        "zh": "该GR/IR总账行缺少采购订单项目关联，请由总账会计核对原始凭证。",
                        "en": "This GR/IR line lacks a purchase-order item relationship; General Ledger should inspect the source document.",
                    },
                    "material_document_refs": "",
                    "supplier_invoice_refs": "",
                    "accounting_document_refs": _document_ref(
                        row, "AccountingDocument", "FiscalYear", "AccountingDocumentItem"
                    ),
                    "evidence_complete": False,
                    "evidence_gaps": ["unassigned_grir_gl_rows"],
                    "business_status": "inconclusive",
                }
            )

    severity_order = {"high": 0, "medium": 1, "low": 2}
    records.sort(
        key=lambda item: (
            0 if item["reconciliation_status"] == "requires_action" else 1 if item["reconciliation_status"] == "unknown" else 2,
            severity_order.get(str(item.get("severity")), 3),
            -(int(item.get("age_days") or 0)),
            -abs(_decimal_value(item.get("gr_ir_open_amount")) or Decimal("0")),
            str(item.get("purchase_order") or ""),
            str(item.get("purchase_order_item") or ""),
        )
    )
    actions = [item for item in records if item["reconciliation_status"] == "requires_action"]
    unknown = [item for item in records if item["reconciliation_status"] == "unknown"]
    matched = [item for item in records if item["reconciliation_status"] == "matched"]
    all_record_gaps = {
        str(gap)
        for record in records
        for gap in record.get("evidence_gaps") or []
        if str(gap)
    }
    all_gaps = sorted(global_gaps | all_record_gaps)
    evidence_complete = source_complete and not all_gaps and not unknown
    business_status = (
        "inconclusive"
        if not source_complete or not evidence_complete
        else "attention"
        if actions
        else "normal"
    )
    return {
        "records": records,
        "action_records": actions,
        "unknown_records": unknown,
        "matched_records": matched,
        "examined_item_count": len(records),
        "matched_item_count": len(matched),
        "follow_up_item_count": len(actions),
        "unknown_item_count": len(unknown),
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "business_status": business_status,
        "evidence_gaps": all_gaps,
    }


def _po_key(
    row: Mapping[str, Any], po_field: str, item_field: str
) -> PurchaseOrderKey | None:
    po = _text(row, po_field)
    item = _text(row, item_field)
    if not po or not item:
        return None
    return PurchaseOrderKey(po, item.lstrip("0") or "0")


def _group_rows(
    rows: Sequence[Mapping[str, Any]], po_field: str, item_field: str
) -> dict[PurchaseOrderKey, list[Mapping[str, Any]]]:
    grouped: dict[PurchaseOrderKey, list[Mapping[str, Any]]] = {}
    for row in rows:
        key = _po_key(row, po_field, item_field)
        if key is not None:
            grouped.setdefault(key, []).append(row)
    return grouped


def _text(row: Mapping[str, Any], *fields: str) -> str:
    for field_name in fields:
        value = row.get(field_name)
        if value not in {None, ""}:
            return str(value).strip()
    return ""


def _decimal_value(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _signed_value(value: Decimal | None, direction: Any) -> Decimal | None:
    code = str(direction or "").strip().upper()
    if value is None or code not in {"S", "H", "D", "C"}:
        return None
    return abs(value) if code in {"S", "D"} else -abs(value)


def _bool_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "x", "yes"}:
        return True
    if text in {"false", "0", "", "no"} and value is not None:
        return False
    return None


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if text.startswith("/Date("):
        try:
            milliseconds = int(text[6:].split(")", 1)[0].split("+", 1)[0])
            return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).date()
        except (ValueError, OSError):
            return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _document_ref(
    row: Mapping[str, Any], document_field: str, year_field: str, item_field: str
) -> str:
    return "/".join(
        value
        for value in (
            _text(row, document_field),
            _text(row, year_field),
            _text(row, item_field),
        )
        if value
    )


def _unique_nonempty(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _oldest_open_date(
    dated_amounts: Sequence[tuple[date, Decimal, str]],
    quantity_dates: Sequence[date],
    open_amount: Decimal,
    quantity_difference: Decimal,
    config: RuleConfig,
) -> date | None:
    if abs(open_amount) > config.amount_tolerance and dated_amounts:
        balance = Decimal("0")
        anchor: date | None = None
        for posting_date, amount, document in sorted(
            dated_amounts, key=lambda item: (item[0], item[2])
        ):
            previous = balance
            balance += amount
            if abs(balance) <= config.amount_tolerance:
                balance = Decimal("0")
                anchor = None
            elif abs(previous) <= config.amount_tolerance or previous * balance < 0:
                anchor = posting_date
        if anchor:
            return anchor
    if abs(quantity_difference) > config.quantity_tolerance and quantity_dates:
        return min(quantity_dates)
    return min((item[0] for item in dated_amounts), default=None)


__all__ = [
    "AnalysisCriteria",
    "AnalysisReport",
    "AnalysisSummary",
    "GrirAnalyzer",
    "GrirException",
    "HistoryEventType",
    "PurchaseOrderHistoryEvent",
    "PurchaseOrderItem",
    "PurchaseOrderKey",
    "ReasonCode",
    "RuleConfig",
    "Severity",
    "evaluate_odata_grir",
]
