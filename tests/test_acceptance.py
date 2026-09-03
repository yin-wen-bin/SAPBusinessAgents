from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from sap_business_agents_platform.acceptance import (
    CanonicalTestCase,
    canonical_hash,
    compare_semantic_results,
    validate_direct_baseline,
)
from scripts.run_three_stage_acceptance import (
    _acceptance_prompt,
    _finalize_normalized,
    _limitation_codes,
    _normalize_run,
    _normalize_record,
    _read_fixed_result,
)
from scripts.run_three_stage_campaign import (
    _matched_free_run_id,
    _terminal_acceptance,
    _validate_campaign,
)
from scripts.promote_three_stage_acceptance import baseline_report
from scripts.build_material_shortage_direct_baseline import select_qualified_coverage


CONTRACT = {
    "business_keys": ["document", "item", "ledger"],
    "facts": ["as_of_status", "clearing_document"],
    "decimal_fields": ["amount"],
    "currency_fields": ["currency"],
    "metrics": ["open_items"],
    "required_limitations": ["bank_settlement_not_proven"],
}


def _result(records: list[dict], *, complete: bool = True) -> dict:
    return {
        "records": records,
        "metrics": {"open_items": len(records)},
        "limitations": ["bank_settlement_not_proven"],
        "source_complete": complete,
    }


def test_campaign_prompt_exposes_only_value_free_semantic_contract() -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "2.0",
            "case_id": "summary-live-001",
            "agent_id": "summary-agent",
            "question": {"zh": "检查期间", "en": "Check the requested period"},
            "input": {"company_code": "1710", "period": 1},
            "business_conditions": {"company_code": "1710", "period": 1},
            "expected_grain": ["company_code", "period"],
            "expected_output": {
                "record_fields": ["company_code", "period", "business_status"],
                "metric_ids": ["posting_rows"],
                "minimum_primary_evidence_rows": 1,
                "allow_empty_result": False,
                "evidence_scope": "complete",
            },
        }
    )
    prompt = _acceptance_prompt(
        case,
        {
            "business_keys": ["company_code", "period"],
            "facts": ["business_status"],
            "metrics": ["posting_rows"],
            "required_limitations": ["period_control_evidence"],
        },
    )

    assert prompt.startswith("Check the requested period")
    assert "company_code, period" in prompt
    assert "posting_rows" in prompt
    assert "Always include these required limitation codes" in prompt
    assert "1710" not in prompt


def test_acceptance_prompt_includes_versioned_fact_definitions() -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "2.0",
            "case_id": "mrp-facts-live-001",
            "agent_id": "mrp-exception-analysis",
            "question": {"zh": "检查 MRP", "en": "Check MRP"},
            "input": {},
            "business_conditions": {},
            "expected_grain": ["exception_number"],
            "expected_output": {
                "record_fields": ["exception_number", "priority_level"],
                "metric_ids": [],
                "minimum_primary_evidence_rows": 1,
                "allow_empty_result": False,
                "evidence_scope": "complete",
            },
        }
    )

    prompt = _acceptance_prompt(
        case,
        {
            "business_keys": ["exception_number"],
            "facts": ["priority_level"],
            "metrics": [],
            "required_limitations": [],
            "fact_definitions": {
                "priority_level": "10=high; 20=medium; not SAP native priority"
            },
        },
    )

    assert "Fact priority_level: 10=high; 20=medium" in prompt


def test_campaign_prompt_includes_comparison_units_and_only_blocking_status_rule() -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "2.0",
            "case_id": "unit-live-001",
            "agent_id": "unit-agent",
            "question": {"zh": "检查数量", "en": "Check quantity"},
            "input": {},
            "business_conditions": {},
            "expected_grain": ["document"],
            "expected_output": {
                "record_fields": ["document", "quantity", "unit", "business_status"],
                "metric_ids": ["confirmed_quantity"],
                "minimum_primary_evidence_rows": 1,
                "allow_empty_result": False,
                "evidence_scope": "bounded",
            },
        }
    )

    prompt = _acceptance_prompt(
        case,
        {
            "business_keys": ["document"],
            "facts": ["business_status"],
            "decimal_fields": ["quantity"],
            "unit_fields": ["unit"],
            "metrics": ["confirmed_quantity"],
            "required_limitations": ["bank_settlement_not_proven"],
            "blocking_limitations": ["historical_balance_evidence"],
        },
    )

    assert "business_status, quantity, unit" in prompt
    assert "historical_balance_evidence" in prompt
    assert "required limitation remains unresolved" not in prompt
    assert "return null; never substitute zero for unknown" in prompt


