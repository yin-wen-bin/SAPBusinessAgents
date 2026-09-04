from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from sap_business_agents_platform.agent_rules import evaluate_business_agent
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.models import RunCreate, RunInput, RunMode
from sap_business_agents_platform.restricted_artifacts import RestrictedArtifactStore
from sap_business_agents_platform.rules import prepare_fi_ledger_scope
from sap_business_agents_platform.security import LocalSecretProtector


ROOT = Path(__file__).resolve().parents[1]


def _complete(**values: object) -> dict[str, object]:
    return {"source_complete": True, "source_truncated": False, **values}


def _cash_inputs(
    receipts: list[dict[str, object]],
    *,
    bank_status: str = "complete",
    payment_rows: list[dict[str, object]] | None = None,
    invoice_rows: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    bank_complete = bank_status == "complete"
    return {
        "agent_id": "ar-cash-application",
        "run_input": {
            "company_code": "1710",
            "date_from": "2026-09-01",
            "date_to": "2026-09-04",
            "business_date": "2026-09-04",
        },
        "known_gaps": [],
        "evidence": {
            "leading_ledger": _complete(),
            "ledger_scope": _complete(ledger="0L", evidence_gaps=[]),
            "bank_receipts": {
                "status": bank_status,
                "receipts": receipts,
                "requested_scope": {"receipt_reference_supplied": False},
                "completeness": {
                    "source_complete": bank_complete,
                    "evidence_complete": bank_complete,
                    "total_rows": len(receipts) if bank_complete else None,
                },
            },
            "cash_scope": _complete(ledger="0L", evidence_gaps=[]),
            "cash_application_fi": _complete(
                step_results={
                    "subledger_payment_documents": _complete(
                        results=payment_rows or []
                    ),
                    "directly_cleared_invoices": _complete(
                        results=invoice_rows or []
                    ),
                    "subsequent_clearing_documents": _complete(results=[]),
                    "subsequently_cleared_invoices": _complete(results=[]),
                }
            ),
        },
    }


def test_cash_application_complete_empty_period_is_not_assessed() -> None:
    result = evaluate_business_agent(_cash_inputs([]))

    assert result["business_status"] == "normal"
    assert result["business_complete"] is True
    assert result["evidence_complete"] is True
    assert result["receipt_search_status"] == "not_found"
    assert result["cash_application_status"] == "not_assessed"
    assert result["source_receipt_count"] == 0


def test_fi_ledger_scope_reads_standard_sap_read_results() -> None:
    result = prepare_fi_ledger_scope(
        {
            "ledger_evidence": {
                "source_complete": True,
                "source_truncated": False,
                "data": {
                    "results": [
                        {
                            "Ledger": "0L",
                            "IsLeadingLedger": True,
                            "LedgerApplication": "FI",
                        }
                    ]
                },
            }
        }
    )

    assert result["status"] == "complete"
    assert result["ledger"] == "0L"
    assert result["evidence_gaps"] == []


def test_cash_application_reports_posting_state_before_not_reversed() -> None:
    result = evaluate_business_agent(
        _cash_inputs(
            [
                {
                    "statement_id": "STMT-FIXTURE",
                    "statement_item": "1",
                    "posting_status": "not_completed",
                    "reversal_status": "not_reversed",
                }
            ]
        )
    )

    assert result["receipt_results"][0]["reason_code"] == "not_completed"


def test_cash_application_aggregate_mixed_pending_and_unassessed_is_pending() -> None:
    result = evaluate_business_agent(
        _cash_inputs(
            [
                {
                    "statement_id": "STMT-FIXTURE",
                    "statement_item": "1",
                    "posting_status": "not_completed",
                    "reversal_status": "not_reversed",
                },
                {
                    "statement_id": "STMT-FIXTURE",
                    "statement_item": "2",
                    "posting_status": "completed",
                    "reversal_status": "not_reversed",
                },
            ]
        )
    )

    assert result["cash_application_status"] == "pending"


def test_cash_application_confirms_only_a_real_clearing_relationship() -> None:
    receipt = {
        "statement_id": "STMT-FIXTURE",
        "statement_item": "1",
        "value_date": "2026-09-02",
        "amount": "100.00",
        "currency": "USD",
        "posting_status": "completed",
        "reversal_status": "not_reversed",
        "related_accounting_document": {
            "subledger_document": "PAY-FIXTURE",
            "fiscal_year": "2026",
        },
    }
    payment = {
        "CompanyCode": "1710",
        "Ledger": "0L",
        "FiscalYear": "2026",
        "AccountingDocument": "PAY-FIXTURE",
        "AccountingDocumentItem": "1",
        "FinancialAccountType": "D",
        "Customer": "CUSTOMER-FIXTURE",
        "SpecialGLCode": "",
    }
    invoice = {
        "CompanyCode": "1710",
        "Ledger": "0L",
        "FiscalYear": "2026",
        "AccountingDocument": "INV-FIXTURE",
        "AccountingDocumentItem": "1",
        "FinancialAccountType": "D",
        "Customer": "CUSTOMER-FIXTURE",
        "SpecialGLCode": "",
        "ClearingAccountingDocument": "PAY-FIXTURE",
        "ClearingDocFiscalYear": "2026",
    }

    result = evaluate_business_agent(
        _cash_inputs([receipt], payment_rows=[payment], invoice_rows=[invoice])
    )

    assert result["business_status"] == "normal"
    assert result["cash_application_status"] == "confirmed"
    assert result["receipt_results"][0]["reason_code"] == (
        "sap_clearing_relationship_confirmed"
    )


def test_cash_application_discards_rows_from_partial_skill_output() -> None:
    result = evaluate_business_agent(
        _cash_inputs(
            [
                {
                    "statement_id": "UNTRUSTED",
                    "statement_item": "1",
                    "posting_status": "completed",
                    "reversal_status": "not_reversed",
                }
            ],
            bank_status="partial",
        )
    )

    assert result["business_status"] == "inconclusive"
    assert result["receipt_search_status"] == "partial"
    assert result["source_receipt_count"] is None
    assert result["receipt_results"] == []


def test_new_cash_output_satisfies_its_public_schema() -> None:
    manifest = json.loads(
        (ROOT / "agents/FI/ar-cash-application/agent.json").read_text(
            encoding="utf-8"
        )
    )
    result = evaluate_business_agent(_cash_inputs([]))["workflow_output"]

    Draft202012Validator(manifest["execution"]["outputSchema"]).validate(result)


def test_restricted_bank_fields_are_encrypted_and_public_projection_is_safe(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path / "platform.sqlite3")
    run_id = "run_fixture"
    store.create_run(
        run_id,
        RunCreate(mode=RunMode.free_query, query="fixture", input={}),
    )
    artifacts = RestrictedArtifactStore(tmp_path, store)
    public, private_refs = artifacts.materialize_skill_output(
        run_id=run_id,
        skill_id="sap-bank-receipt-evidence",
        output={
            "status": "complete",
            "source_profile": {"hash_key_id": "skill-account-key-v1"},
            "receipts": [
                {
                    "statement_id": "STMT-FIXTURE",
                    "statement_item": "1",
                    "payer_name": "PRIVATE-PAYER-FIXTURE",
                    "bank_reference": "PRIVATE-REFERENCE-FIXTURE",
                    "payer_account_hash": "a" * 64,
                }
            ],
        },
    )

    assert "payer_name" not in public["receipts"][0]
    assert "bank_reference" not in public["receipts"][0]
    assert public["receipts"][0]["payer_account_hash"]["domain"] == (
        "payer-bank-account"
    )
    artifact = private_refs[0]
    cipher = Path(artifact["path"]).read_bytes()
    assert b"PRIVATE-PAYER-FIXTURE" not in cipher
    assert b"PRIVATE-REFERENCE-FIXTURE" not in cipher
    assert artifacts.rows(run_id, artifact["artifact_id"])[0]["payer_name"] == (
        "PRIVATE-PAYER-FIXTURE"
    )


def test_secure_supplemental_input_can_be_submitted_without_chat_text() -> None:
    payload = RunInput.model_validate(
        {"sensitiveInputs": {"receipt_reference": "  PRIVATE-REFERENCE  "}}
    )

    assert payload.input is None
    assert payload.sensitive_inputs == {"receipt_reference": "PRIVATE-REFERENCE"}


def test_terminal_secret_crypto_shred_leaves_only_domain_separated_hmac(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path / "platform.sqlite3")
    run_id = "run_secret_fixture"
    store.create_run(
        run_id,
        RunCreate(mode=RunMode.free_query, query="fixture", input={}),
    )
    protector = LocalSecretProtector(tmp_path)
    secret_ref, protected, descriptor = protector.create_secret_ref(
        run_id=run_id,
        field="receipt_reference",
        value="PRIVATE-REFERENCE-FIXTURE",
        domain="bank-receipt-reference",
    )
    store.save_run_secret(
        secret_ref=secret_ref,
        run_id=run_id,
        field_name="receipt_reference",
        protected_value=protected,
        hmac_descriptor=descriptor,
    )

    binding = store.get_run_secret(run_id, "receipt_reference")
    assert b"PRIVATE-REFERENCE-FIXTURE" not in binding["protected_value"]
    assert descriptor["algorithm"] == "HMAC-SHA-256"
    assert descriptor["key_id"] == "sapba-business-reference-v1"
    assert descriptor["domain"] == "bank-receipt-reference"
    assert protector.reveal_run_secret(
        run_id=run_id,
        field="receipt_reference",
        protected_value=binding["protected_value"],
    ) == "PRIVATE-REFERENCE-FIXTURE"

    store.delete_run_secrets(run_id)
    try:
        store.get_run_secret(run_id, "receipt_reference")
    except KeyError:
        pass
    else:
        raise AssertionError("Terminal run secrets must be crypto-shredded.")
    assert b"PRIVATE-REFERENCE-FIXTURE" not in (tmp_path / "platform.sqlite3").read_bytes()
