"""Current shared-contract boundaries; no external model, SAP or service."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.runtime_contract import (
    RuntimeContractError, RuntimeResult, RuntimeSession, await_business,
    begin_cleanup, cleanup_binding, cleanup_deadline, deadline_scope,
)
from sap_business_agents_platform.runtime_policy import OPERATIONS, RETIRED_OPERATIONS


def test_active_operation_matrix_covers_both_driver_trace_families():
    from tests.runtime_trace_fixture import cases
    from tests.runtime_native_trace_fixture import OPERATIONS as native_operations
    active = set(cases()) - RETIRED_OPERATIONS
    assert active | set(native_operations) == set(OPERATIONS)
    assert not set(OPERATIONS) & RETIRED_OPERATIONS


def test_success_requires_explicit_cleanup_confirmation():
    result = RuntimeResult({"answer": "valid"}, RuntimeSession("codex", "owned"))
    with pytest.raises(RuntimeContractError, match="runtime_result_not_complete"):
        _ = result.final_response


def test_cleanup_cutoff_is_shared_not_renewed(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr("sap_business_agents_platform.runtime_contract.time.monotonic", lambda: clock["now"])
    with cleanup_binding():
        assert begin_cleanup() == 110
        clock["now"] = 106
        assert begin_cleanup() == cleanup_deadline() == 110


def test_cancel_swallowing_terminal_cannot_complete_expired_request():
    async def late():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            return {"answer": "too late"}
    async def scenario():
        with deadline_scope(time.monotonic() + 0.01):
            with pytest.raises(RuntimeContractError, match="runtime_deadline_exceeded"):
                await await_business(late())
    asyncio.run(scenario())


def test_invalid_native_output_does_not_enter_platform_history(tmp_path, monkeypatch):
    from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner
    planner = WorkBuddyPlanner(tmp_path, "offline")
    async def native(*args, **kwargs):
        return {"unexpected": "not-a-terminal"}, "native-id"
    monkeypatch.setattr(planner, "_structured_turn", native)
    async def scenario():
        with deadline_scope(time.monotonic() + 20):
            async with planner._driver.client() as client:
                thread = await client.thread_start()
                with pytest.raises(RuntimeContractError, match="runtime_report_validation_failed"):
                    await thread.run("offline", output_schema={"type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["answer"], "additionalProperties": False})
                assert thread.state["history"] == []
                assert "native_session_id" not in thread.state
    asyncio.run(scenario())


def test_material_understanding_does_not_invent_catalogue_evaluation():
    import json
    from sap_business_agents_platform.runtime_prompts import ROLE_MATCHING_OUTPUT_SCHEMA, _decode_role_matching_output
    from sap_business_agents_platform.runtime_role_contract import COLLECTIONS, canonical_output
    analysis = {name: [] for name in COLLECTIONS}
    analysis["document_issues"] = []
    analysis["non_sap_operation_count"] = 0
    envelope = {"analysis_json": json.dumps(analysis), "summary_zh": "材料", "summary_en": "Material"}
    native = {**envelope, "analysis_json": analysis}
    encoded = canonical_output(native, ROLE_MATCHING_OUTPUT_SCHEMA, operation="analyze_role_matching")
    for raw in (envelope, encoded):
        result = _decode_role_matching_output(SimpleNamespace(final_response=json.dumps(raw)), {"documents": []})
        assert result == {**analysis, "summary": {"zh": "材料", "en": "Material"}}
        assert "catalog_evaluation" not in result


@pytest.mark.parametrize("native", [False, True])
def test_shared_role_decoder_rejects_unknown_document_references(native):
    import json
    from sap_business_agents_platform.runtime_prompts import ROLE_MATCHING_OUTPUT_SCHEMA, _decode_role_matching_output
    from sap_business_agents_platform.runtime_role_contract import COLLECTIONS, canonical_output
    analysis = {name: [] for name in COLLECTIONS}
    analysis["document_issues"] = []
    analysis["operations"] = [{"operation_id": "one", "evidence_refs": [
        {"document_id": "unknown", "chunk_id": "unregistered"}]}]
    envelope = {"analysis_json": analysis if native else json.dumps(analysis),
        "summary_zh": "材料", "summary_en": "Material"}
    if native:
        envelope = canonical_output(envelope, ROLE_MATCHING_OUTPUT_SCHEMA, operation="analyze_role_matching")
    with pytest.raises(RuntimeContractError, match="role_matching_evidence_ref_invalid") as rejected:
        _decode_role_matching_output(SimpleNamespace(final_response=json.dumps(envelope)),
            {"documents": [{"document_id": "known", "chunks": [{"chunk_id": "registered"}]}]})
    assert rejected.value.detail["validation_issues"] == [{
        "code": "role_matching_evidence_ref_invalid", "path": "/operations/0/evidence_refs",
        "constraint": "known_document_reference"}]


def test_worker_startup_uses_original_deadline_and_safe_timeout(tmp_path, monkeypatch):
    from tests.test_workbuddy_isolation import supervisor
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
    owner, binding = supervisor(tmp_path, monkeypatch)
    async def slow_start(*args, **kwargs):
        await asyncio.sleep(30)
        raise AssertionError("late worker launch")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", slow_start)
    with pytest.raises(WorkBuddyError, match="workbuddy_deadline_exceeded"):
        asyncio.run(owner.run(task_id="startup", snapshot=binding, operation="review_workflow",
            payload={}, seconds=0.01))
    assert not owner.active and not owner.pending()


@pytest.mark.parametrize("change,code", [
    ("login", "workbuddy_authentication_required"),
    ("login_environment", "workbuddy_authentication_required"),
    ("model", "runtime_model_check_required"),
    ("model_digest", "runtime_model_check_required"),
    ("sdk", "workbuddy_environment_digest_mismatch"),
])
def test_private_validation_cannot_bypass_login_model_or_sdk(tmp_path, monkeypatch, change, code):
    from tests.test_workbuddy_isolation import supervisor, verified_binding
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
    owner, binding = supervisor(tmp_path, monkeypatch)
    binding = verified_binding(owner, binding)
    if change == "login":
        owner.environment.mutate(lambda state: state.update(authenticated=False))
    elif change == "login_environment":
        owner.environment.mutate(lambda state: state.update(authentication_environment="other"))
    elif change == "model":
        owner.environment.mutate(lambda state: state["models"]["fixture-model"].update(compatible=False))
    elif change == "model_digest":
        binding["model_check_digest"] = "stale"
    else:
        binding["sdk_fingerprint"] = "changed"
    with pytest.raises(WorkBuddyError, match=code):
        with owner.verification(binding, {"free_query"}):
            raise AssertionError("invalid private verification admitted")
    assert not owner.active


def test_private_verification_rechecks_login_and_formal_identity(tmp_path, monkeypatch):
    from tests.test_workbuddy_isolation import supervisor, verified_binding
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
    owner, binding = supervisor(tmp_path, monkeypatch)
    binding = verified_binding(owner, binding)
    with owner.verification(binding, {"sample_discovery"}):
        assert owner.check_operation(binding, "sample_discovery")
        owner.environment.mutate(lambda state: state.update(authenticated=False))
        with pytest.raises(WorkBuddyError, match="workbuddy_authentication_required"):
            owner.check_operation(binding, "sample_discovery")
    binding = verified_binding(owner, binding)
    owner.environment.mutate(lambda state: state["models"].update({"default-model": state["models"]["fixture-model"]}))
    binding["model"] = "default-model"
    with pytest.raises(WorkBuddyError, match="workbuddy_model_alias_not_allowed"):
        with owner.verification(binding, {"acceptance_baseline"}):
            raise AssertionError("formal route alias admitted")


@pytest.mark.parametrize("operation", sorted(RETIRED_OPERATIONS))
def test_frozen_execution_cannot_bypass_operation_retirement(operation):
    from sap_business_agents_platform.runtime_contract import RuntimeRequest, execute_frozen
    class Driver:
        provider_id = "codex"
        async def _execute(self, *args):
            raise AssertionError("retired SDK dispatch")
    request = RuntimeRequest("codex", operation, "historical", {}, "offline", {}, deadline=time.monotonic() + 10)
    with pytest.raises(RuntimeContractError, match="runtime_operation_retired"):
        asyncio.run(execute_frozen(Driver(), request))


@pytest.mark.parametrize("operation", sorted(RETIRED_OPERATIONS))
def test_injected_router_cannot_restore_retired_chain(operation):
    from sap_business_agents_platform.runtime import StaticRuntimeRouter
    async def forbidden(*args, **kwargs):
        raise AssertionError("retired chain dispatched")
    router = StaticRuntimeRouter(SimpleNamespace(**{operation: forbidden}))
    assert not router.supports(operation)
    with pytest.raises(RuntimeContractError, match="runtime_operation_retired"):
        asyncio.run(getattr(router, operation)())