def test_shortage_acceptance_contract_preserves_nonblocking_staleness_and_blank_keys() -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "1.0",
            "case_id": "shortage-live-002",
            "agent_id": "material-shortage-procurement-response",
            "question": {"zh": "检查短缺", "en": "Check shortage"},
            "input": {},
            "business_conditions": {},
            "expected_grain": ["material", "plant", "requirement_id"],
        }
    )
    contract = {
        "business_keys": ["material", "plant", "requirement_id"],
        "facts": ["mrp_element_type", "business_status"],
        "metrics": ["shortage_quantity", "pending_pr"],
        "composite_blank_fields": ["requirement_id"],
        "composite_key_parts": {
            "requirement_id": [
                {"name": "profile", "aliases": ["MaterialShortageProfile"]},
                {"name": "counter", "aliases": ["MaterialShortageProfileCount"]},
                {"name": "mrp_area", "aliases": ["MRPArea"]},
                {"name": "segment", "aliases": ["MRPPlanningSegmentNumber"]},
                {"name": "segment_type", "aliases": ["MRPPlanningSegmentType"]},
            ]
        },
        "nonblocking_observation_codes": ["mrp_snapshot_stale"],
        "test_data_qualification_definition": (
            "Positive active coverage with external procurement F and complete sources qualifies."
        ),
        "value_mappings": {"mrp_element_type": {"02": "material_coverage"}},
    }

    prompt = _acceptance_prompt(case, contract)
    normalized = _normalize_record(
        {
            "material": "RM4_CP",
            "plant": "1710",
            "requirement_id": "PROFILE|001|1710|<blank>|02",
            "mrp_element_type": "02",
        },
        case,
        contract,
    )

    assert "non-blocking observations" in prompt
    assert "mrp_snapshot_stale" in prompt
    assert "represent every missing key segment exactly as (blank)" in prompt
    assert normalized["requirement_id"] == "PROFILE|001|1710|(blank)|02"
    assert normalized["mrp_element_type"] == "material_coverage"

    labelled = _normalize_record(
        {
            "requirement_id": (
                "profile=PROFILE;counter=001;mrp_area=1710;"
                "segment=<blank>;segment_type=02"
            )
        },
        case,
        contract,
    )
    assert labelled["requirement_id"] == "PROFILE|001|1710|(blank)|02"


def test_single_object_key_value_presentation_participates_in_acceptance() -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "2.0",
            "case_id": "summary-live-001",
            "agent_id": "summary-agent",
            "question": {"zh": "检查期间", "en": "Check period"},
            "input": {"company_code": "1710", "period": 1},
            "business_conditions": {"company_code": "1710", "period": 1},
            "expected_grain": ["company_code", "period"],
            "expected_output": {
                "record_fields": ["company_code", "period", "business_status"],
                "metric_ids": ["posting_rows"],
                "minimum_primary_evidence_rows": 1,
                "allow_empty_result": False,
                "evidence_scope": "complete",
            },
        }
    )
    contract = {
        "business_keys": ["company_code", "period"],
        "facts": ["business_status"],
        "metrics": ["posting_rows"],
        "required_limitations": [],
        "field_aliases": {},
        "input_defaults": {},
        "constant_defaults": {},
    }
    run = {
        "result": {
            "completeness": {"source_complete": True},
            "presentation": {
                "blocks": [
                    {
                        "type": "key_value",
                        "entries": [
                            {"label": {"en": "company_code"}, "value": {"en": "1710"}},
                            {"label": {"en": "period"}, "value": {"en": "1"}},
                            {"label": {"en": "business_status"}, "value": {"en": "ready"}},
                        ],
                    },
                    {
                        "type": "metrics",
                        "metrics": [
                            {"id": "posting_rows", "value": {"en": "2"}},
                        ],
                    },
                ]
            },
        }
    }

    normalized = _normalize_run(run, case, contract)

    assert normalized["records"] == [
        {"company_code": "1710", "period": "1", "business_status": "ready"}
    ]
    assert normalized["metrics"] == {"posting_rows": "2"}


def test_table_aliases_are_applied_once_when_source_names_overlap_canonical_fields() -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "2.0",
            "case_id": "rfq-live-001",
            "agent_id": "rfq-agent",
            "question": {"zh": "评估询价", "en": "Evaluate RFQ"},
            "input": {},
            "business_conditions": {},
            "expected_grain": ["rfq", "item"],
            "expected_output": {
                "record_fields": ["rfq", "item", "unit", "price_unit"],
                "metric_ids": [],
                "minimum_primary_evidence_rows": 1,
                "allow_empty_result": False,
                "evidence_scope": "complete",
            },
        }
    )
    contract = {
        "business_keys": ["rfq", "item"],
        "facts": [],
        "metrics": [],
        "required_limitations": [],
        "decimal_fields": ["price_unit"],
        "unit_fields": ["unit"],
        "field_aliases": {
            "price_unit": ["price_basis_qty"],
            "unit": ["price_unit"],
        },
        "input_defaults": {},
        "constant_defaults": {},
    }
    run = {
        "result": {
            "completeness": {"source_complete": True},
            "presentation": {
                "blocks": [
                    {
                        "type": "table",
                        "columns": [
                            {"key": "rfq"},
                            {"key": "item"},
                            {"key": "price_basis_qty"},
                            {"key": "price_unit"},
                        ],
                        "rows": [
                            {"values": ["700", "10", "1", "PC"]},
                        ],
                    }
                ]
            },
        }
    }

    normalized = _normalize_run(run, case, contract)

    assert normalized["records"] == [
        {"rfq": "700", "item": "10", "price_unit": "1", "unit": "PC"}
    ]


