"""Cross-object joins and P2P state-machine evaluation."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .model import (
    DocumentFlow,
    Finding,
    ItemStatus,
    ItemStatusResult,
    P2PReport,
    P2PTables,
    QueryParameters,
    SapRow,
)


ZERO = Decimal("0")
EPSILON = Decimal("0.0001")
PAYMENT_DOCUMENT_TYPES = {"KZ", "ZP", "PY"}
POSTED_INVOICE_STATUSES = {"5", "POSTED"}
NEGATIVE_MOVEMENT_TYPES = {"102", "122", "124", "162"}


class P2PAnalysisError(LookupError):
    """Raised for a PO or item that cannot be analyzed."""


def _text(row: SapRow | None, field: str) -> str:
    if row is None:
        return ""
    return str(row.get(field, "") or "").strip()


def _decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return ZERO
    try:
        return Decimal(str(value).replace(",", ""))
    except InvalidOperation as exc:
        raise P2PAnalysisError(f"无法解析数值 {value!r}") from exc


def _date(value: Any) -> date | None:
    text = str(value or "").strip().replace("-", "")
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        return None


def _key(row: SapRow, *fields: str) -> tuple[str, ...]:
    return tuple(_text(row, field) for field in fields)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _document(number: str, year: str) -> str:
    return f"{number}/{year}" if year else number


def _signed_quantity(row: SapRow, *, movement: bool = False) -> Decimal:
    quantity = _decimal(row.get("MENGE"))
    sign = _text(row, "SHKZG").upper()
    if sign == "H":
        return -quantity
    if sign == "S":
        return quantity
    if movement and _text(row, "BWART") in NEGATIVE_MOVEMENT_TYPES:
        return -quantity
    return quantity


def _signed_amount(row: SapRow) -> Decimal:
    amount = _decimal(row.get("WRBTR") if row.get("WRBTR") not in (None, "") else row.get("DMBTR"))
    return -amount if _text(row, "SHKZG").upper() == "H" else amount


def _is_truthy_flag(value: Any) -> bool:
    return str(value or "").strip().upper() not in {"", "0", "N", "NO", "FALSE"}


class P2PAnalyzer:
    """Join EKKO/EKPO through material, invoice and FI/payment documents."""

    def __init__(self, payment_document_types: Iterable[str] = PAYMENT_DOCUMENT_TYPES):
        configured = {str(value).strip().upper() for value in payment_document_types if str(value).strip()}
        if not configured:
            raise ValueError("At least one payment document type must be configured")
        self.payment_document_types = configured

    def analyze(
        self,
        tables: P2PTables,
        query: QueryParameters,
        *,
        as_of: date,
    ) -> P2PReport:
        headers = [row for row in tables.ekko if _text(row, "EBELN") == query.po_number]
        if not headers:
            raise P2PAnalysisError(f"采购订单 {query.po_number} 不存在或数据源未返回 EKKO。")
        header = headers[0]
        items = [row for row in tables.ekpo if _text(row, "EBELN") == query.po_number]
        if query.item_number:
            items = [row for row in items if _text(row, "EBELP").zfill(5) == query.item_number]
            if not items:
                raise P2PAnalysisError(f"采购订单 {query.po_number} 中不存在项目 {query.item_number}。")
        if not items:
            raise P2PAnalysisError(f"采购订单 {query.po_number} 没有可分析的 EKPO 项目。")

        results = tuple(self._analyze_item(header, item, tables, as_of) for item in items)
        warnings: list[str] = []
        if len(headers) > 1:
            warnings.append("数据源返回多个 EKKO 抬头；已使用第一条。")
        return P2PReport(
            po_number=query.po_number,
            company_code=_text(header, "BUKRS"),
            vendor=_text(header, "LIFNR"),
            currency=_text(header, "WAERS"),
            as_of=as_of,
            items=results,
            warnings=tuple(warnings),
        )

    def _analyze_item(
        self,
        header: SapRow,
        item: SapRow,
        tables: P2PTables,
        as_of: date,
    ) -> ItemStatusResult:
        po = _text(item, "EBELN")
        item_number = _text(item, "EBELP").zfill(5)
        ordered = _decimal(item.get("MENGE"))
        findings: list[Finding] = []

        material_rows = [
            row
            for row in tables.mseg
            if _text(row, "EBELN") == po and _text(row, "EBELP").zfill(5) == item_number
        ]
        history_rows = [
            row
            for row in tables.ekbe
            if _text(row, "EBELN") == po and _text(row, "EBELP").zfill(5) == item_number
        ]
        if material_rows:
            received = sum((_signed_quantity(row, movement=True) for row in material_rows), ZERO)
            material_documents = _unique(
                _document(_text(row, "MBLNR"), _text(row, "MJAHR")) for row in material_rows
            )
            mkpf_keys = {_key(row, "MBLNR", "MJAHR") for row in tables.mkpf}
            missing_headers = _unique(
                _document(_text(row, "MBLNR"), _text(row, "MJAHR"))
                for row in material_rows
                if _key(row, "MBLNR", "MJAHR") not in mkpf_keys
            )
            if missing_headers:
                findings.append(
                    Finding(
                        "MATERIAL_HEADER_MISSING",
                        "MSEG 存在，但对应 MKPF 抬头未完整返回。",
                        "warning",
                        missing_headers,
                    )
                )
        else:
            gr_history = [
                row
                for row in history_rows
                if _text(row, "BEWTP").upper() == "E" or _text(row, "VGABE") == "1"
            ]
            received = sum((_signed_quantity(row, movement=True) for row in gr_history), ZERO)
            material_documents = _unique(
                _document(_text(row, "BELNR"), _text(row, "GJAHR")) for row in gr_history
            )
            if gr_history:
                findings.append(
                    Finding(
                        "GR_FROM_EKBE",
                        "未返回 MSEG 明细；收货数量由 EKBE 采购订单历史回退计算。",
                        "warning",
                        material_documents,
                    )
                )

        invoice_rows = [
            row
            for row in tables.rseg
            if _text(row, "EBELN") == po and _text(row, "EBELP").zfill(5) == item_number
        ]
        rbkp_by_key = {_key(row, "BELNR", "GJAHR"): row for row in tables.rbkp}
        invoice_groups: dict[tuple[str, str], list[SapRow]] = defaultdict(list)
        for row in invoice_rows:
            invoice_groups[_key(row, "BELNR", "GJAHR")].append(row)

        bkpf_by_key = {_key(row, "BUKRS", "BELNR", "GJAHR"): row for row in tables.bkpf}
        bseg_by_key: dict[tuple[str, str, str], list[SapRow]] = defaultdict(list)
        for row in tables.bseg:
            bseg_by_key[_key(row, "BUKRS", "BELNR", "GJAHR")].append(row)

        posted_quantity = ZERO
        posted_amount = ZERO
        paid_amount = ZERO
        open_amount = ZERO
        invoice_documents: list[str] = []
        accounting_documents: list[str] = []
        clearing_documents: list[str] = []
        has_parked_invoice = False
        has_cleared_nonpayment = False

        for invoice_key, rows in invoice_groups.items():
            inv_number, inv_year = invoice_key
            invoice_documents.append(_document(inv_number, inv_year))
            invoice_header = rbkp_by_key.get(invoice_key)
            rbstat = _text(invoice_header, "RBSTAT").upper()
            is_posted = invoice_header is None or rbstat in POSTED_INVOICE_STATUSES
            item_quantity = sum((_signed_quantity(row) for row in rows), ZERO)
            item_amount = sum((_signed_amount(row) for row in rows), ZERO)

            if invoice_header is None:
                findings.append(
                    Finding(
                        "INVOICE_HEADER_MISSING",
                        f"发票 {inv_number}/{inv_year} 有 RSEG 但缺少 RBKP；暂按已过账数量计入。",
                        "warning",
                        (_document(inv_number, inv_year),),
                    )
                )
            elif not is_posted:
                has_parked_invoice = True
                findings.append(
                    Finding(
                        "INVOICE_NOT_POSTED",
                        f"发票 {inv_number}/{inv_year} 尚未过账（RBKP-RBSTAT={rbstat or '空'}）。",
                        "warning",
                        (_document(inv_number, inv_year),),
                    )
                )
                continue

            blocked_fields = sorted(
                {
                    field
                    for row in rows
                    for field, value in row.items()
                    if str(field).upper().startswith("SPGR") and _is_truthy_flag(value)
                }
            )
            if blocked_fields:
                findings.append(
                    Finding(
                        "INVOICE_BLOCKED",
                        f"发票校验存在冻结标识：{', '.join(blocked_fields)}。",
                        "warning",
                        (_document(inv_number, inv_year),),
                    )
                )

            posted_quantity += item_quantity
            posted_amount += item_amount
            fi_headers = [
                row
                for row in tables.bkpf
                if _text(row, "AWTYP").upper() == "RMRP"
                and _text(row, "AWKEY").startswith(f"{inv_number}{inv_year}")
            ]
            if not fi_headers:
                direct_rows = [
                    row
                    for row in tables.bseg
                    if _text(row, "EBELN") == po
                    and _text(row, "EBELP").zfill(5) == item_number
                    and (
                        (_text(row, "LIV_BELNR") == inv_number and _text(row, "LIV_GJAHR") == inv_year)
                        or (len(invoice_groups) == 1 and not _text(row, "LIV_BELNR"))
                    )
                ]
                direct_keys = {_key(row, "BUKRS", "BELNR", "GJAHR") for row in direct_rows}
                fi_headers = [
                    row for row in tables.bkpf if _key(row, "BUKRS", "BELNR", "GJAHR") in direct_keys
                ]
            if not fi_headers:
                open_amount += item_amount
                findings.append(
                    Finding(
                        "FI_DOCUMENT_MISSING",
                        f"发票 {inv_number}/{inv_year} 未关联到 BKPF FI 凭证。",
                        "warning",
                        (_document(inv_number, inv_year),),
                    )
                )
                continue

            # One LIV document normally creates one FI document. If there are
            # several, each line is evaluated and the amounts are capped below.
            invoice_paid_factor = ZERO
            invoice_open_factor = ZERO
            invoice_cleared_nonpayment = False
            for fi_header in fi_headers:
                fi_key = _key(fi_header, "BUKRS", "BELNR", "GJAHR")
                fi_document = _document(fi_key[1], fi_key[2])
                accounting_documents.append(fi_document)
                vendor_lines = [
                    row
                    for row in bseg_by_key.get(fi_key, [])
                    if _text(row, "KOART").upper() == "K" or _text(row, "LIFNR")
                ]
                if not vendor_lines:
                    invoice_open_factor = Decimal("1")
                    findings.append(
                        Finding(
                            "VENDOR_LINE_MISSING",
                            f"FI 凭证 {fi_document} 缺少供应商 BSEG 行，无法确认付款。",
                            "warning",
                            (fi_document,),
                        )
                    )
                    continue

                for vendor_line in vendor_lines:
                    block = _text(vendor_line, "ZLSPR")
                    if block:
                        findings.append(
                            Finding(
                                "PAYMENT_BLOCK",
                                f"FI 凭证 {fi_document} 有付款冻结（BSEG-ZLSPR={block}）。",
                                "error",
                                (fi_document,),
                            )
                        )
                    clearing_number = _text(vendor_line, "AUGBL")
                    clearing_year = _text(vendor_line, "AUGGJ") or fi_key[2]
                    if clearing_number:
                        clearing_key = (fi_key[0], clearing_number, clearing_year)
                        clearing_header = bkpf_by_key.get(clearing_key)
                        clearing_document = _document(clearing_number, clearing_year)
                        clearing_documents.append(clearing_document)
                        if clearing_header and _text(clearing_header, "BLART").upper() in self.payment_document_types:
                            invoice_paid_factor = Decimal("1")
                        else:
                            invoice_cleared_nonpayment = True
                            has_cleared_nonpayment = True
                            findings.append(
                                Finding(
                                    "CLEARED_NOT_PAYMENT",
                                    f"FI 凭证 {fi_document} 已由 {clearing_document} 清账，但该凭证不是已知付款类型。",
                                    "warning",
                                    (fi_document, clearing_document),
                                )
                            )
                        continue

                    # Partial payments point back to the original invoice in
                    # REBZG/REBZJ and leave the invoice vendor item open.
                    vendor_total = abs(_signed_amount(vendor_line))
                    partial_rows = [
                        row
                        for row in tables.bseg
                        if _text(row, "BUKRS") == fi_key[0]
                        and _text(row, "REBZG") == fi_key[1]
                        and (_text(row, "REBZJ") or _text(row, "GJAHR")) == fi_key[2]
                        and _key(row, "BUKRS", "BELNR", "GJAHR") != fi_key
                    ]
                    valid_partial_rows = []
                    for row in partial_rows:
                        payment_header = bkpf_by_key.get(_key(row, "BUKRS", "BELNR", "GJAHR"))
                        if payment_header and _text(payment_header, "BLART").upper() in self.payment_document_types:
                            valid_partial_rows.append(row)
                            clearing_documents.append(
                                _document(_text(row, "BELNR"), _text(row, "GJAHR"))
                            )
                    partial_total = sum((abs(_signed_amount(row)) for row in valid_partial_rows), ZERO)
                    if vendor_total > EPSILON and partial_total > ZERO:
                        invoice_paid_factor = min(Decimal("1"), partial_total / vendor_total)
                    invoice_open_factor = Decimal("1") - invoice_paid_factor

                    due_date = _date(vendor_line.get("FAEDT"))
                    if due_date and due_date < as_of and invoice_open_factor > EPSILON:
                        findings.append(
                            Finding(
                                "PAYMENT_OVERDUE",
                                f"未清金额已于 {due_date.isoformat()} 到期。",
                                "warning",
                                (fi_document,),
                            )
                        )
                    elif due_date and invoice_open_factor > EPSILON:
                        findings.append(
                            Finding(
                                "PAYMENT_NOT_DUE",
                                f"未清金额到期日为 {due_date.isoformat()}。",
                                "info",
                                (fi_document,),
                            )
                        )

            invoice_paid_factor = min(Decimal("1"), invoice_paid_factor)
            paid_amount += item_amount * invoice_paid_factor
            if invoice_cleared_nonpayment:
                pass
            elif invoice_open_factor > ZERO:
                open_amount += item_amount * invoice_open_factor
            elif invoice_paid_factor < Decimal("1"):
                open_amount += item_amount

        if received > ordered + EPSILON:
            findings.append(
                Finding(
                    "OVER_RECEIPT",
                    f"净收货数量 {received} 超过订单数量 {ordered}。",
                    "warning",
                    material_documents,
                )
            )
        if posted_quantity > received + EPSILON:
            findings.append(
                Finding(
                    "INVOICE_EXCEEDS_RECEIPT",
                    f"已过账发票数量 {posted_quantity} 超过净收货数量 {received}。",
                    "warning",
                    tuple(invoice_documents),
                )
            )
        if posted_quantity > ZERO and received <= EPSILON:
            findings.append(
                Finding(
                    "INVOICE_WITHOUT_GR",
                    "存在已过账发票，但当前项目没有有效净收货。",
                    "error",
                    tuple(invoice_documents),
                )
            )
        if _text(item, "ELIKZ").upper() == "X" and received + EPSILON < ordered:
            findings.append(
                Finding(
                    "SHORT_CLOSED_DELIVERY",
                    "项目已勾选交货完成，但累计收货少于订单数量。",
                    "warning",
                )
            )

        deleted = bool(_text(item, "LOEKZ"))
        if deleted:
            status = ItemStatus.CANCELLED
            explanation = f"项目删除标识为 {_text(item, 'LOEKZ')}，不再按正常 P2P 进度推进。"
        elif received <= EPSILON:
            status = ItemStatus.NOT_RECEIVED
            explanation = f"订单 {ordered} {_text(item, 'MEINS')}，尚无有效净收货。"
        elif received + EPSILON < ordered:
            status = ItemStatus.PARTIALLY_RECEIVED
            explanation = f"已收 {received}/{ordered} {_text(item, 'MEINS')}，仍有 {ordered - received} 待收。"
        elif posted_quantity <= EPSILON:
            status = ItemStatus.RECEIVED_NOT_INVOICED
            suffix = "；已发现未过账发票" if has_parked_invoice else ""
            explanation = f"已完成收货，但没有已过账的发票校验凭证{suffix}。"
        elif posted_quantity + EPSILON < received:
            status = ItemStatus.PARTIALLY_INVOICED
            explanation = f"已发票 {posted_quantity}/{received} {_text(item, 'MEINS')}，仍有收货数量未开票。"
        elif posted_amount > EPSILON and paid_amount + EPSILON >= posted_amount:
            status = ItemStatus.PAID
            explanation = f"发票金额 {posted_amount} {_text(header, 'WAERS')} 已由付款凭证清账。"
        elif paid_amount > EPSILON:
            status = ItemStatus.PARTIALLY_PAID
            explanation = (
                f"发票金额 {posted_amount} {_text(header, 'WAERS')} 中已付款 {paid_amount}，"
                f"未清 {open_amount}。"
            )
        else:
            status = ItemStatus.INVOICED_NOT_PAID
            if has_cleared_nonpayment:
                explanation = "发票已清账，但清账凭证不是已知付款类型，不能判定为已付款。"
            else:
                explanation = f"发票已过账，当前未确认付款；未清金额 {open_amount} {_text(header, 'WAERS')}。"

        return ItemStatusResult(
            item_number=item_number,
            material=_text(item, "MATNR"),
            description=_text(item, "TXZ01"),
            plant=_text(item, "WERKS"),
            ordered_quantity=ordered,
            received_quantity=received,
            invoiced_quantity=posted_quantity,
            unit=_text(item, "MEINS"),
            invoiced_amount=posted_amount,
            paid_amount=paid_amount,
            open_amount=open_amount,
            currency=_text(header, "WAERS"),
            status=status,
            explanation=explanation,
            findings=tuple(findings),
            documents=DocumentFlow(
                material_documents=material_documents,
                invoice_documents=_unique(invoice_documents),
                accounting_documents=_unique(accounting_documents),
                clearing_documents=_unique(clearing_documents),
            ),
        )
