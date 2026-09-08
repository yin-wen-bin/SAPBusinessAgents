from datetime import date
from decimal import Decimal
from pathlib import Path

from grir_clearing.analyzer import GrirAnalyzer, RuleConfig
from grir_clearing.fixture_adapter import FixtureGrirDataSource
from grir_clearing.models import AnalysisCriteria, ReasonCode, Severity
from grir_clearing.service import GrirClearingService


FIXTURE = Path(__file__).parents[1] / "fixtures" / "grir_sample.json"


def build_report(criteria: AnalysisCriteria):
    source = FixtureGrirDataSource.from_file(FIXTURE)
    analyzer = GrirAnalyzer(RuleConfig(long_outstanding_days=90, high_severity_days=180))
    return GrirClearingService(source, analyzer).analyze(criteria)


def test_classifies_all_core_exception_types_and_excludes_cleared_item():
    report = build_report(AnalysisCriteria(as_of_date=date(2026, 7, 22)))
    by_po = {item.po.key.po_number: item for item in report.items}

    assert report.summary.examined_po_items == 7
    assert report.summary.exception_count == 6
    assert "4500000007" not in by_po
    assert by_po["4500000001"].primary_reason == ReasonCode.GR_WITHOUT_IR
    assert by_po["4500000002"].primary_reason == ReasonCode.IR_WITHOUT_GR
    assert by_po["4500000003"].primary_reason == ReasonCode.QUANTITY_DIFFERENCE
    assert by_po["4500000004"].primary_reason == ReasonCode.PRICE_DIFFERENCE
    assert by_po["4500000005"].primary_reason == ReasonCode.RETURN_PENDING
    assert by_po["4500000006"].primary_reason == ReasonCode.PRICE_DIFFERENCE


def test_calculates_open_cycle_age_and_long_outstanding_flag():
    report = build_report(AnalysisCriteria(as_of_date=date(2026, 7, 22)))
    by_po = {item.po.key.po_number: item for item in report.items}

    old_gr = by_po["4500000001"]
    assert old_gr.oldest_open_date == date(2026, 1, 10)
    assert old_gr.age_days == 193
    assert old_gr.reasons == (ReasonCode.GR_WITHOUT_IR, ReasonCode.LONG_OUTSTANDING)
    assert old_gr.severity == Severity.HIGH

    price_tail = by_po["4500000006"]
    assert price_tail.amount_difference == Decimal("-5")
    # Quantity was balanced; the value-only exception starts when the invoice
    # introduces the five-CNY variance.
    assert price_tail.oldest_open_date == date(2026, 1, 4)
    assert ReasonCode.LONG_OUTSTANDING in price_tail.reasons


def test_filters_candidates_and_optional_activity_window_without_truncating_history():
    report = build_report(
        AnalysisCriteria(
            as_of_date=date(2026, 7, 22),
            company_code="2000",
            activity_from=date(2026, 7, 1),
            activity_to=date(2026, 7, 22),
        )
    )

    assert report.summary.examined_po_items == 1
    assert [item.po.key.po_number for item in report.items] == ["4500000005"]
    assert report.items[0].gr_quantity == Decimal("80")
    assert report.items[0].ir_quantity == Decimal("100")
