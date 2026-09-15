from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from sap_business_agents_platform.acceptance import (
    CanonicalTestCase,
    canonical_hash,
    validate_direct_baseline,
)
from sap_business_agents_platform.agent_acceptance_campaigns import AgentAcceptanceJobs, _contract
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.models import AgentAcceptanceCampaignRequest


def _seed(store: RunStore, tmp_path) -> dict:
    draft = {
        "draft_id": "draft-acceptance",
        "agent_id": "acceptance-test",
        "source_type": "blank",
        "status": "draft",
        "revision": 4,
        "path": str(tmp_path / "draft"),
        "metadata": {},
    }
    package = {
        "manifest": {
            "slug": "acceptance-test",
            "module": "FI",
            "version": "0.1.0",
            "execution": {"inputSchema": {"type": "object"}, "steps": []},
        },
        "rules": None,
    }
    store.save_agent_authoring_draft(draft, package=package)
    return draft


def _seed_runnable(store: RunStore, tmp_path) -> dict:
    draft = {
        "draft_id": "draft-runnable",
        "agent_id": "acceptance-test",
        "source_type": "blank",
        "status": "draft",
        "revision": 4,
        "path": str(tmp_path / "runnable"),
        "metadata": {
            "trial": {
                "revision": 4,
                "verdict": "PASS",
                "business_output_available": True,
                "output_schema_valid": True,
                "read_only_audit": True,
            }
        },
    }
    package = {
        "manifest": {
            "slug": "acceptance-test",
            "module": "FI",
            "version": "0.1.0",
            "validation": {"acceptanceMode": "three_stage"},
            "execution": {
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"company_code": {"type": "string"}},
                    "required": ["company_code"],
                },
                "acceptance": {
                    "schemaVersion": "2.0",
                    "businessKeys": ["company_code"],
                    "facts": ["business_status"],
                    "metrics": [],
                },
                "steps": [],
            },
        },
        "rules": None,
    }
    store.save_agent_authoring_draft(draft, package=package)
    return draft


class _Lifecycle:
    def __init__(self, *, validation_must_not_run: bool = False, reused: bool = False) -> None:
        self.validation_must_not_run = validation_must_not_run
        self.reused = reused

    def require_technical_identity(self, draft):
        assert draft["agent_id"] == "acceptance-test"

    def validate(self, draft_id, *, expected_revision):
        if self.validation_must_not_run:
            raise AssertionError("idempotent recovery must precede mutable validation")
        return {"static_checks": {
            "errors": [],
            "checks": ["manifest"],
            "presentation_contract": {"status": "ready", "issues": []},
        }}

    def _platform_changes_pending(self, draft_id):
        return False

    def _formal_acceptance(self, draft, package):
        return {"verdict": "PASS", "reused_validation": True} if self.reused else None


class _Runtime:
    def runtime_snapshot(self):
        return {
            "provider_id": "codex",
            "sdk_id": "openai-codex",
            "version": "0.147.0",
            "cli_version": "0.147.0",
            "model": "gpt-5.6-sol",
            "model_source": "system_settings",
            "reasoning_effort": "high",
            "reasoning_effort_source": "system_settings",
            "runtime_configuration_revision": 1,
            "configuration_digest": "runtime-current",
            "capabilities": ["planning", "tool_calling"],
            "selected_at": "2026-09-13T00:00:00Z",
        }


class _SecretProtector:
    @staticmethod
    def hmac_descriptor(value, *, domain):
        return {"algorithm": "HMAC-SHA256", "key_id": "test", "digest": canonical_hash([domain, value])}


class _Coordinator:
    secret_protector = _SecretProtector()

    async def cancel(self, run_id):
        return True


