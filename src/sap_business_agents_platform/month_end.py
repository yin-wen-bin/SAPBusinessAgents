from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from jsonschema import Draft202012Validator

from .grir import RuleConfig, evaluate_odata_grir


JsonObject = dict[str, Any]
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = REPOSITORY_ROOT / ".local-data" / "config" / "month-end-closing" / "profiles.json"
PROFILE_SCHEMA_PATH = REPOSITORY_ROOT / "config" / "month-end-closing-profiles.schema.json"
CHECKLIST_PATH = REPOSITORY_ROOT / "agents" / "FI" / "month-end-closing" / "config" / "month_end_checklist.toml"

ALLOWED_EVIDENCE_SOURCES = frozenset(
    {
        "embedded_gl_line_items",
        "embedded_operational_due_items",
        "embedded_grir_chain",
        "embedded_billing_documents",
        "adt_asset_depreciation_status",
        "adt_fi_period_control",
        "adt_mm_period_status",
    }
)

CHECK_EVIDENCE_SOURCES: dict[str, tuple[str, ...]] = {
    "AP_OVERDUE_ITEMS": ("embedded_operational_due_items",),
    "AR_UNAPPLIED_RECEIPTS": ("embedded_gl_line_items",),
    "GL_UNRECONCILED_ITEMS": ("embedded_gl_line_items",),
    "MM_GRIR_AGED_ITEMS": ("embedded_grir_chain",),
    "MM_GRIR_ADJUSTMENTS_PENDING": ("embedded_grir_chain",),
    "AA_DEPRECIATION_PENDING": ("adt_asset_depreciation_status",),
    "GL_FX_VALUATION_PENDING": (),
    "GL_AUTO_CLEARING_PENDING": ("embedded_gl_line_items",),
    "GL_PERIOD_CONTROL_ISSUE": ("adt_fi_period_control",),
    "MM_PERIOD_CLOSE_PENDING": ("adt_mm_period_status",),
    "CO_UNALLOCATED_COSTS": ("embedded_gl_line_items",),
    "SD_BILLING_TRANSFER_ERRORS": ("embedded_billing_documents",),
}

ZERO_RESULT_SEMANTICS: dict[str, str] = {
    check_id: "passed_when_bounded_source_complete"
    for check_id in (
        "AP_OVERDUE_ITEMS",
        "AR_UNAPPLIED_RECEIPTS",
        "GL_UNRECONCILED_ITEMS",
        "MM_GRIR_AGED_ITEMS",
        "MM_GRIR_ADJUSTMENTS_PENDING",
        "GL_AUTO_CLEARING_PENDING",
        "CO_UNALLOCATED_COSTS",
        "SD_BILLING_TRANSFER_ERRORS",
    )
}


@dataclass(frozen=True, slots=True)
class MonthEndCheck:
    check_id: str
    name: str
    module: str
    metric_name: str
    severity: str
    blocking: bool
    owner_department: str
    owner: str
    remediation: str
    tcode: str
    tables: tuple[str, ...]


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _profile_path() -> Path:
    configured = os.getenv("SAPBA_MONTH_END_PROFILE_PATH", "").strip()
    if not configured:
        return DEFAULT_PROFILE_PATH
    path = Path(configured)
    return (path if path.is_absolute() else REPOSITORY_ROOT / path).resolve()


def _profile_registry() -> tuple[list[JsonObject], list[str]]:
    path = _profile_path()
    if not path.is_file():
        return [], ["month_end_profile_registry_missing"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        schema = json.loads(PROFILE_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], [f"month_end_profile_registry_invalid:{type(exc).__name__}"]
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda item: list(item.path))
    if errors:
        locations = ["/".join(str(part) for part in error.path) or "$" for error in errors[:5]]
        return [], [f"month_end_profile_schema_invalid:{location}" for location in locations]
    profiles = [dict(item) for item in payload.get("profiles", []) if isinstance(item, dict)]
    return profiles, []


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if text.startswith("/Date("):
        try:
            match = re.match(r"^/Date\((-?\d+)", text)
            if match is None:
                return None
            milliseconds = int(match.group(1))
            return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).date()
        except (ValueError, OverflowError):
            return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _decimal(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _text(row: Mapping[str, Any], *fields: str) -> str:
    for field in fields:
        value = row.get(field)
        if value not in {None, ""}:
            return str(value).strip()
    return ""


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "x", "yes"}:
        return True
    if text in {"false", "0", "", "no"} and value is not None:
        return False
    return None


def _rows(payload: Any, *step_ids: str) -> list[JsonObject]:
    if not isinstance(payload, dict):
        return []
    wanted = set(step_ids)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    step_results = payload.get("step_results")
    if not isinstance(step_results, dict):
        step_results = data.get("step_results") if isinstance(data, dict) else None
    found: list[JsonObject] = []
    matched = False
    if isinstance(step_results, dict):
        for step_id, result in step_results.items():
            if wanted and step_id not in wanted:
                continue
            if not isinstance(result, dict):
                continue
            matched = True
            found.extend(dict(row) for row in result.get("results", []) if isinstance(row, dict))
    if matched:
        return found
    for container in (data, payload):
        if isinstance(container, dict):
            found.extend(dict(row) for row in container.get("results", []) if isinstance(row, dict))
    return found


def _source_complete(payload: Any) -> bool:
    if not isinstance(payload, dict) or payload.get("ok") is False:
        return False
    flags: list[bool] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("source_complete"), bool):
                flags.append(value["source_complete"] and value.get("source_truncated") is not True)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return bool(flags) and all(flags)


