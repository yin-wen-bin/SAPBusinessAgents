"""Domain models for month-end closing checks and reporting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
import calendar
from typing import Any


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return (Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL).index(self)


class ClosingConclusion(StrEnum):
    READY = "可关账"
    NOT_RECOMMENDED = "不建议关账"
    BLOCKED = "存在阻塞项"


class CheckStatus(StrEnum):
    PASSED = "passed"
    EXCEPTION = "exception"
    ERROR = "error"


@dataclass(frozen=True)
class DataSourceTrace:
    """Auditable record of a source used, skipped, or unavailable for one check."""

    provider: str
    status: str
    service_name: str = ""
    resource: str = ""
    case_ids: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "service_name": self.service_name or None,
            "resource": self.resource or None,
            "case_ids": list(self.case_ids),
            "artifacts": list(self.artifacts),
            "detail": self.detail or None,
        }


@dataclass(frozen=True)
class ClosingContext:
    company_code: str
    fiscal_year: int
    period: int

    def __post_init__(self) -> None:
        if not self.company_code or len(self.company_code) > 8:
            raise ValueError("company_code must contain 1 to 8 characters")
        if not 2000 <= self.fiscal_year <= 9999:
            raise ValueError("fiscal_year must be a four-digit year")
        if not 1 <= self.period <= 16:
            raise ValueError("period must be between 1 and 16")

    @property
    def period_key(self) -> str:
        return f"{self.fiscal_year}{self.period:02d}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_code": self.company_code,
            "fiscal_year": self.fiscal_year,
            "period": self.period,
        }


@dataclass(frozen=True)
class CheckDefinition:
    check_id: str
    name: str
    module: str
    handler: str
    metric_name: str
    operator: str
    threshold: Decimal
    severity: Severity
    blocking: bool
    owner_department: str
    owner: str
    remediation: str
    requires_human_confirmation: bool
    tcode: str
    tables: tuple[str, ...]


@dataclass(frozen=True)
class Checklist:
    checklist_id: str
    version: str
    description: str
    currency: str
    not_recommended_at: Severity
    checks: tuple[CheckDefinition, ...]


@dataclass(frozen=True)
class CheckObservation:
    value: Decimal
    amount: Decimal
    currency: str
    evidence: tuple[dict[str, Any], ...] = ()
    sources: tuple[DataSourceTrace, ...] = ()
    data_quality_issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckExecution:
    check_id: str
    name: str
    module: str
    status: CheckStatus
    actual_value: Decimal | None
    threshold: Decimal
    operator: str
    message: str
    sources: tuple[DataSourceTrace, ...] = ()
    data_quality_issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "name": self.name,
            "module": self.module,
            "status": self.status.value,
            "actual_value": _decimal(self.actual_value),
            "rule": {"operator": self.operator, "threshold": _decimal(self.threshold)},
            "message": self.message,
            "source_mode": source_mode(self.sources),
            "data_sources": [item.to_dict() for item in self.sources],
            "data_quality_issues": list(self.data_quality_issues),
        }


@dataclass(frozen=True)
class Finding:
    finding_id: str
    check_id: str
    title: str
    module: str
    severity: Severity
    blocking: bool
    owner_department: str
    owner: str
    amount: Decimal
    currency: str
    message: str
    remediation: str
    requires_human_confirmation: bool
    tcode: str
    tables: tuple[str, ...]
    evidence: tuple[dict[str, Any], ...] = ()
    data_sources: tuple[DataSourceTrace, ...] = ()
    data_quality_issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "check_id": self.check_id,
            "title": self.title,
            "module": self.module,
            "severity": self.severity.value,
            "blocking": self.blocking,
            "owner_department": self.owner_department,
            "owner": self.owner,
            "amount": _decimal(self.amount),
            "currency": self.currency,
            "message": self.message,
            "remediation": self.remediation,
            "requires_human_confirmation": self.requires_human_confirmation,
            "source": {
                "mode": source_mode(self.data_sources),
                "tcode": self.tcode,
                "tables": list(self.tables),
                "data_sources": [item.to_dict() for item in self.data_sources],
            },
            "data_quality_issues": list(self.data_quality_issues),
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class ClosingTodo:
    todo_id: str
    finding_id: str
    title: str
    module: str
    severity: Severity
    blocking: bool
    owner_department: str
    owner: str
    remediation: str
    status: str
    requires_human_confirmation: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "todo_id": self.todo_id,
            "finding_id": self.finding_id,
            "title": self.title,
            "module": self.module,
            "severity": self.severity.value,
            "blocking": self.blocking,
            "owner_department": self.owner_department,
            "owner": self.owner,
            "remediation": self.remediation,
            "status": self.status,
            "requires_human_confirmation": self.requires_human_confirmation,
        }


@dataclass(frozen=True)
class ClosingReport:
    report_id: str
    generated_at: datetime
    context: ClosingContext
    checklist_id: str
    checklist_version: str
    report_currency: str
    conclusion: ClosingConclusion
    conclusion_reason: str
    check_executions: tuple[CheckExecution, ...]
    findings: tuple[Finding, ...]
    todos: tuple[ClosingTodo, ...]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        timing = assessment_timing(self.context, self.generated_at)
        return {
            "report_id": self.report_id,
            "generated_at": self.generated_at.isoformat(),
            "scope": self.context.to_dict(),
            "checklist": {"id": self.checklist_id, "version": self.checklist_version},
            "report_currency": self.report_currency,
            "conclusion": self.conclusion.value,
            "conclusion_reason": self.conclusion_reason,
            "assessment_timing": timing,
            "safety": {
                "closing_action_executed": False,
                "closing_action_requires_human_confirmation": True,
                "message": "本助手仅检查、建议并生成待办，不自动执行 OB52、MMPV、过账或关账动作。",
            },
            "summary": self.summary,
            "checks": [item.to_dict() for item in self.check_executions],
            "findings": [item.to_dict() for item in self.findings],
            "todos": [item.to_dict() for item in self.todos],
        }


def _decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def source_mode(sources: tuple[DataSourceTrace, ...]) -> str:
    used = {item.provider for item in sources if item.status == "used"}
    unavailable = {item.provider for item in sources if item.status == "unavailable"}
    if "sapclaw_runtime_mcp" in used and "sap_se16n_export" in used:
        return "mcp+se16n"
    if "sap_se16n_export" in used and "sapclaw_runtime_mcp" in unavailable:
        return "se16n_fallback"
    if "sapclaw_runtime_mcp" in used:
        return "mcp"
    if "sap_se16n_export" in used:
        return "se16n"
    if "fixture" in used:
        return "fixture"
    return "unavailable"


def assessment_timing(context: ClosingContext, generated_at: datetime) -> dict[str, Any]:
    """Describe whether the report is a pre-close snapshot or a final-period run."""

    period_end: date | None = None
    if context.period <= 12:
        last_day = calendar.monthrange(context.fiscal_year, context.period)[1]
        period_end = date(context.fiscal_year, context.period, last_day)
    generated_date = generated_at.date()
    pre_close = period_end is not None and generated_date < period_end
    return {
        "phase": "pre_close_snapshot" if pre_close else "period_end_or_later",
        "generated_date": generated_date.isoformat(),
        "period_end": period_end.isoformat() if period_end else None,
        "is_pre_close_snapshot": pre_close,
        "final_close_certification": False if pre_close else None,
    }