def test_contract_derives_boolean_facts_and_business_status_domain_from_output_schema():
    manifest = {
        "execution": {
            "acceptance": {
                "businessKeys": ["purchase_order"],
                "facts": ["delivery_complete", "source_complete"],
                "metrics": [],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "business_status": {
                        "type": "string",
                        "enum": ["normal", "attention", "inconclusive"],
                    },
                    "source_complete": {"type": "boolean"},
                    "records": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "delivery_complete": {"type": "boolean"},
                            },
                        },
                    },
                },
            },
        }
    }

    value = _contract(manifest)

    assert value["boolean_fields"] == ["delivery_complete", "source_complete"]
    assert value["business_status_values"] == ["normal", "attention", "inconclusive"]


class _AnchorBroker:
    def __init__(self, raw):
        self.raw = raw

    def _read_evidence(self, run_id, evidence_ref):
        return self.raw, {"source_complete": True}


class _AnchorSap:
    def __init__(self, raw):
        self.raw = raw

    async def validate_plan(self, plan, query):
        return {"ok": True, "normalized_plan": plan}

    async def execute_plan(self, plan, query, conversation_id=None):
        return self.raw


def _campaign(store: RunStore, tmp_path, *, request_id: str = "request-1") -> tuple[dict, dict]:
    draft = _seed(store, tmp_path)
    operation = store.reserve_agent_operation(draft["draft_id"], 4, "formal_acceptance")
    value = store.create_agent_acceptance_campaign(
        campaign={
            "campaign_id": "campaign-test",
            "draft_id": draft["draft_id"],
            "revision": 4,
            "operation_id": operation["operation_id"],
            "request_id": request_id,
            "request_hash": "sha256:" + "1" * 64,
            "agent_id": draft["agent_id"],
            "acceptance_mode": "three_stage",
            "runtime": {"model": "gpt-5.6-sol"},
            "digests": {"execution_digest": "sha256:" + "2" * 64},
            "artifact_dir": str(tmp_path / "artifacts"),
        },
        cases=[
            {
                "case_id": "case-1",
                "input": {"company_code": "1710"},
                "sensitive_fingerprints": {
                    "receipt_reference": {"algorithm": "HMAC-SHA256", "digest": "masked"}
                },
            }
        ],
    )
    return value, operation


def test_acceptance_request_requires_one_to_five_unique_cases() -> None:
    valid = {
        "expectedRevision": 4,
        "requestId": "request-1",
        "cases": [{"caseId": "current-input", "input": {}}],
    }
    assert AgentAcceptanceCampaignRequest.model_validate(valid).cases[0].case_id == "current-input"
    with pytest.raises(ValidationError):
        AgentAcceptanceCampaignRequest.model_validate({**valid, "cases": []})
    with pytest.raises(ValidationError):
        AgentAcceptanceCampaignRequest.model_validate(
            {**valid, "cases": valid["cases"] * 6}
        )
    with pytest.raises(ValidationError, match="unique"):
        AgentAcceptanceCampaignRequest.model_validate(
            {**valid, "cases": valid["cases"] * 2}
        )
    with pytest.raises(ValidationError):
        AgentAcceptanceCampaignRequest.model_validate(
            {**valid, "cases": [{"caseId": "contains spaces", "input": {}}]}
        )


def test_campaign_store_persists_cases_events_and_only_sensitive_fingerprints(tmp_path) -> None:
    store = RunStore(tmp_path / "campaign.sqlite3")
    created, _ = _campaign(store, tmp_path)

    current = store.get_agent_acceptance_campaign(created["draft_id"], created["campaign_id"])

    assert current["status"] == "queued"
    assert current["cases"][0]["input"] == {"company_code": "1710"}
    assert current["cases"][0]["sensitive_fingerprints"]["receipt_reference"]["digest"] == "masked"
    assert store.agent_acceptance_events_after(created["campaign_id"])[0]["type"] == "campaign_queued"
    with store._connect() as connection:
        persisted = connection.execute(
            "SELECT input_json, sensitive_fingerprints_json FROM agent_acceptance_cases"
        ).fetchone()
    assert "receipt_reference" in persisted["sensitive_fingerprints_json"]
    assert "actual-secret-value" not in (
        persisted["input_json"] + persisted["sensitive_fingerprints_json"]
    )