def _adt_rows(payload: Any) -> list[JsonObject]:
    if not isinstance(payload, dict):
        return []
    rows = [dict(row) for row in payload.get("rows", []) if isinstance(row, dict)]
    for value in payload.values():
        if isinstance(value, dict):
            rows.extend(_adt_rows(value))
    return rows


def _adt_complete(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    completeness = payload.get("completeness")
    return bool(
        payload.get("status") == "complete"
        and payload.get("read_only") is True
        and payload.get("validated") is True
        and isinstance(completeness, dict)
        and completeness.get("source_complete") is True
        and completeness.get("paging_complete") is True
        and not payload.get("validation_issues")
    )


def _load_checks() -> tuple[MonthEndCheck, ...]:
    payload = tomllib.loads(CHECKLIST_PATH.read_text(encoding="utf-8"))
    return tuple(
        MonthEndCheck(
            check_id=str(item["id"]),
            name=str(item["name"]),
            module=str(item["module"]),
            metric_name=str(item["metric_name"]),
            severity=str(item["severity"]),
            blocking=bool(item["blocking"]),
            owner_department=str(item["owner_department"]),
            owner=str(item["owner"]),
            remediation=str(item["remediation"]),
            tcode=str(item["tcode"]),
            tables=tuple(str(value) for value in item.get("tables", [])),
        )
        for item in payload.get("checks", [])
    )


def _profile_for(run_input: JsonObject, as_of: date) -> tuple[JsonObject, list[str]]:
    profiles, gaps = _profile_registry()
    if gaps:
        return {}, gaps
    system_alias = os.getenv("SAPBA_SAP_SYSTEM_ALIAS", "default").strip() or "default"
    sap_client = os.getenv("SAP_CLIENT", "").strip()
    company_code = str(run_input.get("company_code") or "").strip()
    requested_id = str(run_input.get("profile_id") or "").strip()
    matches: list[JsonObject] = []
    for profile in profiles:
        if requested_id and str(profile.get("profile_id") or "") != requested_id:
            continue
        if str(profile.get("system_alias") or "default") != system_alias:
            continue
        configured_client = str(profile.get("sap_client") or "").strip()
        if configured_client and configured_client != sap_client:
            continue
        if str(profile.get("company_code") or "").strip() != company_code:
            continue
        effective_from = _as_date(profile.get("effective_from")) or date.min
        effective_to = _as_date(profile.get("effective_to")) or date.max
        if effective_from <= as_of <= effective_to:
            matches.append(profile)
    if not matches:
        return {}, ["month_end_profile_not_found"]
    if len(matches) > 1:
        return {}, ["month_end_profile_ambiguous"]
    profile = dict(matches[0])
    unknown_sources = sorted(set(profile.get("approved_evidence_sources", [])) - ALLOWED_EVIDENCE_SOURCES)
    if unknown_sources:
        return {}, [f"month_end_profile_source_not_approved:{source}" for source in unknown_sources]
    return profile, []


def _period_bounds(year: int, period: int, variant: str, profile: JsonObject) -> tuple[date | None, date | None]:
    if variant.upper() == "K4" and 1 <= period <= 12:
        return date(year, period, 1), date(year, period, calendar.monthrange(year, period)[1])
    key = f"{year}-{period:02d}"
    configured = (profile.get("period_boundaries") or {}).get(key)
    if not isinstance(configured, dict):
        return None, None
    return _as_date(configured.get("start")), _as_date(configured.get("end"))


def prepare_month_end_scope(inputs: JsonObject) -> JsonObject:
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    company_code = str(run_input.get("company_code") or "").strip()
    fiscal_year = int(run_input.get("fiscal_year"))
    period = int(run_input.get("period"))
    as_of = _as_date(run_input.get("as_of"))
    if as_of is None:
        raise ValueError("as_of must use YYYY-MM-DD")
    if as_of > date.today():
        raise ValueError("as_of must not be in the future")

    company_payload = inputs.get("company_evidence")
    ledger_payload = inputs.get("ledger_evidence")
    company_rows = [row for row in _rows(company_payload) if _text(row, "CompanyCode") == company_code]
    ledger_rows = _rows(ledger_payload)
    gaps: list[str] = []
    if not _source_complete(company_payload) or len(company_rows) != 1:
        gaps.append("company_metadata_incomplete")
    if not _source_complete(ledger_payload):
        gaps.append("ledger_metadata_incomplete")

    company = company_rows[0] if len(company_rows) == 1 else {}
    fiscal_variant = _text(company, "FiscalYearVariant")
    requested_ledger = str(run_input.get("ledger") or "").strip()
    available_ledgers = {_text(row, "Ledger") for row in ledger_rows if _text(row, "Ledger")}
    if requested_ledger:
        ledger = requested_ledger if requested_ledger in available_ledgers else ""
        if not ledger:
            gaps.append("requested_ledger_not_available")
    else:
        leading = sorted(
            {_text(row, "Ledger") for row in ledger_rows if _bool(row.get("IsLeadingLedger")) is True}
        )
        ledger = leading[0] if len(leading) == 1 else ""
        if not ledger:
            gaps.append("leading_ledger_not_unique")

    profile, profile_gaps = _profile_for(run_input, as_of)
    gaps.extend(profile_gaps)
    period_start, period_end = _period_bounds(fiscal_year, period, fiscal_variant, profile)
    if period_start is None or period_end is None:
        gaps.append("fiscal_period_boundaries_missing")
    approved_sources = set(profile.get("approved_evidence_sources", []))
    profile_hash = _canonical_hash(profile) if profile else ""
    metadata_complete = not any(
        gap in gaps
        for gap in {
            "company_metadata_incomplete",
            "ledger_metadata_incomplete",
            "requested_ledger_not_available",
            "leading_ledger_not_unique",
        }
    )
    query_ready = metadata_complete and bool(period_start and period_end)
    return {
        "rule_id": "prepare_month_end_scope_v1",
        "status": "complete" if query_ready else "inconclusive",
        "company_code": company_code,
        "fiscal_year": str(fiscal_year),
        "period": period,
        "period_text": str(period),
        "as_of": as_of.isoformat(),
        "period_start": period_start.isoformat() if period_start else "",
        "period_end": period_end.isoformat() if period_end else "",
        "currency": _text(company, "Currency"),
        "fiscal_year_variant": fiscal_variant,
        "ledger": ledger,
        "profile": profile,
        "profile_id": str(profile.get("profile_id") or ""),
        "profile_version": str(profile.get("version") or ""),
        "profile_hash": profile_hash,
        "config_gaps": sorted(set(gaps)),
        "metadata_complete": metadata_complete,
        "query_ready": query_ready,
        "grir_accounts": list((profile.get("accounts") or {}).get("gr_ir", [])),
        "grir_enabled": query_ready and bool((profile.get("accounts") or {}).get("gr_ir")),
        "skill_requirements": {
            "asset_depreciation": "adt_asset_depreciation_status" in approved_sources,
            "fi_period_control": "adt_fi_period_control" in approved_sources,
            "mm_period_status": "adt_mm_period_status" in approved_sources,
        },
    }


def resolve_month_end_skill_requirements(inputs: JsonObject) -> JsonObject:
    """Require both an approved profile source and an assessed API capability gap."""

    scope = inputs.get("scope") if isinstance(inputs.get("scope"), dict) else {}
    assessment = (
        inputs.get("assessment") if isinstance(inputs.get("assessment"), dict) else {}
    )
    approved = (
        scope.get("skill_requirements")
        if isinstance(scope.get("skill_requirements"), dict)
        else {}
    )
    needs_adt = (
        assessment.get("needs_adt")
        if isinstance(assessment.get("needs_adt"), dict)
        else {}
    )
    requirements = {
        topic: bool(approved.get(topic)) and bool(needs_adt.get(topic))
        for topic in (
            "asset_depreciation",
            "fi_period_control",
            "mm_period_status",
        )
    }
    return {
        "rule_id": "month_end_skill_requirements_v1",
        "status": "complete",
        **requirements,
        "approved_by_profile": {
            topic: bool(approved.get(topic)) for topic in requirements
        },
        "confirmed_api_capability_gap": {
            topic: bool(needs_adt.get(topic)) for topic in requirements
        },
    }


def _open_at(row: Mapping[str, Any], as_of: date) -> bool:
    posting = _as_date(row.get("PostingDate"))
    clearing = _as_date(row.get("ClearingDate"))
    return posting is not None and posting <= as_of and (clearing is None or clearing > as_of)


def _amount(row: Mapping[str, Any]) -> Decimal:
    value = _decimal(row.get("AmountInCompanyCodeCurrency")) or Decimal("0")
    return abs(value)


def _direction(row: Mapping[str, Any]) -> int:
    code = _text(row, "DebitCreditCode").upper()
    return 1 if code in {"S", "D"} else -1 if code in {"H", "C"} else 0


def _owner(spec: MonthEndCheck, profile: JsonObject) -> tuple[str, str, str]:
    override = (profile.get("owners") or {}).get(spec.check_id)
    if not isinstance(override, dict):
        return spec.owner_department, spec.owner, spec.remediation
    return (
        str(override.get("department") or spec.owner_department),
        str(override.get("owner") or spec.owner),
        str(override.get("remediation") or spec.remediation),
    )


def _result_for(
    spec: MonthEndCheck,
    *,
    status: str,
    actual: int | str | None,
    profile: JsonObject,
    detail_zh: str,
    detail_en: str,
    amount: Decimal = Decimal("0"),
    currency: str = "",
    finding_kind: str = "confirmed",
    evidence_refs: Sequence[str] = (),
    gaps: Sequence[str] = (),
) -> JsonObject:
    department, owner, remediation = _owner(spec, profile)
    thresholds = profile.get("thresholds") if isinstance(profile.get("thresholds"), dict) else {}
    threshold = {
        "MM_GRIR_AGED_ITEMS": f"> {thresholds.get('grir_age_days', 'unconfigured')} days",
        "MM_GRIR_ADJUSTMENTS_PENDING": (
            f"quantity>{thresholds.get('grir_quantity_tolerance', 'unconfigured')} or "
            f"amount>{thresholds.get('grir_amount_tolerance', 'unconfigured')}"
        ),
        "GL_AUTO_CLEARING_PENDING": f"±{thresholds.get('auto_clearing_tolerance', 'unconfigured')}",
    }.get(spec.check_id, "0 exceptions")
    return {
        "check_id": spec.check_id,
        "name": {"zh": spec.name, "en": spec.check_id.replace("_", " ").title()},
        "module": spec.module,
        "status": status,
        "finding_kind": finding_kind if status == "attention" else "",
        "severity": spec.severity,
        "blocking": spec.blocking,
        "metric_name": spec.metric_name,
        "actual_value": actual,
        "threshold": threshold,
        "amount": format(amount, "f"),
        "currency": currency,
        "detail": {"zh": detail_zh, "en": detail_en},
        "owner_department": department,
        "owner": owner,
        "remediation": remediation,
        "requires_human_confirmation": True,
        "tcode": spec.tcode,
        "tables": list(spec.tables),
        "evidence_refs": list(evidence_refs),
        "evidence_sources": list(CHECK_EVIDENCE_SOURCES.get(spec.check_id, ())),
        "zero_result_semantics": ZERO_RESULT_SEMANTICS.get(
            spec.check_id, "status_source_required"
        ),
        "evidence_gaps": sorted(set(str(gap) for gap in gaps if str(gap))),
    }


def _auto_clearing_candidates(rows: Sequence[JsonObject], accounts: set[str], tolerance: Decimal) -> int:
    grouped: dict[tuple[str, str], list[Decimal]] = {}
    for row in rows:
        account = _text(row, "GLAccount")
        reference = _text(row, "AssignmentReference")
        amount = _decimal(row.get("AmountInCompanyCodeCurrency"))
        direction = _direction(row)
        if account not in accounts or not reference or amount is None or direction == 0:
            continue
        grouped.setdefault((account, reference), []).append(abs(amount) * direction)
    count = 0
    for amounts in grouped.values():
        remaining = list(amounts)
        while remaining:
            value = remaining.pop(0)
            match = next((index for index, other in enumerate(remaining) if abs(value + other) <= tolerance), None)
            if match is not None:
                count += 1
                remaining.pop(match)
    return count


def _taba_complete(rows: Sequence[JsonObject], fiscal_year: int, period: int) -> bool:
    for row in rows:
        row_year = _text(row, "AFBLGJ", "GJAHR")
        row_period = _text(row, "AFBLPE", "POPER")
        try:
            if int(row_year) == fiscal_year and int(row_period) >= period:
                return True
        except ValueError:
            continue
    return False


def _mm_period_complete(rows: Sequence[JsonObject], fiscal_year: int, period: int) -> bool:
    for row in rows:
        try:
            current = (int(_text(row, "LFGJA")), int(_text(row, "LFMON")))
        except ValueError:
            continue
        if current > (fiscal_year, period):
            return True
    return False


def _period_control_open(rows: Sequence[JsonObject], fiscal_year: int, period: int) -> bool | None:
    if not rows:
        return None
    understood = False
    for row in rows:
        for suffix in ("1", "2", "3"):
            try:
                from_year = int(_text(row, f"FRYE{suffix}"))
                from_period = int(_text(row, f"FRPE{suffix}"))
                to_year = int(_text(row, f"TOYE{suffix}"))
                to_period = int(_text(row, f"TOPE{suffix}"))
            except ValueError:
                continue
            understood = True
            if (from_year, from_period) <= (fiscal_year, period) <= (to_year, to_period):
                return True
    return False if understood else None


def evaluate_month_end_closing(inputs: JsonObject) -> JsonObject:
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    scope = inputs.get("scope") if isinstance(inputs.get("scope"), dict) else {}
    evidence = inputs.get("evidence") if isinstance(inputs.get("evidence"), dict) else {}
    fallbacks = inputs.get("fallbacks") if isinstance(inputs.get("fallbacks"), dict) else {}
    profile = scope.get("profile") if isinstance(scope.get("profile"), dict) else {}
    as_of = _as_date(scope.get("as_of") or run_input.get("as_of")) or date.today()
    period_end = _as_date(scope.get("period_end"))
    fiscal_year = int(scope.get("fiscal_year") or run_input.get("fiscal_year"))
    period = int(scope.get("period") or run_input.get("period"))
    currency = str(scope.get("currency") or "")
    checks = {item.check_id: item for item in _load_checks()}

    gl_payload = evidence.get("gl_line_items")
    due_payload = evidence.get("due_items")
    billing_payload = evidence.get("billing_documents")
    grir_payload = evidence.get("grir_chain")
    gl_rows = _rows(gl_payload, "gl_line_items")
    due_rows = _rows(due_payload, "due_items")
    billing_rows = _rows(billing_payload, "billing_documents")
    gl_complete = _source_complete(gl_payload)
    due_complete = _source_complete(due_payload)
    billing_complete = _source_complete(billing_payload)
    grir_complete = _source_complete(grir_payload)
    results: list[JsonObject] = []
    global_gaps = list(scope.get("config_gaps") or [])

    supplier_rows = [row for row in due_rows if _text(row, "Supplier")]
    due_missing = [row for row in supplier_rows if _open_at(row, as_of) and _as_date(row.get("NetDueDate")) is None]
    overdue = [
        row for row in supplier_rows
        if _open_at(row, as_of) and (_as_date(row.get("NetDueDate")) or date.max) <= as_of
    ]
    spec = checks["AP_OVERDUE_ITEMS"]
    if not due_complete or due_missing:
        results.append(_result_for(spec, status="not_assessed", actual=None, profile=profile, detail_zh="供应商行项目来源不完整或缺少 SAP 净到期日。", detail_en="Supplier line-item evidence is incomplete or SAP net due dates are missing.", currency=currency, gaps=["ap_due_date_evidence_incomplete"]))
    else:
        amount = sum((_amount(row) for row in overdue), Decimal("0"))
        results.append(_result_for(spec, status="attention" if overdue else "passed", actual=len(overdue), profile=profile, detail_zh=f"发现 {len(overdue)} 条截至基准日逾期未清供应商项目。", detail_en=f"Found {len(overdue)} overdue supplier open item(s) as of the assessment date.", amount=amount, currency=currency, evidence_refs=[_text(row, "AccountingDocument") for row in overdue[:50]]))

    incoming_types = set((profile.get("document_types") or {}).get("incoming_payments", []))
    spec = checks["AR_UNAPPLIED_RECEIPTS"]
    if not gl_complete or not incoming_types:
        results.append(_result_for(spec, status="not_assessed", actual=None, profile=profile, detail_zh="缺少完整客户行项目或公司收款凭证类型配置。", detail_en="Complete customer items or the company incoming-payment document-type configuration is missing.", gaps=["ar_incoming_payment_configuration_missing"]))
    else:
        candidates = [row for row in gl_rows if _text(row, "Customer") and _text(row, "AccountingDocumentType") in incoming_types and _open_at(row, as_of)]
        results.append(_result_for(spec, status="attention" if candidates else "passed", actual=len(candidates), profile=profile, detail_zh=f"识别 {len(candidates)} 条未清收款候选，需人工核对认领关系。", detail_en=f"Identified {len(candidates)} open receipt candidate(s) for manual assignment review.", amount=sum((_amount(row) for row in candidates), Decimal("0")), currency=currency, finding_kind="candidate", evidence_refs=[_text(row, "AccountingDocument") for row in candidates[:50]]))

    open_item_accounts = set((profile.get("accounts") or {}).get("open_item_gl", []))
    spec = checks["GL_UNRECONCILED_ITEMS"]
    if not gl_complete or not open_item_accounts:
        results.append(_result_for(spec, status="not_assessed", actual=None, profile=profile, detail_zh="缺少完整总账行项目或未清项科目配置。", detail_en="Complete G/L items or the open-item account configuration is missing.", gaps=["gl_open_item_account_configuration_missing"]))
    else:
        candidates = [row for row in gl_rows if _text(row, "GLAccount") in open_item_accounts and _open_at(row, as_of)]
        results.append(_result_for(spec, status="attention" if candidates else "passed", actual=len(candidates), profile=profile, detail_zh=f"发现 {len(candidates)} 条配置科目范围内的未清明细。", detail_en=f"Found {len(candidates)} open line item(s) in the configured account scope.", amount=sum((_amount(row) for row in candidates), Decimal("0")), currency=currency, finding_kind="candidate"))

    grir_analysis: JsonObject = {}
    if grir_complete:
        step_results = ((grir_payload or {}).get("data") or {}).get("step_results") if isinstance(grir_payload, dict) else {}
        step_results = step_results if isinstance(step_results, dict) else {}
        incomplete_steps = [name for name, value in step_results.items() if not isinstance(value, dict) or value.get("source_complete") is not True or value.get("source_truncated") is True]
        thresholds = profile.get("thresholds") if isinstance(profile.get("thresholds"), dict) else {}
        grir_analysis = evaluate_odata_grir(
            analysis_date=as_of,
            po_items=_rows(grir_payload, "purchase_order_items"),
            material_documents=_rows(grir_payload, "material_documents"),
            material_document_headers=_rows(grir_payload, "material_document_headers"),
            supplier_invoice_items=_rows(grir_payload, "supplier_invoice_items"),
            supplier_invoice_headers=_rows(grir_payload, "supplier_invoice_headers"),
            gl_items=_rows(grir_payload, "grir_gl_history"),
            candidate_gl_items=_rows(grir_payload, "gl_items"),
            source_complete=not incomplete_steps,
            incomplete_steps=incomplete_steps,
            config=RuleConfig(
                quantity_tolerance=Decimal(str(thresholds.get("grir_quantity_tolerance", "0.001"))),
                amount_tolerance=Decimal(str(thresholds.get("grir_amount_tolerance", "0.01"))),
                long_outstanding_days=int(thresholds.get("grir_age_days", 90)),
                high_severity_days=int(thresholds.get("grir_high_severity_days", 180)),
            ),
        )
    aged_actions = [row for row in grir_analysis.get("action_records", []) if "long_outstanding" in (row.get("reason_codes") or [])]
    for check_id, rows_for_check, candidate in (
        ("MM_GRIR_AGED_ITEMS", aged_actions, False),
        ("MM_GRIR_ADJUSTMENTS_PENDING", grir_analysis.get("action_records", []), True),
    ):
        spec = checks[check_id]
        if not grir_complete or not grir_analysis.get("evidence_complete"):
            results.append(_result_for(spec, status="not_assessed", actual=None, profile=profile, detail_zh="GR/IR 跨 API 凭证链不完整。", detail_en="The cross-API GR/IR document chain is incomplete.", gaps=grir_analysis.get("evidence_gaps", ["grir_chain_incomplete"])))
        else:
            rows_list = list(rows_for_check)
            results.append(_result_for(spec, status="attention" if rows_list else "passed", actual=len(rows_list), profile=profile, detail_zh=f"发现 {len(rows_list)} 条 GR/IR {'调整候选' if candidate else '长账龄差异'}。", detail_en=f"Found {len(rows_list)} GR/IR {'adjustment candidate(s)' if candidate else 'aged difference(s)'}.", amount=sum((abs(_decimal(row.get("gr_ir_open_amount")) or Decimal("0")) for row in rows_list), Decimal("0")), currency=currency, finding_kind="candidate" if candidate else "confirmed"))

    aa_payload = fallbacks.get("asset_depreciation")
    spec = checks["AA_DEPRECIATION_PENDING"]
    if not _adt_complete(aa_payload):
        results.append(_result_for(spec, status="not_assessed", actual=None, profile=profile, detail_zh="没有完整、已验证的折旧运行状态证据；不会根据缺少折旧凭证推断运行状态。", detail_en="Complete validated depreciation-run status evidence is unavailable; missing postings are not used to infer run status.", gaps=["asset_depreciation_status_unavailable"]))
    else:
        completed = _taba_complete(_adt_rows(aa_payload), fiscal_year, period)
        results.append(_result_for(spec, status="passed" if completed else "attention", actual=0 if completed else 1, profile=profile, detail_zh="折旧状态源显示目标期间已覆盖。" if completed else "折旧状态源未显示目标期间已完成。", detail_en="The depreciation status source covers the target period." if completed else "The depreciation status source does not show the target period as complete."))

    spec = checks["GL_FX_VALUATION_PENDING"]
    results.append(_result_for(spec, status="not_assessed", actual=None, profile=profile, detail_zh="目标系统尚未配置经过审核的外币估值运行状态源；凭证类型不能证明运行完整。", detail_en="No reviewed foreign-currency valuation run-status source is configured for the target system; document types do not prove run completeness.", gaps=["fx_valuation_run_status_source_unavailable"]))

    auto_accounts = set((profile.get("accounts") or {}).get("auto_clearing", []))
    tolerance = Decimal(str((profile.get("thresholds") or {}).get("auto_clearing_tolerance", "0.01")))
    spec = checks["GL_AUTO_CLEARING_PENDING"]
    if not gl_complete or not auto_accounts:
        results.append(_result_for(spec, status="not_assessed", actual=None, profile=profile, detail_zh="缺少完整总账行项目或自动清账科目配置。", detail_en="Complete G/L items or the automatic-clearing account configuration is missing.", gaps=["auto_clearing_configuration_missing"]))
    else:
        candidate_count = _auto_clearing_candidates(gl_rows, auto_accounts, tolerance)
        results.append(_result_for(spec, status="attention" if candidate_count else "passed", actual=candidate_count, profile=profile, detail_zh=f"识别 {candidate_count} 组自动清账候选；这不表示 F.13 已运行。", detail_en=f"Identified {candidate_count} automatic-clearing candidate pair(s); this does not indicate that F.13 ran.", finding_kind="candidate"))

    period_payload = fallbacks.get("fi_period_control")
    spec = checks["GL_PERIOD_CONTROL_ISSUE"]
    if as_of != date.today():
        results.append(_result_for(spec, status="not_assessed", actual=None, profile=profile, detail_zh="T001B 只反映当前期间控制状态，不能证明历史基准日的开放状态。", detail_en="T001B reflects current posting-period control and cannot prove the state at a historical as-of date.", gaps=["fi_period_control_historical_state_unavailable"]))
    elif not _adt_complete(period_payload):
        results.append(_result_for(spec, status="not_assessed", actual=None, profile=profile, detail_zh="缺少完整、已验证的 FI 期间控制证据。", detail_en="Complete validated FI posting-period control evidence is unavailable.", gaps=["fi_period_control_unavailable"]))
    else:
        open_state = _period_control_open(_adt_rows(period_payload), fiscal_year, period)
        if open_state is None:
            results.append(_result_for(spec, status="not_assessed", actual=None, profile=profile, detail_zh="期间状态字段无法可靠解释。", detail_en="The posting-period status fields could not be interpreted reliably.", gaps=["fi_period_control_semantics_unresolved"]))
        else:
            should_be_closed = period_end is not None and as_of >= period_end
            issue = should_be_closed and open_state
            results.append(_result_for(spec, status="attention" if issue else "passed", actual=1 if issue else 0, profile=profile, detail_zh="目标期间在期末后仍处于开放范围。" if issue else "期间控制状态与当前评估阶段一致。", detail_en="The target period remains open after period end." if issue else "Posting-period control is consistent with the current assessment phase."))

    mm_payload = fallbacks.get("mm_period_status")
    spec = checks["MM_PERIOD_CLOSE_PENDING"]
    if as_of != date.today():
        results.append(_result_for(spec, status="not_assessed", actual=None, profile=profile, detail_zh="MARV 只反映当前 MM 期间状态，不能证明历史基准日的状态。", detail_en="MARV reflects the current MM period and cannot prove the state at a historical as-of date.", gaps=["mm_period_historical_state_unavailable"]))
    elif period > 12 or not _adt_complete(mm_payload):
        results.append(_result_for(spec, status="not_assessed", actual=None, profile=profile, detail_zh="MM 期间状态证据不可用或目标为特殊期间。", detail_en="MM period-status evidence is unavailable or the target is a special period.", gaps=["mm_period_status_unavailable"]))
    else:
        completed = _mm_period_complete(_adt_rows(mm_payload), fiscal_year, period)
        results.append(_result_for(spec, status="passed" if completed else "attention", actual=0 if completed else 1, profile=profile, detail_zh="MM 当前期间已晚于目标期间。" if completed else "MM 当前期间尚未晚于目标期间。", detail_en="The current MM period is later than the target period." if completed else "The current MM period has not advanced beyond the target period."))

    co_cost_centers = set((profile.get("co") or {}).get("cost_centers", []))
    spec = checks["CO_UNALLOCATED_COSTS"]
    if not gl_complete or not co_cost_centers:
        results.append(_result_for(spec, status="not_assessed", actual=None, profile=profile, detail_zh="缺少完整 CO 行项目或成本中心范围配置。", detail_en="Complete CO line items or the cost-center scope configuration is missing.", gaps=["co_cost_scope_configuration_missing"]))
    else:
        candidates = [row for row in gl_rows if _text(row, "CostCenter") in co_cost_centers and not _text(row, "OrderID", "PartnerCostCenter") and _amount(row) > 0]
        results.append(_result_for(spec, status="attention" if candidates else "passed", actual=len(candidates), profile=profile, detail_zh=f"识别 {len(candidates)} 条需要复核分配状态的成本项目。", detail_en=f"Identified {len(candidates)} cost item(s) whose allocation status needs review.", amount=sum((_amount(row) for row in candidates), Decimal("0")), currency=currency, finding_kind="candidate"))

    transfer_errors = set((profile.get("status_mappings") or {}).get("billing_transfer_errors", []))
    posting_errors = set((profile.get("status_mappings") or {}).get("billing_posting_errors", []))
    spec = checks["SD_BILLING_TRANSFER_ERRORS"]
    if not billing_complete or not (transfer_errors or posting_errors):
        results.append(_result_for(spec, status="not_assessed", actual=None, profile=profile, detail_zh="缺少完整开票数据或公司状态代码映射。", detail_en="Complete billing data or the company status-code mapping is missing.", gaps=["billing_status_mapping_missing"]))
    else:
        errors = [row for row in billing_rows if _bool(row.get("BillingDocumentIsCancelled")) is not True and (_text(row, "AccountingTransferStatus") in transfer_errors or _text(row, "AccountingPostingStatus") in posting_errors)]
        results.append(_result_for(spec, status="attention" if errors else "passed", actual=len(errors), profile=profile, detail_zh=f"发现 {len(errors)} 张开票凭证存在配置定义的会计传输或过账异常。", detail_en=f"Found {len(errors)} billing document(s) with configured accounting-transfer or posting errors.", amount=sum((abs(_decimal(row.get("TotalNetAmount")) or Decimal("0")) for row in errors), Decimal("0")), currency=currency, evidence_refs=[_text(row, "BillingDocument") for row in errors[:50]]))

    results.sort(key=lambda item: list(checks).index(item["check_id"]))
    missing_evidence = sorted({gap for item in results for gap in item.get("evidence_gaps", [])} | set(global_gaps))
    source_complete = bool(scope.get("metadata_complete")) and gl_complete and due_complete and billing_complete and (grir_complete if scope.get("grir_enabled") else False)
    checklist_complete = len(results) == len(checks) and all(item["status"] in {"passed", "attention"} for item in results)
    evidence_complete = source_complete and checklist_complete and not missing_evidence
    attention = [item for item in results if item["status"] == "attention"]
    business_status = (
        "inconclusive"
        if not evidence_complete
        else "action_required"
        if attention
        else "in_progress"
        if period_end is not None and as_of < period_end
        else "ready_for_review"
    )

    findings: list[JsonObject] = []
    todos: list[JsonObject] = []
    for index, item in enumerate(attention, start=1):
        finding_id = f"ME-{scope.get('company_code')}-{fiscal_year}{period:02d}-{item['check_id']}"
        findings.append({
            "finding_id": finding_id,
            "check_id": item["check_id"],
            "title": item["name"],
            "finding_kind": item["finding_kind"],
            "severity": item["severity"],
            "blocking": item["blocking"],
            "amount": item["amount"],
            "currency": item["currency"],
            "detail": item["detail"],
            "owner_department": item["owner_department"],
            "owner": item["owner"],
            "evidence_refs": item["evidence_refs"],
        })
        todos.append({
            "todo_id": f"TODO-{index:03d}",
            "finding_id": finding_id,
            "check_id": item["check_id"],
            "priority": item["severity"],
            "owner_department": item["owner_department"],
            "owner": item["owner"],
            "recommended_action": item["remediation"],
            "requires_human_confirmation": True,
        })

    ap_suppliers = sorted({_text(row, "Supplier") for row in overdue if _text(row, "Supplier")})
    ap_follow_ups = [
        {
            "company_code": scope.get("company_code"),
            "supplier": supplier,
            "as_of": as_of.isoformat(),
            "finding_ids": [
                f"ME-{scope.get('company_code')}-{fiscal_year}{period:02d}-AP_OVERDUE_ITEMS"
            ],
            "evidence_refs": sorted(
                {
                    _text(row, "AccountingDocument")
                    for row in overdue
                    if _text(row, "Supplier") == supplier
                    and _text(row, "AccountingDocument")
                }
            )[:50],
        }
        for supplier in ap_suppliers[:50]
    ]
    grir_accounts = list(scope.get("grir_accounts") or [])
    grir_evidence_refs = next(
        (
            list(item.get("evidence_refs") or [])
            for item in results
            if item.get("check_id") == "MM_GRIR_AGED_ITEMS"
        ),
        [],
    )
    grir_follow_ups = [
        {
            "company_code": scope.get("company_code"),
            "fiscal_year": str(fiscal_year),
            "date_from": scope.get("period_start"),
            "date_to": as_of.isoformat(),
            "gl_account": account,
            "finding_ids": [
                f"ME-{scope.get('company_code')}-{fiscal_year}{period:02d}-MM_GRIR_AGED_ITEMS"
            ],
            "evidence_refs": grir_evidence_refs[:50],
        }
        for account in grir_accounts[:50]
        if aged_actions
    ]
    unsupported = [{"check_id": item["check_id"], "reason": "no_validated_specialist_agent"} for item in attention if item["check_id"] in {"AA_DEPRECIATION_PENDING", "GL_FX_VALUATION_PENDING", "GL_PERIOD_CONTROL_ISSUE", "MM_PERIOD_CLOSE_PENDING", "CO_UNALLOCATED_COSTS"}]
    follow_up_complete = len(ap_suppliers) <= 50 and len(grir_accounts) <= 50

    counts = {status: sum(1 for item in results if item["status"] == status) for status in ("passed", "attention", "not_assessed", "error")}
    headline = {
        "ready_for_review": {"zh": "月结检查已完成，可提交人工关账复核", "en": "Month-end checks are complete and ready for human closing review"},
        "in_progress": {"zh": "月结检查当前无异常，但仍处于期间进行中", "en": "No exception is currently identified, but the period is still in progress"},
        "action_required": {"zh": f"月结前有 {len(attention)} 项需要处理或确认", "en": f"{len(attention)} item(s) require action or confirmation before close"},
        "inconclusive": {"zh": "月结证据不完整，暂不能判断准备度", "en": "Month-end evidence is incomplete, so readiness cannot be determined"},
    }[business_status]
    scope_output = {key: scope.get(key) for key in ("company_code", "fiscal_year", "period", "as_of", "period_start", "period_end", "currency", "fiscal_year_variant", "ledger", "profile_id", "profile_version", "profile_hash")}
    report = {
        "tone": "success" if business_status == "ready_for_review" else "warning" if business_status == "action_required" else "info",
        "headline": headline,
        "overview": {"zh": "本报告只提供只读准备度建议；任何关账、过账、清账或期间操作都必须由授权人员确认。", "en": "This report is a read-only readiness recommendation; every closing, posting, clearing, or period action requires authorized human confirmation."},
        "summary": headline,
        "scope": scope_output,
        "provider": {"id": "embedded-odata", "capability": "sap_read.v2", "read_only": True, "automatic_fallback": False},
        "source_complete": source_complete,
        "checklist_complete": checklist_complete,
        "evidence_complete": evidence_complete,
        "stages": [{"id": item["check_id"], "label": item["name"], "state": item["status"], "detail": item["detail"]} for item in results],
        "metrics": [
            {"id": "checks_total", "label": {"zh": "检查总数", "en": "Total checks"}, "value": len(results)},
            *[{"id": f"checks_{status}", "label": {"zh": {"passed": "通过", "attention": "需处理", "not_assessed": "未评估", "error": "错误"}[status], "en": status.replace("_", " ").title()}, "value": value} for status, value in counts.items()],
        ],
        "findings": findings,
        "missing_evidence": missing_evidence,
        "next_actions": {"zh": [todo["recommended_action"] for todo in todos] or ["保留本报告并由月结负责人完成人工复核。"], "en": [todo["recommended_action"] for todo in todos] or ["Retain this report and complete human review by the closing owner."]},
        "records": results,
        "record_columns": [
            {"key": "check_id", "label": {"zh": "检查", "en": "Check"}},
            {"key": "module", "label": {"zh": "模块", "en": "Module"}},
            {"key": "status", "label": {"zh": "状态", "en": "Status"}},
            {"key": "actual_value", "label": {"zh": "结果", "en": "Result"}},
            {"key": "owner_department", "label": {"zh": "责任部门", "en": "Owner"}},
        ],
        "action_tables": [
            {"id": "month_end_checks", "title": {"zh": "12 项月结检查", "en": "Twelve month-end checks"}, "artifact_name": "month-end-checks.csv", "rows": results},
            {"id": "month_end_findings", "title": {"zh": "月结异常", "en": "Month-end findings"}, "artifact_name": "findings.csv", "rows": findings},
            {"id": "month_end_todos", "title": {"zh": "责任待办", "en": "Owner actions"}, "artifact_name": "todos.csv", "rows": todos},
        ],
        "json_artifacts": [{"artifact_name": "follow-up-scopes.json", "payload": {"gr_ir": grir_follow_ups, "ap": ap_follow_ups, "unsupported": unsupported, "complete": follow_up_complete}}],
        "safety": {"closing_action_executed": False, "closing_action_requires_human_confirmation": True},
    }
    workflow_output = {
        **run_input,
        "scope": scope_output,
        "business_status": business_status,
        "source_complete": source_complete,
        "checklist_complete": checklist_complete,
        "evidence_complete": evidence_complete,
        "check_results": results,
        "findings": findings,
        "todos": todos,
        "missing_evidence": missing_evidence,
        "gr_ir_follow_up_scopes": grir_follow_ups,
        "ap_follow_up_scopes": ap_follow_ups,
        "unsupported_follow_ups": unsupported,
        "follow_up_scope_complete": follow_up_complete,
        "business_report": report,
    }
    return {
        "rule_id": "month_end_closing_deterministic_v2",
        "status": "complete" if evidence_complete else "inconclusive",
        "business_status": business_status,
        "business_complete": evidence_complete,
        "source_complete": source_complete,
        "checklist_complete": checklist_complete,
        "evidence_complete": evidence_complete,
        "missing_evidence": missing_evidence,
        "findings": findings,
        "metrics": report["metrics"],
        "business_report": report,
        "reason": report["overview"],
        "summary": headline,
        "workflow_output": workflow_output,
    }
