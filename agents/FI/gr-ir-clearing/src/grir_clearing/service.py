from __future__ import annotations

from collections import Counter
from decimal import Decimal

from .analyzer import GrirAnalyzer
from .models import AnalysisCriteria, AnalysisReport, AnalysisSummary
from .ports import GrirDataSource


class GrirClearingService:
    def __init__(self, source: GrirDataSource, analyzer: GrirAnalyzer | None = None) -> None:
        self._source = source
        self._analyzer = analyzer or GrirAnalyzer()

    def analyze(self, criteria: AnalysisCriteria) -> AnalysisReport:
        po_items = tuple(self._source.list_po_items(criteria))
        history_by_key = self._source.load_po_history(
            tuple(item.key for item in po_items),
            criteria.as_of_date,
        )
        exceptions = []
        examined = 0
        for po in po_items:
            history = tuple(history_by_key.get(po.key, ()))
            if not self._has_activity_in_scope(history, criteria):
                continue
            examined += 1
            exception = self._analyzer.analyze_item(po, history, criteria.as_of_date)
            if exception:
                exceptions.append(exception)

        exceptions.sort(
            key=lambda item: (
                {"high": 0, "medium": 1, "low": 2}[item.severity.value],
                -item.age_days,
                item.po.key.po_number,
                item.po.key.po_item,
            )
        )
        reason_counts = Counter(reason.value for item in exceptions for reason in item.reasons)
        responsibility_counts = Counter(item.responsibility for item in exceptions)
        summary = AnalysisSummary(
            examined_po_items=examined,
            exception_count=len(exceptions),
            total_absolute_amount_difference=sum(
                (abs(item.amount_difference) for item in exceptions), Decimal("0")
            ),
            counts_by_reason=dict(sorted(reason_counts.items())),
            counts_by_responsibility=dict(sorted(responsibility_counts.items())),
        )
        return AnalysisReport(
            criteria=criteria,
            source_name=self._source.source_name,
            items=tuple(exceptions),
            summary=summary,
        )

    @staticmethod
    def _has_activity_in_scope(history, criteria: AnalysisCriteria) -> bool:
        if not criteria.activity_from and not criteria.activity_to:
            return True
        start = criteria.activity_from
        end = criteria.activity_to or criteria.as_of_date
        return any((not start or event.posting_date >= start) and event.posting_date <= end for event in history)