def test_hidden_action_table_can_supply_complete_acceptance_records() -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "2.0",
            "case_id": "grir-live-001",
            "agent_id": "gr-ir-clearing",
            "question": {"zh": "核对GR/IR", "en": "Reconcile GR/IR"},
            "input": {},
            "business_conditions": {},
            "expected_grain": ["purchase_order", "purchase_order_item"],
            "expected_output": {
                "record_fields": [
                    "purchase_order",
                    "purchase_order_item",
                    "business_status",
                ],
                "metric_ids": ["examined_item_count"],
                "minimum_primary_evidence_rows": 1,
                "allow_empty_result": False,
                "evidence_scope": "complete",
            },
        }
    )
    normalized = _normalize_run(
        {
            "result": {
                "completeness": {"source_complete": True},
                "rule_results": [
                    {
                        "business_status": "normal",
                        "business_report": {
                            "records": [],
                            "metrics": [{"id": "examined_item_count", "value": 1}],
                            "action_tables": [
                                {
                                    "id": "all_reconciliation_records",
                                    "display": False,
                                    "acceptance_records": True,
                                    "rows": [
                                        {
                                            "purchase_order": "1",
                                            "purchase_order_item": "10",
                                            "business_status": "normal",
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                ],
            }
        },
        case,
        {
            "business_keys": ["purchase_order", "purchase_order_item"],
            "facts": ["business_status"],
            "metrics": ["examined_item_count"],
            "required_limitations": [],
            "field_aliases": {},
            "input_defaults": {},
            "constant_defaults": {},
        },
    )

    assert normalized["records"] == [
        {
            "purchase_order": "1",
            "purchase_order_item": "10",
            "business_status": "normal",
        }
    ]
    assert normalized["metrics"] == {"examined_item_count": 1}


def test_positive_diagnostic_notice_is_not_reported_as_a_limitation() -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "1.0",
            "case_id": "complete-live-001",
            "agent_id": "complete-agent",
            "question": {"zh": "检查", "en": "Check"},
            "input": {},
            "business_conditions": {},
            "expected_grain": ["document"],
        }
    )
    normalized = _normalize_run(
        {
            "result": {
                "completeness": {"source_complete": True},
                "presentation": {
                    "blocks": [
                        {
                            "type": "table",
                            "columns": [{"key": "document"}],
                            "rows": [{"values": ["1"]}],
                        },
                        {
                            "type": "notice",
                            "tone": "info",
                            "claim_scope": "diagnostic",
                            "text": {"en": "All exact reads returned source_complete=true."},
                        },
                    ]
                },
            }
        },
        case,
        {
            "business_keys": ["document"],
            "facts": [],
            "metrics": [],
            "required_limitations": [],
            "field_aliases": {},
            "input_defaults": {},
            "constant_defaults": {},
            "limitation_keywords": {"source_incomplete": ["source incomplete"]},
        },
    )

    assert normalized["limitations"] == []


def test_info_business_notice_emits_declared_limitation_code() -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "1.0",
            "case_id": "horizon-live-001",
            "agent_id": "mrp-exception-analysis",
            "question": {"zh": "检查", "en": "Check"},
            "input": {},
            "business_conditions": {},
            "expected_grain": ["document"],
        }
    )
    normalized = _normalize_run(
        {
            "result": {
                "completeness": {"source_complete": True},
                "presentation": {
                    "blocks": [
                        {
                            "type": "table",
                            "columns": [{"key": "document"}],
                            "rows": [{"values": ["1"]}],
                        },
                        {
                            "type": "notice",
                            "tone": "info",
                            "claim_scope": "business_semantics",
                            "text": {
                                "en": "sap_shortage_time_horizon_applies: current material coverage and supply-demand horizon only; source_complete=true."
                            },
                        },
                    ]
                },
            }
        },
        case,
        {
            "business_keys": ["document"],
            "facts": [],
            "metrics": [],
            "required_limitations": ["sap_shortage_time_horizon_applies"],
            "field_aliases": {},
            "input_defaults": {},
            "constant_defaults": {},
            "ignored_notice_keywords": ["source_complete=true"],
            "limitation_keywords": {
                "sap_shortage_time_horizon_applies": ["time_horizon"],
                "mrp_coverage_evidence": ["material coverage"],
                "mrp_supply_demand_evidence": ["supply-demand"],
            },
        },
    )

    assert normalized["limitations"] == ["sap_shortage_time_horizon_applies"]


def test_metric_value_mapping_can_explicitly_map_unknown_text_to_null() -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "1.0",
            "case_id": "metric-live-001",
            "agent_id": "metric-agent",
            "question": {"zh": "检查", "en": "Check"},
            "input": {},
            "business_conditions": {},
            "expected_grain": ["document"],
        }
    )
    normalized = _finalize_normalized(
        {
            "records": [{"document": "1"}],
            "metrics": {"ready": "Not determined"},
            "limitations": [],
            "source_complete": True,
        },
        case,
        {
            "business_keys": ["document"],
            "facts": [],
            "metrics": ["ready"],
            "metric_value_mappings": {"ready": {"not determined": None}},
        },
        business_status="",
    )

    assert normalized["metrics"]["ready"] is None