def test_campaign_request_id_is_idempotent_but_conflicting_hash_is_rejected(tmp_path) -> None:
    store = RunStore(tmp_path / "campaign.sqlite3")
    created, operation = _campaign(store, tmp_path)
    same = store.create_agent_acceptance_campaign(
        campaign={
            **created,
            "operation_id": operation["operation_id"],
            "request_hash": created["request_hash"],
        },
        cases=[],
    )
    assert same["campaign_id"] == created["campaign_id"]
    with pytest.raises(ValueError, match="agent_acceptance_request_conflict"):
        store.create_agent_acceptance_campaign(
            campaign={**created, "request_hash": "sha256:" + "9" * 64},
            cases=[],
        )


def test_job_level_request_retry_recovers_before_static_validation_or_operation_lock(tmp_path) -> None:
    store = RunStore(tmp_path / "jobs.sqlite3")
    draft = _seed_runnable(store, tmp_path)
    payload = AgentAcceptanceCampaignRequest.model_validate(
        {
            "expectedRevision": 4,
            "requestId": "retry-request",
            "cases": [{"caseId": "case-1", "input": {"company_code": "1710"}}],
        }
    )
    request_hash = canonical_hash(
        {
            "revision": 4,
            "cases": [
                {
                    "case_id": "case-1",
                    "input": {"company_code": "1710"},
                    "sensitive_fingerprints": {},
                }
            ],
        }
    )
    operation = store.reserve_agent_operation(draft["draft_id"], 4, "formal_acceptance")
    store.create_agent_acceptance_campaign(
        campaign={
            "campaign_id": "campaign-retry",
            "draft_id": draft["draft_id"],
            "revision": 4,
            "operation_id": operation["operation_id"],
            "request_id": payload.request_id,
            "request_hash": request_hash,
            "agent_id": draft["agent_id"],
            "acceptance_mode": "three_stage",
            "runtime": _Runtime().runtime_snapshot(),
            "digests": {},
            "artifact_dir": str(tmp_path / "retry-artifacts"),
        },
        cases=[{"case_id": "case-1", "input": {"company_code": "1710"}}],
    )
    jobs = AgentAcceptanceJobs(
        SimpleNamespace(data_root=tmp_path), store,
        _Lifecycle(validation_must_not_run=True), _Coordinator(), _Runtime(),
    )

    recovered = jobs.start(draft["draft_id"], payload)

    assert recovered["campaign_id"] == "campaign-retry"
    assert recovered["active"] is True


def test_documentation_only_reused_pass_does_not_start_a_live_campaign(tmp_path) -> None:
    store = RunStore(tmp_path / "reuse.sqlite3")
    draft = _seed_runnable(store, tmp_path)
    jobs = AgentAcceptanceJobs(
        SimpleNamespace(data_root=tmp_path), store,
        _Lifecycle(reused=True), _Coordinator(), _Runtime(),
    )
    payload = AgentAcceptanceCampaignRequest.model_validate(
        {
            "expectedRevision": 4,
            "requestId": "reuse-request",
            "cases": [{"caseId": "case-1", "input": {"company_code": "1710"}}],
        }
    )

    with pytest.raises(Exception) as captured:
        jobs.start(draft["draft_id"], payload)

    assert getattr(captured.value, "code", None) == "agent_acceptance_reused"
    assert store.list_agent_acceptance_campaigns(draft["draft_id"]) == []
    assert store.get_agent_operation(draft["draft_id"]) is None


