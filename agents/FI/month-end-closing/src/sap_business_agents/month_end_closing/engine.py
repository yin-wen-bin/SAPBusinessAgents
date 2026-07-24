"""Closing assessment orchestration, decisioning, and todo generation."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
import calendar
from typing import Callable

from .checkers import ThresholdChecker, default_checkers
from .gateway import SapDataUnavailable, SapGateway
from .models import (
    CheckExecution,
    CheckStatus,
    Checklist,
    ClosingConclusion,
    ClosingContext,
    ClosingReport,
    ClosingTodo,
    Finding,
    Severity,
    source_mode,
)


class MonthEndClosingAssistant:
    def __init__(
        self,
        checklist: Checklist,
        gateway: SapGateway,
        checkers: dict[str, ThresholdChecker] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._checklist = checklist
        self._gateway = gateway
        self._checkers = default_checkers() if checkers is None else checkers
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        missing = sorted({item.handler for item in checklist.checks} - self._checkers.keys())
        if missing:
            raise ValueError(f"no checker registered for handlers: {', '.join(missing)}")

    def assess(self, context: ClosingContext) -> ClosingReport:
        generated_at = self._clock()
        findings: list[Finding] = []
        executions: list[CheckExecution] = []

        scope_error: SapDataUnavailable | None = None
        try:
            report_currency = self._gateway.report_currency(context)
            if not report_currency.strip():
                raise SapDataUnavailable("report currency is empty")
        except SapDataUnavailable as exc:
            scope_error = exc
            report_currency = "UNRESOLVED"

        for definition in self._checklist.checks:
            try:
                if scope_error is not None:
                    raise SapDataUnavailable(f"scope validation failed: {scope_error}")
                observation = self._gateway.collect(context, definition)
                if observation.currency != report_currency:
                    raise SapDataUnavailable(
                        f"{definition.check_id} currency {observation.currency} is not normalized "
                        f"to report currency {report_currency}",
                        observation.sources,
                    )
                if observation.data_quality_issues:
                    raise SapDataUnavailable(
                        "observation failed data-quality checks: "
                        + "; ".join(observation.data_quality_issues),
                        observation.sources,
                    )
                evaluation = self._checkers[definition.handler].evaluate(definition, observation)
                status = CheckStatus.EXCEPTION if evaluation.is_exception else CheckStatus.PASSED
                executions.append(
                    CheckExecution(
                        definition.check_id,
                        definition.name,
                        definition.module,
                        status,
                        observation.value,
                        definition.threshold,
                        definition.operator,
                        evaluation.message,
                        observation.sources,
                        observation.data_quality_issues,
                    )
                )
                if evaluation.is_exception:
                    findings.append(
                        Finding(
                            finding_id=f"FND-{context.company_code}-{context.period_key}-{definition.check_id}",
                            check_id=definition.check_id,
                            title=definition.name,
                            module=definition.module,
                            severity=definition.severity,
                            blocking=definition.blocking,
                            owner_department=definition.owner_department,
                            owner=definition.owner,
                            amount=observation.amount,
                            currency=observation.currency,
                            message=evaluation.message,
                            remediation=definition.remediation,
                            requires_human_confirmation=definition.requires_human_confirmation,
                            tcode=definition.tcode,
                            tables=definition.tables,
                            evidence=observation.evidence,
                            data_sources=observation.sources,
                            data_quality_issues=observation.data_quality_issues,
                        )
                    )
            except SapDataUnavailable as exc:  # Missing SAP data must fail closed, never become a false pass.
                message = f"required SAP data unavailable: {type(exc).__name__}: {exc}"
                executions.append(
                    CheckExecution(
                        definition.check_id,
                        definition.name,
                        definition.module,
                        CheckStatus.ERROR,
                        None,
                        definition.threshold,
                        definition.operator,
                        message,
                        exc.sources,
                    )
                )
                findings.append(
                    Finding(
                        finding_id=f"FND-{context.company_code}-{context.period_key}-{definition.check_id}-DATA",
                        check_id=definition.check_id,
                        title=f"数据不可用：{definition.name}",
                        module=definition.module,
                        severity=Severity.CRITICAL,
                        blocking=True,
                        owner_department=definition.owner_department,
                        owner=definition.owner,
                        amount=Decimal("0"),
                        currency=report_currency,
                        message=message,
                        remediation=(
                            "由业务责任团队与 Finance Systems / SAP Support 协同恢复只读数据、"
                            "确认检查口径并重新运行；在此之前不得据此报告执行关账。"
                        ),
                        requires_human_confirmation=False,
                        tcode=definition.tcode,
                        tables=definition.tables,
                        data_sources=exc.sources,
                    )
                )

        conclusion, reason = self._decide(findings, context, generated_at)
        todos = tuple(self._make_todo(context, finding) for finding in findings)
        return ClosingReport(
            report_id=f"MEC-{context.company_code}-{context.period_key}",
            generated_at=generated_at,
            context=context,
            checklist_id=self._checklist.checklist_id,
            checklist_version=self._checklist.version,
            report_currency=report_currency,
            conclusion=conclusion,
            conclusion_reason=reason,
            check_executions=tuple(executions),
            findings=tuple(findings),
            todos=todos,
            summary=_summarize(executions, findings, report_currency),
        )

    def _decide(
        self,
        findings: list[Finding],
        context: ClosingContext,
        generated_at: datetime,
    ) -> tuple[ClosingConclusion, str]:
        blockers = [item for item in findings if item.blocking]
        if blockers:
            return ClosingConclusion.BLOCKED, f"发现 {len(blockers)} 个阻塞项，必须完成并复核后再关账。"
        material = [
            item for item in findings if item.severity.rank >= self._checklist.not_recommended_at.rank
        ]
        if material:
            return (
                ClosingConclusion.NOT_RECOMMENDED,
                f"无硬阻塞，但有 {len(material)} 个达到 {self._checklist.not_recommended_at.value} 或以上的异常。",
            )
        if _is_pre_close_snapshot(context, generated_at):
            return (
                ClosingConclusion.NOT_RECOMMENDED,
                "当前为期间结束前快照；即使已执行检查均通过，也不能替代期末截止后的最终关账确认。",
            )
        return ClosingConclusion.READY, "未发现阻塞项或达到不建议关账阈值的异常；仍需由授权人员确认关账。"

    @staticmethod
    def _make_todo(context: ClosingContext, finding: Finding) -> ClosingTodo:
        return ClosingTodo(
            todo_id=f"TODO-{context.company_code}-{context.period_key}-{finding.check_id}",
            finding_id=finding.finding_id,
            title=finding.title,
            module=finding.module,
            severity=finding.severity,
            blocking=finding.blocking,
            owner_department=finding.owner_department,
            owner=finding.owner,
            remediation=finding.remediation,
            status="open",
            requires_human_confirmation=finding.requires_human_confirmation,
        )


def _summarize(
    executions: list[CheckExecution], findings: list[Finding], report_currency: str
) -> dict[str, object]:
    by_module: dict[str, dict[str, object]] = defaultdict(lambda: {"count": 0, "amount": Decimal("0")})
    by_department: dict[str, dict[str, object]] = defaultdict(
        lambda: {"count": 0, "amount": Decimal("0")}
    )
    by_severity: dict[str, dict[str, object]] = defaultdict(
        lambda: {"count": 0, "amount": Decimal("0")}
    )
    total = Decimal("0")
    by_source_mode: dict[str, int] = defaultdict(int)
    for execution in executions:
        by_source_mode[source_mode(execution.sources)] += 1
    for finding in findings:
        total += finding.amount
        for group, key in (
            (by_module, finding.module),
            (by_department, finding.owner_department),
            (by_severity, finding.severity.value),
        ):
            group[key]["count"] = int(group[key]["count"]) + 1
            group[key]["amount"] = Decimal(group[key]["amount"]) + finding.amount

    def render(groups: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
        return {
            key: {"count": value["count"], "amount": format(Decimal(value["amount"]), "f")}
            for key, value in sorted(groups.items())
        }

    return {
        "currency": report_currency,
        "checks_total": len(executions),
        "checks_passed": sum(item.status is CheckStatus.PASSED for item in executions),
        "checks_exception": sum(item.status is CheckStatus.EXCEPTION for item in executions),
        "checks_error": sum(item.status is CheckStatus.ERROR for item in executions),
        "checks_data_gap": sum(item.status is CheckStatus.ERROR for item in executions),
        "findings_total": len(findings),
        "blocking_total": sum(item.blocking for item in findings),
        "total_exception_amount": format(total, "f"),
        "by_module": render(by_module),
        "by_department": render(by_department),
        "by_severity": render(by_severity),
        "by_source_mode": dict(sorted(by_source_mode.items())),
    }


def _is_pre_close_snapshot(context: ClosingContext, generated_at: datetime) -> bool:
    if context.period > 12:
        return False
    last_day = calendar.monthrange(context.fiscal_year, context.period)[1]
    return generated_at.date() < date(context.fiscal_year, context.period, last_day)