def test_canonical_business_report_fields_are_not_reinterpreted_as_display_aliases() -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "1.0",
            "case_id": "canonical-live-001",
            "agent_id": "canonical-agent",
            "question": {"zh": "检查", "en": "Check"},
            "input": {},
            "business_conditions": {},
            "expected_grain": ["document"],
        }
    )
    normalized = _finalize_normalized(
        {
            "records": [{"document": "1", "unit": "PC", "price_unit": "1"}],
            "metrics": {},
            "limitations": [],
            "source_complete": True,
        },
        case,
        {
            "business_keys": ["document"],
            "facts": [],
            "metrics": [],
            "decimal_fields": ["price_unit"],
            "unit_fields": ["unit"],
            "field_aliases": {
                "price_unit": ["price_basis_qty"],
                "unit": ["price_unit"],
            },
        },
        business_status="",
        records_are_canonical=True,
    )

    assert normalized["records"][0]["unit"] == "PC"
    assert normalized["records"][0]["price_unit"] == "1"


def test_one_notice_can_emit_multiple_canonical_limitations() -> None:
    assert _limitation_codes(
        "billing_output_status_evidence missing; billing_dispute_case_evidence missing",
        {
            "limitation_keywords": {
                "billing_output_status_evidence": ["output evidence"],
                "billing_dispute_case_evidence": ["dispute evidence"],
            }
        },
    ) == ["billing_output_status_evidence", "billing_dispute_case_evidence"]


def test_blocking_limitation_overrides_raw_sap_status_in_business_status() -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "1.0",
            "case_id": "blocked-live-001",
            "agent_id": "blocked-agent",
            "question": {"zh": "检查状态", "en": "Check status"},
            "input": {},
            "business_conditions": {},
            "expected_grain": ["document"],
        }
    )
    normalized = _finalize_normalized(
        {
            "records": [{"document": "1", "business_status": "C"}],
            "metrics": {},
            "limitations": ["missing_business_evidence"],
            "source_complete": True,
        },
        case,
        {
            "business_keys": ["document"],
            "facts": ["business_status"],
            "metrics": [],
            "blocking_limitations": ["missing_business_evidence"],
        },
        business_status="",
    )

    assert normalized["records"][0]["business_status"] == "capability_blocked"


def test_semantic_comparison_ignores_record_order_and_decimal_format() -> None:
    first = {
        "document": "1900000019",
        "item": "1",
        "ledger": "0L",
        "as_of_status": "open",
        "clearing_document": "",
        "amount": "-1000.00",
        "currency": "USD",
    }
    second = {
        "document": "5100000088",
        "item": "1",
        "ledger": "0L",
        "as_of_status": "open_subsequently_cleared",
        "clearing_document": "2000000014",
        "amount": "-200",
        "currency": "USD",
    }
    expected = _result([first, second])
    actual = _result([{**second, "amount": "-200.0"}, first])

    comparison = compare_semantic_results(expected, actual, CONTRACT)

    assert comparison.verdict == "MATCH"
    assert comparison.differences == ()


def test_semantic_comparison_treats_null_and_blank_optional_facts_as_equal() -> None:
    expected = _result(
        [{"document": "1", "item": "1", "ledger": "0L", "as_of_status": None, "clearing_document": ""}]
    )
    actual = _result(
        [{"document": "1", "item": "1", "ledger": "0L", "as_of_status": "", "clearing_document": None}]
    )

    comparison = compare_semantic_results(expected, actual, CONTRACT)

    assert comparison.verdict == "MATCH"


def test_semantic_comparison_allows_declared_blank_business_key_segments() -> None:
    expected = _result(
        [{"document": "1", "item": "1", "ledger": "", "as_of_status": "open"}]
    )
    actual = _result(
        [{"document": "1", "item": "1", "ledger": "", "as_of_status": "open"}]
    )

    comparison = compare_semantic_results(
        expected,
        actual,
        {**CONTRACT, "blank_business_key_fields": ["ledger"]},
    )

    assert comparison.verdict == "MATCH"


def test_semantic_comparison_reports_records_facts_and_completeness() -> None:
    expected = _result(
        [
            {
                "document": "1900000019",
                "item": "1",
                "ledger": "0L",
                "as_of_status": "open",
                "clearing_document": "",
                "amount": "-1000",
                "currency": "USD",
            }
        ],
        complete=False,
    )
    actual = _result([], complete=True)
    actual["limitations"] = []

    comparison = compare_semantic_results(expected, actual, CONTRACT)

    assert comparison.verdict == "MISMATCH"
    assert {item["code"] for item in comparison.differences} == {
        "record_set_mismatch",
        "metric_mismatch",
        "completeness_overstated",
        "required_limitations_missing",
    }