def test_cancelling_before_worker_starts_still_releases_formal_acceptance_lock(tmp_path) -> None:
    async def scenario():
        store = RunStore(tmp_path / "cancel.sqlite3")
        draft = _seed_runnable(store, tmp_path)
        jobs = AgentAcceptanceJobs(
            SimpleNamespace(data_root=tmp_path), store,
            _Lifecycle(), _Coordinator(), _Runtime(),
        )
        payload = AgentAcceptanceCampaignRequest.model_validate(
            {
                "expectedRevision": 4,
                "requestId": "cancel-request",
                "cases": [{"caseId": "case-1", "input": {"company_code": "1710"}}],
            }
        )
        started = jobs.start(draft["draft_id"], payload)

        cancelled = await jobs.cancel(draft["draft_id"], started["campaign_id"])

        assert cancelled["status"] == "cancelled"
        assert cancelled["report"]["verdict"] == "NOT_TESTED"
        assert cancelled["cases"][0]["status"] == "cancelled"
        assert cancelled["cases"][0]["result"]["verdict"] == "NOT_TESTED"
        assert (tmp_path / "agent-acceptance" / cancelled["campaign_id"] / "report.json").is_file()
        assert (tmp_path / "agent-acceptance" / cancelled["campaign_id"] / "report.md").is_file()
        assert store.get_agent_operation(draft["draft_id"]) is None

    asyncio.run(scenario())


def test_source_anchor_replays_get_plan_and_ignores_only_volatile_response_fields(tmp_path) -> None:
    async def scenario():
        store = RunStore(tmp_path / "anchors.sqlite3")
        created, _ = _campaign(store, tmp_path)
        expected = {
            "case_id": "original",
            "elapsed_ms": 10,
            "data": {"results": [{"Document": "1"}], "source_complete": True},
            "source_complete": True,
        }
        replay = {**expected, "case_id": "replay", "elapsed_ms": 99}
        coordinator = _Coordinator()
        coordinator.harness = SimpleNamespace(broker=_AnchorBroker(expected))
        coordinator.sap_read = _AnchorSap(replay)
        jobs = AgentAcceptanceJobs(
            SimpleNamespace(data_root=tmp_path), store, _Lifecycle(), coordinator, _Runtime(),
        )
        baseline_run = {
            "result": {
                "tool_calls": [
                    {
                        "tool": "sap_query_execute",
                        "status": "completed",
                        "evidence_ref": "ev_0123456789abcdef01234567",
                        "input": {"plan": {"http_method": "GET"}, "query": "test"},
                    }
                ]
            }
        }

        anchor = await jobs._capture_source_anchor(
            created["draft_id"], created["campaign_id"], "baseline-run", baseline_run,
            position="before",
        )

        assert anchor["verdict"] == "PASS"
        assert anchor["source_count"] == 1
        assert store.agent_acceptance_events_after(created["campaign_id"])[-1]["type"] == "source_anchor_captured"

    asyncio.run(scenario())


def test_source_anchor_detects_business_data_change(tmp_path) -> None:
    async def scenario():
        store = RunStore(tmp_path / "changed-anchor.sqlite3")
        created, _ = _campaign(store, tmp_path)
        expected = {"data": {"results": [{"Document": "1"}], "source_complete": True}}
        replay = {"data": {"results": [{"Document": "2"}], "source_complete": True}}
        coordinator = _Coordinator()
        coordinator.harness = SimpleNamespace(broker=_AnchorBroker(expected))
        coordinator.sap_read = _AnchorSap(replay)
        jobs = AgentAcceptanceJobs(
            SimpleNamespace(data_root=tmp_path), store, _Lifecycle(), coordinator, _Runtime(),
        )
        baseline_run = {"result": {"tool_calls": [{
            "tool": "sap_query_execute", "evidence_ref": "ev_changed",
            "input": {"plan": {"http_method": "GET"}, "query": "test"},
        }]}}

        anchor = await jobs._capture_source_anchor(
            created["draft_id"], created["campaign_id"], "baseline-run", baseline_run,
            position="after",
        )

        assert anchor["verdict"] == "CHANGED"
        assert anchor["expected_hash"] != anchor["observed_hash"]

    asyncio.run(scenario())


