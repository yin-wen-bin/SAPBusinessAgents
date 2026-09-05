from __future__ import annotations

from scripts.validate_restricted_artifact_frontend import (
    _contains_any,
    _fixed_run_identity,
    _sensitive_values,
)


def test_frontend_acceptance_collects_only_declared_sensitive_values() -> None:
    rows = [
        {
            "payer_name": "Private Payer",
            "bank_reference": "REF-123",
            "amount": "100.00",
            "short": "x",
        }
    ]

    assert _sensitive_values(rows) == ["Private Payer", "REF-123"]
    assert _contains_any({"message": "Private Payer"}, _sensitive_values(rows)) is True
    assert _contains_any({"amount": "100.00"}, _sensitive_values(rows)) is False


def test_frontend_acceptance_recognizes_dunning_restricted_fields() -> None:
    values = _sensitive_values(
        [{"document_reference_id": "DUNNING-REF", "one_time_account": "OT-1"}]
    )

    assert values == ["DUNNING-REF", "OT-1"]


def test_frontend_acceptance_reads_fixed_identity_from_acceptance_artifact() -> None:
    assert _fixed_run_identity(
        {
            "case": {"agent_id": "ar-cash-application"},
            "fixed_agent": {"run_id": "acceptance_123"},
            "hashes": {"agent_execution_digest": "sha256:" + "a" * 64},
        }
    ) == (
        "acceptance_123",
        "ar-cash-application",
        "sha256:" + "a" * 64,
    )