def test_v2_comparison_allows_more_conservative_source_completeness() -> None:
    expected = _result([], complete=True)
    actual = _result([], complete=False)
    contract = {**CONTRACT, "schema_version": "2.0"}

    comparison = compare_semantic_results(expected, actual, contract)

    assert comparison.verdict == "MATCH"
    assert comparison.differences == ()


def test_semantic_comparison_blocks_when_evidence_is_blocked() -> None:
    comparison = compare_semantic_results(
        {"blocked": True},
        {"records": []},
        CONTRACT,
    )

    assert comparison.verdict == "BLOCKED"


def test_canonical_case_and_independent_baseline_contract() -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "1.0",
            "case_id": "ap-payment-live-001",
            "agent_id": "ap-payment",
            "question": {"zh": "查询供应商未清项", "en": "Query supplier open items"},
            "input": {"company_code": "1710", "supplier": "17300001", "as_of": "2018-10-01"},
            "business_conditions": {"company_code": "1710"},
            "expected_grain": ["accounting_document", "accounting_document_item", "ledger"],
        }
    )
    normalized = {"records": [], "metrics": {}, "limitations": [], "source_complete": True}
    baseline = {
        "runtime": "codex_app_direct_sap",
        "used_sap_business_agents": False,
        "http_method": "GET",
        "schema_hash": "sha256:" + "a" * 64,
        "result_hash": canonical_hash(normalized),
        "normalized_result": normalized,
    }

    assert case.case_id == "ap-payment-live-001"
    assert validate_direct_baseline(baseline) == normalized


def test_v2_case_and_multisource_baseline_require_complete_primary_evidence() -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "2.0",
            "case_id": "ar-live-001",
            "agent_id": "ar-collection",
            "question": {"zh": "查询客户未清项", "en": "Query customer open items"},
            "input": {"company_code": "1710", "customer": "1", "as_of": "2018-10-01"},
            "business_conditions": {"financial_account_type": "D"},
            "expected_grain": ["document", "item"],
            "expected_output": {
                "record_fields": ["document", "item", "business_status"],
                "metric_ids": ["open_items"],
                "minimum_primary_evidence_rows": 1,
                "allow_empty_result": False,
                "evidence_scope": "complete",
            },
        }
    )
    normalized = {
        "records": [{"document": "1", "item": "1", "business_status": "attention"}],
        "metrics": {"open_items": 1},
        "limitations": [],
        "source_complete": True,
    }
    source = {
        "source_id": "items",
        "service_name": "API_TEST_SRV",
        "odata_version": "2.0",
        "entity_set": "A_Item",
        "schema_hash": "sha256:" + "a" * 64,
        "query_hash": "sha256:" + "b" * 64,
        "row_count": 1,
        "page_count": 1,
        "stable_order_by": ["Document", "Item"],
        "paging_complete": True,
        "source_complete": True,
        "primary": True,
    }
    baseline = {
        "schema_version": "2.0",
        "runtime": "codex_app_direct_sap",
        "used_sap_business_agents": False,
        "http_methods": ["GET"],
        "sources": [source],
        "result_hash": canonical_hash(normalized),
        "normalized_result": normalized,
    }

    baseline["supplemental_sources"] = [
        {
            "source_id": "item_incompletion",
            "provider": "sap-adt-table-export",
            "object": "VBUV",
            "fields": ["VBELN", "POSNR", "FDNAM"],
            "filter_hash": "sha256:" + "c" * 64,
            "manifest_hash": "sha256:" + "d" * 64,
            "row_count": 0,
            "paging_complete": True,
            "source_complete": True,
            "read_only": True,
            "validated": True,
            "hash_verified": True,
        }
    ]

    assert validate_direct_baseline(baseline, case) == normalized

    baseline["supplemental_sources"][0]["hash_verified"] = False
    try:
        validate_direct_baseline(baseline, case)
    except ValueError as exc:
        assert "hash_verified must be true" in str(exc)
    else:
        raise AssertionError("unverified ADT baseline evidence was accepted")
    baseline["supplemental_sources"][0]["hash_verified"] = True

    baseline["qualification"] = {
        "status": "blocked",
        "reasons": ["qualified_test_data_missing"],
        "evidence_source_ids": ["items"],
        "evidence_hash": canonical_hash({"candidate": "complete-but-not-qualified"}),
    }
    assert validate_direct_baseline(baseline, case) == normalized
    baseline["qualification"]["evidence_source_ids"] = ["unknown-source"]
    try:
        validate_direct_baseline(baseline, case)
    except ValueError as exc:
        assert "unknown source" in str(exc)
    else:
        raise AssertionError("unknown qualification evidence source was accepted")
    baseline["qualification"]["evidence_source_ids"] = ["items"]

    baseline["sources"][0]["paging_complete"] = False
    try:
        validate_direct_baseline(baseline, case)
    except ValueError as exc:
        assert "incomplete" in str(exc)
    else:
        raise AssertionError("incomplete v2 baseline was accepted")


