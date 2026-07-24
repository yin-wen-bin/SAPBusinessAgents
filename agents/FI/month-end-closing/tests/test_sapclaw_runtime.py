from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from sap_business_agents.month_end_closing import ClosingContext, load_checklist
from sap_business_agents.month_end_closing.gateway import SapDataUnavailable
from sap_business_agents.month_end_closing.sapclaw_runtime import (
    SapClawRuntimeClient,
    SapClawRuntimeGateway,
    load_sapclaw_queries,
)


ROOT = Path(__file__).parents[1]
CONTEXT = ClosingContext("1010", 2026, 7)


class StubRuntimeClient:
    def __init__(self, *, variant: str = "K4") -> None:
        self.variant = variant
        self.payloads: list[dict[str, Any]] = []

    def execute_get(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        if payload["service_name"] == "API_COMPANYCODE_SRV":
            return response(
                [
                    {
                        "CompanyCode": "1010",
                        "Currency": "EUR",
                        "FiscalYearVariant": self.variant,
                    }
                ]
            )
        return response(
            [
                {
                    "AccountingDocument": "1",
                    "AccountingDocumentItem": "1",
                    "Supplier": "V1",
                    "PostingDate": "2026-07-01",
                    "NetDueDate": "2026-07-15",
                    "ClearingDate": None,
                    "AmountInCompanyCodeCurrency": "-100.00",
                    "CompanyCodeCurrency": "EUR",
                    "CompanyCode": "1010",
                    "FiscalYear": "2026",
                },
                {
                    "AccountingDocument": "2",
                    "AccountingDocumentItem": "1",
                    "Supplier": "V2",
                    "PostingDate": "2026-07-02",
                    "NetDueDate": "2026-07-16",
                    "ClearingDate": None,
                    "AmountInCompanyCodeCurrency": "50.00",
                    "CompanyCodeCurrency": "EUR",
                    "CompanyCode": "1010",
                    "FiscalYear": "2026",
                },
            ]
        )

    def page(self, case_id: str, skip: int) -> dict[str, Any]:
        raise AssertionError("the test response must not paginate")


def response(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": True,
        "case_id": "case-1",
        "data": {"results": rows},
        "pagination": {
            "total_count": len(rows),
            "has_next": False,
            "next_skip": None,
        },
    }


def ap_check():
    checklist = load_checklist(ROOT / "config" / "month_end_checklist.toml")
    return next(item for item in checklist.checks if item.check_id == "AP_OVERDUE_ITEMS")


def approved_queries():
    queries = load_sapclaw_queries(ROOT / "config" / "sapclaw_queries.toml")
    queries["AP_OVERDUE_ITEMS"] = replace(
        queries["AP_OVERDUE_ITEMS"], production_approved=True
    )
    return queries


def test_approved_runtime_query_resolves_currency_and_normalizes_observation() -> None:
    client = StubRuntimeClient()
    gateway = SapClawRuntimeGateway(client, approved_queries())

    assert gateway.report_currency(CONTEXT) == "EUR"
    observation = gateway.collect(CONTEXT, ap_check())

    assert observation.value == 2
    assert observation.amount == Decimal("150.00")
    assert observation.currency == "EUR"
    assert len(observation.evidence) == 2
    query_payload = client.payloads[-1]
    assert "2026-07-31T23:59:59" in query_payload["query_options"]["$filter"]
    assert query_payload["output_contract"]["support_fields"] == ["CompanyCode", "FiscalYear"]


def test_unapproved_candidate_query_fails_closed_before_contacting_sap() -> None:
    client = StubRuntimeClient()
    queries = load_sapclaw_queries(ROOT / "config" / "sapclaw_queries.toml")
    queries["AP_OVERDUE_ITEMS"] = replace(
        queries["AP_OVERDUE_ITEMS"], production_approved=False
    )
    gateway = SapClawRuntimeGateway(client, queries)

    with pytest.raises(SapDataUnavailable, match="not production approved"):
        gateway.collect(CONTEXT, ap_check())

    assert client.payloads == []


def test_non_calendar_fiscal_variant_requires_approved_resolver() -> None:
    client = StubRuntimeClient(variant="V3")
    gateway = SapClawRuntimeGateway(client, approved_queries())

    with pytest.raises(SapDataUnavailable, match="fiscal calendar resolver"):
        gateway.collect(CONTEXT, ap_check())


def test_query_config_covers_every_check_and_keeps_unresolved_rules_unapproved() -> None:
    queries = load_sapclaw_queries(ROOT / "config" / "sapclaw_queries.toml")

    checklist = load_checklist(ROOT / "config" / "month_end_checklist.toml")
    assert set(queries) == {item.check_id for item in checklist.checks}
    assert queries["AP_OVERDUE_ITEMS"].production_approved is False
    assert "deduplication" in queries["AP_OVERDUE_ITEMS"].validation_status


def test_runtime_client_rejects_non_loopback_base_url() -> None:
    client = SapClawRuntimeClient("http://example.com")

    with pytest.raises(SapDataUnavailable, match="loopback"):
        client.execute_get({"any": "payload"})