def test_formal_report_binding_is_revision_bound_and_releases_operation(tmp_path) -> None:
    store = RunStore(tmp_path / "campaign.sqlite3")
    created, operation = _campaign(store, tmp_path)
    report = {
        "type": "formal_acceptance",
        "verdict": "PASS",
        "executable": True,
        "fixedAgentComparison": "MATCH",
        "freeQueryComparison": "MATCH",
        "blockingLimitations": [],
    }
    digest = canonical_hash(report)

    assert store.bind_agent_acceptance_report(
        draft_id=created["draft_id"],
        campaign_id=created["campaign_id"],
        revision=4,
        operation_id=operation["operation_id"],
        report=report,
        report_digest=digest,
        campaign_status="passed",
    )
    assert store.get_agent_authoring_draft(created["draft_id"])["validation"] == report
    assert store.get_agent_operation(created["draft_id"]) is None
    assert store.get_agent_acceptance_campaign(created["draft_id"], created["campaign_id"])["status"] == "passed"


def test_restart_marks_active_campaign_interrupted_and_not_tested(tmp_path) -> None:
    store = RunStore(tmp_path / "campaign.sqlite3")
    created, _ = _campaign(store, tmp_path)

    store.recover_agent_operations()

    recovered = store.get_agent_acceptance_campaign(created["draft_id"], created["campaign_id"])
    assert recovered["status"] == "interrupted"
    assert recovered["report"]["verdict"] == "NOT_TESTED"
    assert recovered["cases"][0]["status"] == "interrupted"
    assert recovered["cases"][0]["result"]["verdict"] == "NOT_TESTED"
    assert recovered["report"]["error"]["code"] == "agent_acceptance_interrupted"
    assert recovered["report_digest"].startswith("sha256:")
    assert store.get_agent_operation(created["draft_id"]) is None


def test_sdk_direct_baseline_v4_requires_isolation_read_only_evidence_and_case_shape() -> None:
    case = CanonicalTestCase.from_dict(
        {
            "schema_version": "2.0",
            "case_id": "live-1",
            "agent_id": "acceptance-test",
            "question": {"zh": "测试", "en": "Test"},
            "input": {},
            "business_conditions": {},
            "expected_grain": ["document"],
            "expected_output": {
                "record_fields": ["document", "business_status"],
                "metric_ids": ["document_count"],
                "minimum_primary_evidence_rows": 0,
                "allow_empty_result": True,
                "evidence_scope": "complete",
            },
        }
    )
    normalized = {
        "records": [{"document": "1", "business_status": "normal"}],
        "metrics": {"document_count": 1},
        "business_status": "normal",
        "source_complete": True,
        "evidence_complete": True,
        "business_complete": True,
        "evidence_gap_codes": [],
        "limitations": [],
    }
    baseline = {
        "schema_version": "4.0",
        "runtime": "codex_sdk_direct_sap",
        "used_sap_business_agents": False,
        "candidate_access": False,
        "runtime_snapshot": {
            "provider_id": "codex",
            "sdk_id": "openai-codex",
            "model": "gpt-5.6-sol",
            "configuration_digest": "runtime-current",
        },
        "sources": [
            {
                "tool": "sap_query_execute",
                "http_method": "GET",
                "semantic_read_only": False,
                "read_only": True,
                "evidence_ref": "ev_0123456789abcdef01234567",
            }
        ],
        "normalized_result": normalized,
        "result_hash": canonical_hash(normalized),
    }

    assert validate_direct_baseline(baseline, case) == normalized
    with pytest.raises(ValueError, match="candidate_access=false"):
        validate_direct_baseline({**baseline, "candidate_access": True}, case)
    with pytest.raises(ValueError, match="must be read-only"):
        invalid_source = {**baseline, "sources": [{**baseline["sources"][0], "read_only": False}]}
        validate_direct_baseline(invalid_source, case)
    with pytest.raises(ValueError, match="missing expected fields"):
        invalid_result = {**normalized, "records": [{"document": "1"}]}
        validate_direct_baseline(
            {**baseline, "normalized_result": invalid_result, "result_hash": canonical_hash(invalid_result)},
            case,
        )