def test_v2_semantic_comparison_checks_units_decimal_metrics_and_limitations() -> None:
    contract = {
        "schema_version": "2.0",
        "business_keys": ["material"],
        "facts": ["business_status"],
        "decimal_fields": ["quantity"],
        "currency_fields": [],
        "unit_fields": ["unit"],
        "metrics": ["total_quantity"],
        "decimal_metrics": ["total_quantity"],
        "required_limitations": [],
    }
    expected = {
        "records": [{"material": "M1", "business_status": "normal", "quantity": "1.0", "unit": "EA"}],
        "metrics": {"total_quantity": "1.00"},
        "limitations": ["bounded_capacity_evidence"],
        "source_complete": True,
    }
    actual = {
        "records": [{"material": "M1", "business_status": "normal", "quantity": "1", "unit": "PC"}],
        "metrics": {"total_quantity": 1},
        "limitations": [],
        "source_complete": True,
    }

    comparison = compare_semantic_results(expected, actual, contract)

    assert comparison.verdict == "MISMATCH"
    assert {item["code"] for item in comparison.differences} == {
        "currency_or_unit_mismatch",
        "baseline_limitations_missing",
    }


def test_remaining_agents_use_non_placeholder_acceptance_v2_contracts() -> None:
    root = Path(__file__).resolve().parents[1]
    manifests = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((root / "agents").glob("*/*/agent.json"))
        if path.parent.name not in {"ap-payment", "role-agent-matching"}
    ]

    assert len(manifests) == 30
    for manifest in manifests:
        acceptance = manifest["execution"]["acceptance"]
        assert acceptance["schemaVersion"] == "2.0"
        assert acceptance["businessKeys"]
        assert acceptance["metrics"]
        assert "business_status" in acceptance["facts"]
        expected_limitations = {
            "ar-collection": ["historical_dunning_evidence"],
            "month-end-closing": [
                "fx_valuation_run_status_source_unavailable"
            ],
            "billing-output-monitor": ["billing_output_status_evidence"],
            "billing-dispute-classification": ["billing_dispute_case_evidence"],
            "shortage-allocation-advisor": ["atp_availability_evidence"],
            "order-to-cash-anomaly-monitor": [
                "billing_output_status_evidence",
                "billing_dispute_case_evidence",
            ],
            "due-delivery-prioritization": [
                "current_stock_not_historical_atp"
            ],
            "production-variance-analysis": [],
            "demand-forecast-planning": [],
            "new-sales-demand-coverage": ["mrp_simulation_not_formal_atp"],
            "mrp-exception-analysis": ["sap_shortage_time_horizon_applies"],
            "production-scheduling-capacity": [
                "complete_capacity_bucket_evidence"
            ],
            "cost-center-expense-anomaly": ["plan_evidence_missing"],
            "budget-rolling-forecast": ["budget_evidence_missing"],
                "internal-order-project-control": [
                    "plan_evidence",
                    "budget_evidence",
                    "commitment_evidence",
                    "budget_ledger_ambiguous",
                    "wbs_commitment_source_unavailable",
                    "internal_order_commitment_source_unavailable",
                    "currency_not_comparable",
                    "wbs_mode_acceptance",
                    "internal_order_mode_acceptance",
                    "free_query_comparison",
                    "test_data_gap",
                ],
                "product-cost-variance": [],
            "co-month-end-allocation-settlement": [
                "allocation_cycle_evidence",
                "object_status_evidence",
                "settlement_rule_evidence",
            ],
        }.get(manifest["slug"], [])
        assert acceptance["requiredLimitations"] == expected_limitations
        assert isinstance(acceptance["summaryRecord"], bool)
        assert isinstance(acceptance["blockingLimitations"], list)
        assert isinstance(acceptance["ignoredNoticeKeywords"], list)


def test_agent_status_matches_terminal_three_stage_verdict() -> None:
    root = Path(__file__).resolve().parents[1]
    manifests = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((root / "agents").glob("*/*/agent.json"))
    ]

    deterministic = [item for item in manifests if item.get("kind") != "platform_assistant"]
    assert len(deterministic) == 31
    for manifest in deterministic:
        verdict = manifest["validation"]["verdict"]
        expected = "passed" if verdict == "PASS" else verdict.lower()
        assert manifest["status"] == f"Three-stage live acceptance {expected}"


