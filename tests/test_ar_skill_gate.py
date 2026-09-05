from __future__ import annotations

from decimal import Decimal

import pytest

from scripts.build_ar_skill_gate import _dunning_projection, _prove_partition


def test_partition_proof_requires_disjoint_complete_union() -> None:
    whole = [{"K": "1", "V": "A"}, {"K": "2", "V": "B"}]
    _prove_partition(whole, [[whole[0]], [whole[1]]], ["K"])
    with pytest.raises(ValueError, match="overlap"):
        _prove_partition(whole, [[whole[0]], [whole[0], whole[1]]], ["K"])
    with pytest.raises(ValueError, match="union"):
        _prove_partition(whole, [[whole[0]], []], ["K"])


def test_dunning_gate_compares_decimal_semantics_not_formatting() -> None:
    source = {
        "customer": "1", "company_code": "1710", "dunning_area": "",
        "dunning_run_id": "A", "dunning_run_date": "2026-01-01",
        "effective_dunning_date": "2026-01-01", "fiscal_year": "2026",
        "accounting_document": "1", "accounting_document_item": "1",
        "dunning_level": "1", "old_dunning_level": "0",
        "dunning_blocking_reason": "", "dunning_reversal_status": "not_assessed",
        "special_gl_code": "", "amount": "100.00", "currency": "USD",
        "sequence_status": "ordered",
    }
    assert _dunning_projection(source)["amount"] == "100"