def test_completed_fixed_result_can_be_reused_only_for_the_exact_case(tmp_path: Path) -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "2.0",
            "case_id": "sample-live-001",
            "agent_id": "sample-agent",
            "question": {"zh": "测试", "en": "Test"},
            "input": {"company_code": "1710"},
            "business_conditions": {"company_code": "1710"},
            "expected_grain": ["company_code"],
            "expected_output": {
                "record_fields": ["company_code", "business_status"],
                "metric_ids": ["rows"],
                "minimum_primary_evidence_rows": 1,
                "allow_empty_result": False,
                "evidence_scope": "complete",
            },
        }
    )
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps(
            {
                "run_id": "fixed-1",
                "mode": "agent",
                "agent_id": "sample-agent",
                "input": {"company_code": "1710"},
                "completed_at": "2026-08-20T00:00:00Z",
                "completeness": {"source_complete": True, "business_complete": True},
            }
        ),
        encoding="utf-8",
    )

    reused = _read_fixed_result(path, case)
    assert reused["status"] == "completed"
    assert reused["result"]["run_id"] == "fixed-1"

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["input"] = {"company_code": "1010"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        _read_fixed_result(path, case)
    except ValueError as exc:
        assert "input does not match" in str(exc)
    else:
        raise AssertionError("mismatching fixed result was reused")


def test_manifest_driven_composite_field_extraction_is_not_agent_hardcoded() -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "1.0",
            "case_id": "generic",
            "agent_id": "generic",
            "question": {"zh": "查询", "en": "Query"},
            "input": {},
            "business_conditions": {},
            "expected_grain": ["document"],
        }
    )
    contract = {
        "business_keys": ["document"],
        "facts": ["clearing_date", "status"],
        "metrics": [],
        "decimal_fields": [],
        "currency_fields": [],
        "unit_fields": [],
        "field_aliases": {},
        "field_extractors": {
            "clearing_date": {
                "source": ["later_clearing", "subsequent_clearing"],
                "pattern": r"(\d{4}-\d{2}-\d{2})",
                "default": "",
            },
            "status": {
                "source": "dunning_text",
                "contains": {"history unknown": "unknown", "dunned": "confirmed"},
                "default": "none",
            },
        },
    }

    record = _normalize_record(
        {
            "document": "1",
            "subsequent_clearing": "2022-06-09; document 10",
            "dunning_text": "History unknown: later event only",
        },
        case,
        contract,
    )

    assert record["clearing_date"] == "2022-06-09"
    assert record["status"] == "unknown"


def test_manifest_driven_business_status_can_be_derived_from_metric() -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "1.0",
            "case_id": "status-from-metric",
            "agent_id": "generic",
            "question": {"zh": "查询", "en": "Query"},
            "input": {},
            "business_conditions": {},
            "expected_grain": ["document"],
        }
    )
    value = {
        "records": [{"document": "1"}],
        "metrics": {"finding_count": "0 findings"},
        "limitations": [],
        "source_complete": True,
    }
    contract = {
        "business_keys": ["document"],
        "facts": ["business_status"],
        "metrics": ["finding_count"],
        "decimal_fields": [],
        "currency_fields": [],
        "unit_fields": [],
        "input_defaults": {},
        "constant_defaults": {},
        "field_aliases": {},
        "field_extractors": {},
        "business_status_from_metric": {
            "metric": "finding_count",
            "zero": "normal",
            "nonzero": "attention",
        },
    }

    finalized = _finalize_normalized(value, case, contract, business_status="")

    assert finalized["records"] == [{"document": "1", "business_status": "normal"}]


def test_summary_fact_can_be_zeroed_by_an_integer_zero_metric() -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "1.0",
            "case_id": "zero-summary-fact",
            "agent_id": "generic",
            "question": {"zh": "查询", "en": "Query"},
            "input": {"material": "M1"},
            "business_conditions": {},
            "expected_grain": ["material"],
        }
    )
    value = {
        "records": [{"material": "M1"}],
        "metrics": {"planned_rows": 0},
        "limitations": [],
        "source_complete": True,
    }
    contract = {
        "business_keys": ["material"],
        "facts": ["business_status"],
        "metrics": ["planned_rows"],
        "decimal_fields": ["planned_quantity"],
        "currency_fields": [],
        "unit_fields": [],
        "input_defaults": {},
        "constant_defaults": {},
        "field_aliases": {},
        "field_extractors": {},
        "zero_fact_when_metric_zero": {"planned_quantity": "planned_rows"},
    }

    finalized = _finalize_normalized(
        value, case, contract, business_status="capability_blocked"
    )

    assert finalized["records"][0]["planned_quantity"] == "0"


def test_multi_metric_status_rule_overrides_model_status_deterministically() -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "1.0",
            "case_id": "multi-metric-status",
            "agent_id": "generic",
            "question": {"zh": "查询", "en": "Query"},
            "input": {},
            "business_conditions": {},
            "expected_grain": ["document"],
        }
    )
    value = {
        "records": [{"document": "1", "business_status": "normal"}],
        "metrics": {"shortage": "0 ST", "pending": 2},
        "limitations": [],
        "source_complete": True,
    }
    contract = {
        "business_keys": ["document"],
        "facts": ["business_status"],
        "metrics": ["shortage", "pending"],
        "decimal_fields": [],
        "currency_fields": [],
        "unit_fields": [],
        "input_defaults": {},
        "constant_defaults": {},
        "field_aliases": {},
        "field_extractors": {},
        "business_status_from_any_positive_metric": {
            "metrics": ["shortage", "pending"],
            "positive": "attention",
            "zero": "normal",
        },
    }

    finalized = _finalize_normalized(value, case, contract, business_status="normal")

    assert finalized["records"][0]["business_status"] == "attention"


def test_campaign_only_reuses_a_free_run_after_semantic_match() -> None:
    assert _matched_free_run_id(
        {"free_query": {"run_id": "run_match", "comparison": {"verdict": "MATCH"}}}
    ) == "run_match"


def test_campaign_recovers_a_current_terminal_acceptance_artifact() -> None:
    expected_hash = "sha256:" + "a" * 64
    artifact = {
        "verdict": "BLOCKED",
        "blocking_limitations": ["capability_gap"],
        "free_query": {
            "run_id": "run-1",
            "comparison": {"verdict": "MATCH", "expected_hash": expected_hash},
        },
        "fixed_agent": {
            "comparison": {"verdict": "MATCH", "expected_hash": expected_hash},
        },
    }
    assert _terminal_acceptance(artifact, expected_hash) == ("BLOCKED", "run-1")
    artifact["blocking_limitations"] = []
    assert _terminal_acceptance(artifact, expected_hash) is None


def test_public_acceptance_report_contains_sanitized_multisource_completeness(tmp_path: Path) -> None:
    artifact = {
        "case": {
            "case_id": "sample-live-001",
            "input": {"company_code": "1710"},
            "business_conditions": {"company_code": "1710"},
            "expected_grain": ["document"],
        },
        "direct_baseline": {
            "nonblocking_observations": [
                {
                    "code": "mrp_snapshot_stale",
                    "severity": "warning",
                    "blocking": False,
                    "last_mrp_date": "2026-05-12",
                    "age_days": 103,
                }
            ],
            "sources": [
                {
                    "source_id": "items",
                    "service_name": "API_TEST_SRV",
                    "odata_version": "2.0",
                    "entity_set": "A_Item",
                    "row_count": 2,
                    "page_count": 1,
                    "stable_order_by": ["Document"],
                    "paging_complete": True,
                    "source_complete": True,
                }
            ],
            "supplemental_sources": [
                {
                    "source_id": "item_incompletion_log",
                    "provider": "sap-adt-table-export",
                    "object": "VBUV",
                    "fields": ["VBELN", "POSNR", "FDNAM"],
                    "row_count": 0,
                    "paging_complete": True,
                    "source_complete": True,
                    "hash_verified": True,
                    "filter_hash": "sha256:" + "a" * 64,
                    "manifest_hash": "sha256:" + "b" * 64,
                }
            ],
        },
    }

    report = baseline_report(tmp_path, artifact)

    assert "API_TEST_SRV" in report
    assert "A_Item" in report
    assert "Paging complete" in report
    assert "mrp_snapshot_stale" in report
    assert "blocking=`false`" in report
    assert "Supplemental read-only evidence" in report
    assert "sap-adt-table-export" in report
    assert "VBUV" in report
    assert "Hash verified" in report
    assert "1710" not in report
    assert _matched_free_run_id(
        {"free_query": {"run_id": "run_stale", "comparison": {"verdict": "MISMATCH"}}}
    ) is None


def test_material_shortage_candidate_selection_skips_zero_expired_and_internal_rows() -> None:
    master_rows = [
        {
            "Material": "ZERO",
            "MRPPlant": "1710",
            "MRPArea": "1710",
            "MaterialProcurementCategory": "F",
        },
        {
            "Material": "EXPIRED",
            "MRPPlant": "1710",
            "MRPArea": "1710",
            "MaterialProcurementCategory": "F",
        },
        {
            "Material": "INTERNAL",
            "MRPPlant": "1710",
            "MRPArea": "1710",
            "MaterialProcurementCategory": "E",
        },
        {
            "Material": "ACTIVE",
            "MRPPlant": "1710",
            "MRPArea": "1710",
            "MaterialProcurementCategory": "F",
        },
    ]
    coverage_rows = [
        {
            "Material": "ZERO",
            "MRPPlant": "1710",
            "MRPArea": "1710",
            "MaterialShortageQuantity": "0",
            "MaterialShortageEndDate": "9999-12-31",
        },
        {
            "Material": "EXPIRED",
            "MRPPlant": "1710",
            "MRPArea": "1710",
            "MaterialShortageQuantity": "10",
            "MaterialShortageEndDate": "2026-08-22",
        },
        {
            "Material": "INTERNAL",
            "MRPPlant": "1710",
            "MRPArea": "1710",
            "MaterialShortageQuantity": "20",
            "MaterialShortageEndDate": "9999-12-31",
        },
        {
            "Material": "ACTIVE",
            "MRPPlant": "1710",
            "MRPArea": "1710",
            "MaterialShortageQuantity": "30",
            "MaterialShortageEndDate": "/Date(253402214400000)/",
        },
    ]

    selected = select_qualified_coverage(
        master_rows,
        coverage_rows,
        as_of=date(2026, 8, 23),
    )

    assert [row["Material"] for row in selected] == ["ACTIVE"]


def test_campaign_accepts_an_optional_reusable_fixed_result() -> None:
    entries = _validate_campaign(
        {
            "schema_version": "1.0",
            "agents": [
                {
                    "module": "MM",
                    "agent_id": "inventory-health-balancing",
                    "case": "case.json",
                    "baseline": "baseline.json",
                    "fixed_result": "fixed/result.json",
                    "output": "campaign-final",
                }
            ],
        }
    )

    assert entries[0]["fixed_result"] == "fixed/result.json"
