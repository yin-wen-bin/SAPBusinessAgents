from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.agent_authoring import apply_package_edits, package_changes
from sap_business_agents_platform.agent_lifecycle import AgentLifecycleError, AgentLifecycleService
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform.models import AgentAuthoringCreate, AgentDraftUpdate, AgentFeedbackRequest, AgentPublishRequest, RunStatus


def _service(tmp_path):
    settings = Settings(repository_root=tmp_path, data_root=tmp_path / ".local-data", draft_root=tmp_path / ".prototype" / "authoring")
    store = RunStore(settings.database_path)
    service = AgentLifecycleService(settings, store, AgentRepository(tmp_path / "agents"), SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    return service, store, settings


class Runtime:
    def __init__(self, action="clarify", mutate=None, delay=0):
        self.action, self.mutate, self.delay = action, mutate, delay
        self.calls = []
        self.bindings = []

    def snapshot(self):
        return {"provider_id": "codex", "model": "gpt-5.6-sol"}

    @contextmanager
    def pin(self, provider, model):
        self.bindings.append((provider, model))
        yield

    def supports(self, capability):
        return True

    async def review_agent_feedback(self, **kwargs):
        self.calls.append(kwargs)
        await asyncio.sleep(self.delay)
        result = {"action": self.action, "summary": {"zh": "请确认范围", "en": "Confirm the scope"}, "thread_id": "isolated-thread"}
        if self.action == "revise_agent":
            package = copy.deepcopy(kwargs["package"])
            if self.mutate:
                self.mutate(package)
            result["package"] = package
        return result


def create(service):
    draft = asyncio.run(service.create(AgentAuthoringCreate(source="blank", agentId="author-test", module="SD")))
    return service.set_technical_id(draft["draft_id"], SimpleNamespace(expected_revision=1, agent_id=draft["agent_id"]))


def feedback_payload(draft, text="请说明并调整", request="req-1"):
    return AgentFeedbackRequest(baseTurn=max((item["turn"] for item in draft["conversation"]), default=0), baseRevision=draft["revision"], feedback=text, requestId=request)


def test_runtime_optional_input_dependency_is_rejected_before_revision(tmp_path):
    service, _, _ = _service(tmp_path)
    draft = create(service)
    def mutate(package):
        execution = package["manifest"]["execution"]
        execution["inputSchema"]["properties"]["optional_filter"] = {
            "type": "string", "title": {"zh": "可选筛选", "en": "Optional filter"}}
        execution["steps"][0].setdefault("request", {})["optional_probe"] = "{{input.optional_filter}}"
    service.runtime = Runtime("revise_agent", mutate)
    _, result = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    assert result["revision"] == draft["revision"]
    failure = result["conversation"][-1]
    assert failure["status"] == "failed"
    assert failure["decision"]["validation_issues"][0]["code"] == "agent_optional_input_unguarded"


async def send_and_wait(service, draft, payload):
    submitted = await service.submit_feedback(draft["draft_id"], payload)
    await service._feedback_tasks[submitted["task_id"]]
    return submitted, service.get_draft(draft["draft_id"])


def test_new_codex_turn_pins_tool_policy_without_changing_past_turns(tmp_path):
    service, _, _ = _service(tmp_path)
    draft = create(service)
    service.runtime = Runtime('reply')
    before = copy.deepcopy(draft['conversation'])
    _, result = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    policy = {'version': 2, 'mode': 'full_access'}
    assert service.runtime.calls[-1]['tool_policy'] == policy
    assert result['conversation'][-1]['decision']['authoring_policy'] == policy
    assert result['conversation'][:-1] == before


def test_diff_preserves_values_text_binary_and_stable_step_identity():
    before = {"manifest": {"workflow": [{"id": "a", "title": "old"}, {"id": "b"}], "zero": None}, "readme": "first\nold\n", "rules": None, "binary_files": {"image.bin": "YQ=="}}
    after = {"manifest": {"workflow": [{"id": "b"}, {"id": "a", "title": "new"}], "new": ""}, "readme": "first\nnew\n", "rules": "def evaluate(): pass", "binary_files": {"image.bin": "Yg=="}}
    changes = package_changes(before, after)
    keyed = {item["path"]: item for item in changes}
    assert keyed["/manifest/workflow/@a/title"]["before"] == "old"
    assert keyed["/manifest/workflow"]["change"] == "reordered"
    assert keyed["/manifest/zero"]["before_exists"] and not keyed["/manifest/zero"]["after_exists"]
    assert keyed["/manifest/new"]["after"] == ""
    assert "-old" in keyed["/readme"]["unified_diff"] and "+new" in keyed["/readme"]["unified_diff"]
    assert keyed["/binary_files/image.bin"]["before"]["byte_count"] == 1
    assert "YQ==" not in json.dumps(changes)


def test_complete_undo_removes_rules_and_files_and_writes_single_turn(tmp_path):
    service, store, _ = _service(tmp_path)
    draft = create(service)
    revised = service.update(draft["draft_id"], AgentDraftUpdate(expectedRevision=1, manifest=draft["package"]["manifest"], readme="Changed", rules="def evaluate(inputs): return {}"))
    restored = service.undo(draft["draft_id"], expected_revision=2, target_revision=1)
    assert restored["revision"] == 3
    assert restored["package"] == draft["package"]
    assert not (Path(restored["path"]) / "rules.py").exists()
    assert len(restored["conversation"]) == len(revised["conversation"]) + 1
    assert restored["conversation"][-1]["kind"] == "undo"
    assert service.get_diff(draft["draft_id"], 1, 3)["changes"] == []
    assert store.get_agent_authoring_revision(draft["draft_id"], 2)["package"]["rules"]


def test_failed_database_save_restores_entire_package(tmp_path, monkeypatch):
    service, store, _ = _service(tmp_path)
    draft = create(service)
    original = (Path(draft["path"]) / "README.md").read_bytes()
    monkeypatch.setattr(store, "save_agent_authoring_draft", lambda *a, **kw: (_ for _ in ()).throw(ValueError("agent_draft_conflict")))
    with pytest.raises(ValueError):
        service.update(draft["draft_id"], AgentDraftUpdate(expectedRevision=1, manifest=draft["package"]["manifest"], readme="new", rules=""))
    assert (Path(draft["path"]) / "README.md").read_bytes() == original
    assert store.get_agent_authoring_draft(draft["draft_id"])["revision"] == 1
    assert store.get_agent_operation(draft["draft_id"]) is None


@pytest.mark.parametrize("action", ["clarify", "reply", "revise_agent"])
def test_feedback_without_actual_change_keeps_revision_and_bound_model(tmp_path, action):
    service, store, _ = _service(tmp_path)
    service.runtime = Runtime(action)
    draft = create(service)
    submitted, result = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    assert result["revision"] == 1
    assert result["conversation"][-1]["decision"]["action"] == action
    assert result["conversation"][-1]["user_message"] == "请说明并调整"
    assert service.runtime.bindings == [("codex", "gpt-5.6-sol")]
    assert store.get_agent_operation(draft["draft_id"]) is None
    # Request retries return the original result, rather than append another turn.
    duplicate = asyncio.run(service.submit_feedback(draft["draft_id"], feedback_payload(draft)))
    assert duplicate["turn"] == submitted["turn"]
    assert len(service.runtime.calls) == 1


def test_clarification_then_revision_preserves_history_and_only_one_new_turn(tmp_path):
    service, _, _ = _service(tmp_path)
    service.runtime = Runtime()
    draft = create(service)
    _, clarified = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    service.runtime.action = "revise_agent"
    service.runtime.mutate = lambda package: package["manifest"]["title"].update(zh="更新后的名称")
    submitted, result = asyncio.run(send_and_wait(service, clarified, feedback_payload(clarified, "是的，请修改", "req-2")))
    assert result["revision"] == 2
    assert result["package"]["manifest"]["title"]["zh"] == "更新后的名称"
    assert len(result["conversation"]) == len(clarified["conversation"]) + 1
    assert service.runtime.calls[-1]["history"][-1]["action"] == "clarify"
    duplicate = asyncio.run(service.submit_feedback(draft["draft_id"], feedback_payload(clarified, "是的，请修改", "req-2")))
    assert duplicate["task_id"] == submitted["task_id"]


def test_first_feedback_allows_zero_turn_on_version_draft(tmp_path):
    service, store, _ = _service(tmp_path)
    service.runtime = Runtime()
    draft = create(service)
    with store._connect() as connection:
        connection.execute("DELETE FROM agent_conversation_turns WHERE draft_id = ?", (draft["draft_id"],))
    draft = service.get_draft(draft["draft_id"])
    _, result = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    assert result["conversation"][0]["turn"] == 1


@pytest.mark.parametrize("failure", ["timeout", "cancel", "invalid"])
def test_failed_feedback_keeps_original_draft_and_sanitizes_errors(tmp_path, failure):
    service, store, _ = _service(tmp_path)
    service.runtime = Runtime("bogus" if failure == "invalid" else "reply", delay=1 if failure != "invalid" else 0)
    service.feedback_timeout_seconds = 0.001 if failure == "timeout" else 180
    draft = create(service)

    async def scenario():
        task = await service.submit_feedback(draft["draft_id"], feedback_payload(draft))
        if failure == "cancel":
            await asyncio.sleep(0)
            await service.cancel_feedback(draft["draft_id"], task["turn"])
        elif task["task_id"] in service._feedback_tasks:
            await service._feedback_tasks[task["task_id"]]
    asyncio.run(scenario())
    result = service.get_draft(draft["draft_id"])
    assert result["revision"] == 1 and result["package"] == draft["package"]
    assert result["conversation"][-1]["status"] in {"failed", "cancelled"}
    assert store.get_agent_operation(draft["draft_id"]) is None


def test_concurrent_authoring_blocks_update_undo_validate_and_publish(tmp_path):
    service, _, _ = _service(tmp_path)
    service.runtime = Runtime(delay=1)
    draft = create(service)

    async def scenario():
        task = await service.submit_feedback(draft["draft_id"], feedback_payload(draft))
        for callback in (
            lambda: service.update(draft["draft_id"], AgentDraftUpdate(expectedRevision=1, manifest=draft["package"]["manifest"])),
            lambda: service.undo(draft["draft_id"], expected_revision=1, target_revision=1),
            lambda: service.validate(draft["draft_id"]),
            lambda: service.publish(draft["draft_id"], SimpleNamespace(expected_revision=1)),
        ):
            with pytest.raises(AgentLifecycleError) as error:
                callback()
            assert error.value.code == "agent_draft_operation_active"
        await asyncio.sleep(0)
        await service.cancel_feedback(draft["draft_id"], task["turn"])
    asyncio.run(scenario())


def test_trial_success_cannot_fabricate_formal_acceptance_and_secrets_not_saved(tmp_path, monkeypatch):
    service, store, _ = _service(tmp_path)
    draft = create(service)
    calls = []

    async def submit(manifest, inputs, **kwargs):
        calls.append((manifest, inputs, kwargs))
        return "run_trial"

    def descriptor(value, *, domain):
        return {"algorithm": "HMAC-SHA-256", "key_id": "test", "domain": domain, "digest": hashlib.sha256(("test-only-key:" + value).encode()).hexdigest()}
    service.coordinator = SimpleNamespace(submit_agent_snapshot=submit, secret_protector=SimpleNamespace(hmac_descriptor=descriptor))
    trial = asyncio.run(service.live_validate(draft["draft_id"], expected_revision=1, input_value={}, sensitive_inputs={"receipt_reference": "SECRET"}, request_id="trial-1"))
    assert calls[0][2]["sensitive_inputs"]["receipt_reference"] == "SECRET"
    assert "SECRET" not in json.dumps(store.get_agent_authoring_draft(draft["draft_id"]))
    run = SimpleNamespace(status=RunStatus.completed, completed_at=utc, error=None, result=SimpleNamespace(tool_calls=[], workflow_output={"business_status": "inconclusive", "source_complete": True, "evidence_complete": True}, errors=[], completeness=SimpleNamespace(source_complete=True, business_complete=True)))
    monkeypatch.setattr(store, "get_run", lambda _: run)
    report = service.validation_report(draft["draft_id"])
    assert report["trial"]["verdict"] == "PASS"
    assert "fixedAgentComparison" not in report["trial"]
    assert report["acceptance"]["verdict"] == "NOT_TESTED"
    assert report["publishability"]["can_publish"] is False
    assert store.get_agent_operation(draft["draft_id"]) is None
    with pytest.raises(AgentLifecycleError) as error:
        service.publish(draft["draft_id"], AgentPublishRequest(expectedRevision=1, targetVersion="0.1.0"))
    assert error.value.code == "agent_validation_pass_required"
    duplicate = asyncio.run(service.live_validate(draft["draft_id"], expected_revision=1, input_value={}, sensitive_inputs={"receipt_reference": "SECRET"}, request_id="trial-1"))
    assert duplicate["run_id"] == trial["run_id"] and len(calls) == 1
    with pytest.raises(AgentLifecycleError) as conflict:
        asyncio.run(service.live_validate(draft["draft_id"], expected_revision=1, input_value={}, sensitive_inputs={"receipt_reference": "DIFFERENT"}, request_id="trial-1"))
    assert conflict.value.code == "agent_request_conflict"


utc = "2026-09-09T00:00:00Z"


def test_auto_discover_defaults_are_not_pretended_live_samples(tmp_path):
    service, _, _ = _service(tmp_path)
    draft = create(service)
    with pytest.raises(AgentLifecycleError) as error:
        asyncio.run(service.live_validate(draft["draft_id"], input_value={}, auto_discover=True))
    assert error.value.code == "agent_sample_confirmation_required"


def test_sensitive_trial_without_protector_fails_before_run_creation(tmp_path):
    service, store, _ = _service(tmp_path)
    draft = create(service)
    with pytest.raises(AgentLifecycleError) as error:
        asyncio.run(service.live_validate(draft["draft_id"], input_value={}, sensitive_inputs={"receipt_reference": "secret"}))
    assert error.value.code == "agent_secure_input_unsupported"
    assert store.get_agent_operation(draft["draft_id"]) is None


@pytest.mark.parametrize("executable", [None, False, True])
def test_external_formal_report_requires_explicit_execution_approval(tmp_path, executable):
    from sap_business_agents_platform.acceptance import agent_execution_digest
    service, store, _ = _service(tmp_path)
    draft = create(service)
    report = {"verdict": "PASS", "execution_digest": agent_execution_digest(draft["package"]["manifest"], None), "fixedAgentComparison": "MATCH", "freeQueryComparison": "MATCH", "acceptanceMode": "three_stage", "executable": executable, "blockingLimitations": []}
    row = store.get_agent_authoring_draft(draft["draft_id"])
    row["validation"] = report
    store.save_agent_authoring_draft(row)
    result = service.validation_report(draft["draft_id"])
    assert result["publishability"]["can_publish"] is (executable is True)


def test_package_unsafe_paths_rejected_before_write(tmp_path):
    service, _, _ = _service(tmp_path)
    draft = create(service)
    for name in ("../outside.txt", "agent.json", "rules.py", "versions/0.1.0/x", "C:/outside"):
        bad = copy.deepcopy(draft["package"])
        bad["files"][name] = "unsafe"
        with pytest.raises(AgentLifecycleError):
            service._validate_package_paths(bad)


def test_cancel_before_worker_starts_releases_operation(tmp_path):
    service, store, _ = _service(tmp_path)
    service.runtime = Runtime(delay=5)
    draft = create(service)

    async def scenario():
        task = await service.submit_feedback(draft["draft_id"], feedback_payload(draft))
        await service.cancel_feedback(draft["draft_id"], task["turn"])
    asyncio.run(scenario())
    assert store.get_agent_operation(draft["draft_id"]) is None
    assert service.get_draft(draft["draft_id"])["conversation"][-1]["status"] == "cancelled"
    assert service.runtime.calls == []


def test_cancel_is_persisted_before_stubborn_runtime_returns_revision(tmp_path):
    service, store, _ = _service(tmp_path)
    entered = asyncio.Event()
    observed_statuses = []

    class StubbornRuntime(Runtime):
        async def review_agent_feedback(self, **kwargs):
            entered.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                observed_statuses.append(store.get_agent_operation(draft["draft_id"])["status"])
            return {"action": "revise_agent", "summary": {"zh": "修改标题", "en": "Rename"}, "edits": [{"op": "replace", "path": "/manifest/title/zh", "value": "不应保存"}]}

    service.runtime = StubbornRuntime()
    draft = create(service)

    async def scenario():
        submitted = await service.submit_feedback(draft["draft_id"], feedback_payload(draft))
        await entered.wait()
        await service.cancel_feedback(draft["draft_id"], submitted["turn"])
        return submitted

    submitted = asyncio.run(scenario())
    result = service.get_draft(draft["draft_id"])
    assert observed_statuses == ["cancelling"]
    assert result["revision"] == 1 and result["package"] == draft["package"]
    assert result["conversation"][-1]["status"] == "cancelled"
    assert result["conversation"][-1]["result_revision"] is None
    assert store.get_agent_operation(draft["draft_id"]) is None
    assert store.get_agent_operation_by_id(draft["draft_id"], submitted["task_id"])["status"] == "cancelled"


def test_cancel_completed_feedback_is_read_only(tmp_path):
    service, store, _ = _service(tmp_path)
    service.runtime = Runtime("reply")
    draft = create(service)
    submitted, result = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    operation = store.get_agent_operation_by_id(draft["draft_id"], submitted["task_id"])
    asyncio.run(service.cancel_feedback(draft["draft_id"], submitted["turn"]))
    assert service.get_draft(draft["draft_id"])["conversation"] == result["conversation"]
    assert store.get_agent_operation_by_id(draft["draft_id"], submitted["task_id"]) == operation


def test_stop_finalizes_feedback_cancelled_before_worker_starts(tmp_path):
    service, store, _ = _service(tmp_path)
    service.runtime = Runtime(delay=5)
    draft = create(service)

    async def scenario():
        await service.submit_feedback(draft["draft_id"], feedback_payload(draft))
        await service.stop()

    asyncio.run(scenario())
    assert store.get_agent_operation(draft["draft_id"]) is None
    assert service.get_draft(draft["draft_id"])["conversation"][-1]["status"] == "cancelled"
    assert service.runtime.calls == []


def test_private_source_material_is_not_sent_or_destroyed(tmp_path):
    service, store, _ = _service(tmp_path)
    service.runtime = Runtime("revise_agent", mutate=lambda package: package["manifest"]["title"].update(zh="新标题"))
    draft = create(service)
    op = service._reserve_operation(draft["draft_id"], 1, "edit")
    package = copy.deepcopy(draft["package"])
    package["files"]["docs/private.json"] = "PRIVATE_SOURCE"
    package["binary_files"] = {"attachment.bin": "YQ=="}
    service._apply_package(draft["draft_id"], 1, package, kind="manual_edit", operation_id=op["operation_id"])
    service._finish_operation(draft["draft_id"], op["operation_id"])
    row = store.get_agent_authoring_draft(draft["draft_id"])
    row["metadata"]["private_source_files"] = ["docs/private.json", "attachment.bin"]
    store.save_agent_authoring_draft(row)
    draft = service.get_draft(draft["draft_id"])
    _, result = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    assert "PRIVATE_SOURCE" not in json.dumps(service.runtime.calls)
    assert "binary_files" not in service.runtime.calls[0]["package"]
    assert result["package"]["files"]["docs/private.json"] == "PRIVATE_SOURCE"
    assert result["package"]["binary_files"] == {"attachment.bin": "YQ=="}


@pytest.mark.parametrize("mutation", [
    lambda package: package["manifest"].update(module="FI"),
    lambda package: package["manifest"].update(slug="different-id"),
    lambda package: package.update(rules="import os\ndef evaluate(inputs): return os.environ"),
    lambda package: package["files"].update({"../secret": "bad"}),
])
def test_invalid_runtime_package_never_changes_draft(tmp_path, mutation):
    service, _, _ = _service(tmp_path)
    service.runtime = Runtime("revise_agent", mutate=mutation)
    draft = create(service)
    _, result = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    assert result["revision"] == 1
    assert result["package"] == draft["package"]
    assert result["conversation"][-1]["status"] == "failed"


def test_planner_rejects_oversized_context_before_sdk_and_preserves_explicit_model(tmp_path):
    from sap_business_agents_platform.codex_planner import CodexPlanner
    planner = CodexPlanner(tmp_path, "gpt-5.6-sol")
    with pytest.raises(ValueError, match="agent_authoring_context_too_large"):
        asyncio.run(planner.review_agent_feedback(feedback="modify", locale="en", package={"readme": "x" * 300_001}))
    planner.model = None
    with pytest.raises(ValueError, match="agent_runtime_binding_missing"):
        asyncio.run(planner.review_agent_feedback(feedback="modify", locale="en", package={}))


@pytest.mark.parametrize("action", ["clarify", "reply", "revise_agent"])
def test_planner_response_discriminator_and_empty_isolated_workspace(tmp_path, action):
    from sap_business_agents_platform.codex_planner import CodexPlanner
    planner = CodexPlanner(tmp_path, "gpt-5.6-sol")
    calls = []
    raw = {"action": action, "summary": {"zh": "说明", "en": "Explanation"}, "manifest_json": '{"slug":"x"}', "readme": "# Agent", "rules_source": "", "files_json": "{}"}

    async def run(prompt, **kwargs):
        assert "action" in kwargs["output_schema"]["required"]
        return SimpleNamespace(final_response=json.dumps(raw))

    async def start(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id="thread-test", run=run)

    decision = asyncio.run(planner._run_agent_feedback(SimpleNamespace(thread_start=start), "prompt", {}, None, str(tmp_path / "empty")))
    assert decision["action"] == action
    assert ("package" in decision) == (action == "revise_agent")
    assert calls[0]["model"] == "gpt-5.6-sol" and calls[0]["cwd"] == str(tmp_path / "empty")


def test_targeted_edits_preserve_complete_package_and_pointer_escaping():
    package = {"manifest": {"title": {"zh": "旧名称"}, "tags": ["one", "two"], "validation": {"verdict": "PASS"}}, "readme": "Complete long README", "rules": None, "files": {"docs/a~b.md": "old"}}
    edited = apply_package_edits(package, [
        {"op": "replace", "path": "/manifest/title/zh", "value": "新名称"},
        {"op": "remove", "path": "/manifest/tags/0"},
        {"op": "add", "path": "/manifest/tags/-", "value": "three"},
        {"op": "replace", "path": "/files/docs~1a~0b.md", "value": "new"},
    ])
    assert edited["manifest"]["title"]["zh"] == "新名称"
    assert edited["manifest"]["tags"] == ["two", "three"]
    assert edited["files"]["docs/a~b.md"] == "new"
    assert edited["readme"] == package["readme"]
    assert edited["manifest"]["validation"] == package["manifest"]["validation"]
    assert package["manifest"]["title"]["zh"] == "旧名称"


@pytest.mark.parametrize("edits", [
    [],
    [{"op": "replace", "path": "/manifest/missing", "value": 1}],
    [{"op": "replace", "path": "/manifest/title/xx~2yy", "value": 1}],
    [{"op": "replace", "path": "/manifest/validation/verdict", "value": "PASS"}],
    [{"op": "replace", "path": "/manifest", "value": {}}],
    [{"op": "replace", "path": "/binary_files/file", "value": "abc"}],
    [{"op": "remove", "path": "/manifest/tags/3"}],
    [{"op": "remove", "path": "/manifest/tags/-"}],
    [{"op": "add", "path": "/manifest/tags/4", "value": "four"}],
    [{"op": "replace", "path": "/manifest/tags/01", "value": "one"}],
    [{"op": "add", "path": "/manifest/title", "value": {}}],
    [{"op": "remove", "path": "/manifest/title"}, {"op": "add", "path": "/manifest/title", "value": {}}],
    [{"op": "replace", "path": "/manifest/title", "value": {}}, {"op": "add", "path": "/manifest/title/zh", "value": "x"}],
    [{"op": "copy", "path": "/readme", "value": "x"}],
    [{"op": "remove", "path": "/readme", "value": None}],
    [{"op": "replace", "path": "/readme", "value": "x" * 100_001}],
    [{"op": "replace", "path": "/readme", "value": "x"}] * 101,
])
def test_invalid_targeted_edits_fail_without_mutating_source(edits):
    package = {"manifest": {"title": {"zh": "old"}, "tags": ["a", "b"]}, "readme": "Original"}
    original = copy.deepcopy(package)
    with pytest.raises(AgentLifecycleError) as error:
        apply_package_edits(package, edits)
    assert error.value.code == "runtime_agent_feedback_invalid"
    assert package == original


def test_targeted_runtime_rename_produces_exactly_one_revision_and_real_diff(tmp_path):
    service, _, _ = _service(tmp_path)

    class TargetedRuntime(Runtime):
        async def review_agent_feedback(self, **kwargs):
            return {"action": "revise_agent", "summary": {"zh": "已修改名称", "en": "Renamed"}, "edits": [{"op": "replace", "path": "/manifest/title/zh", "value": "业务化新名称"}]}

    service.runtime = TargetedRuntime()
    draft = create(service)
    _, revised = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    assert revised["revision"] == 2
    assert revised["package"]["manifest"]["title"]["zh"] == "业务化新名称"
    assert revised["package"]["readme"] == draft["package"]["readme"]
    assert len(revised["conversation"]) == len(draft["conversation"]) + 1
    assert [item["path"] for item in service.get_diff(draft["draft_id"], 1, 2)["changes"]] == ["/manifest/title/zh"]


def test_legacy_draft_lazily_binds_first_available_model_and_keeps_it(tmp_path):
    service, store, _ = _service(tmp_path)
    draft = create(service)  # no Runtime available at creation
    assert draft["metadata"]["runtime_snapshot"] is None

    class ConfigurableRuntime(Runtime):
        selected = "gpt-5.6-sol"

        def snapshot(self):
            return {"provider_id": "codex", "model": self.selected}

    service.runtime = ConfigurableRuntime()
    _, first = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    assert first["conversation"][-1]["status"] == "completed"
    assert first["metadata"]["runtime_snapshot"]["model"] == "gpt-5.6-sol"
    service.runtime.selected = "gpt-5.6-terra"
    _, second = asyncio.run(send_and_wait(service, first, feedback_payload(first, "继续解释", "next")))
    assert second["metadata"]["runtime_snapshot"]["model"] == "gpt-5.6-sol"
    assert service.runtime.bindings == [("codex", "gpt-5.6-sol"), ("codex", "gpt-5.6-sol")]
    assert store.get_agent_authoring_draft(draft["draft_id"])["metadata"]["runtime_snapshot"]["model"] == "gpt-5.6-sol"


@pytest.mark.parametrize("snapshot", [None, {"provider_id": "codex", "model": None}])
def test_unbound_draft_does_not_call_disabled_or_implicit_runtime(tmp_path, snapshot):
    service, _, _ = _service(tmp_path)
    draft = create(service)

    class DisabledRuntime(Runtime):
        def snapshot(self):
            if snapshot is None:
                raise ValueError("runtime_disabled")
            return snapshot

    service.runtime = DisabledRuntime()
    _, result = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    assert result["conversation"][-1]["status"] == "failed"
    assert result["conversation"][-1]["decision"]["error_code"] == "agent_runtime_binding_missing"
    assert service.runtime.calls == []
    assert result["metadata"]["runtime_snapshot"] is None


def test_planner_accepts_concise_edits_without_expanding_response(tmp_path):
    from sap_business_agents_platform.codex_planner import CodexPlanner
    planner = CodexPlanner(tmp_path, "gpt-5.6-sol")
    edits = [{"op": "replace", "path": "/manifest/title/zh", "value": "new"}]

    async def run(prompt, **kwargs):
        return SimpleNamespace(final_response=json.dumps({"action": "revise_agent", "summary": {"zh": "完成", "en": "Done"}, "manifest_json": "", "readme": "", "rules_source": "", "files_json": "", "edits_json": json.dumps(edits)}))

    async def start(**kwargs):
        return SimpleNamespace(id="targeted-thread", run=run)

    decision = asyncio.run(planner._run_agent_feedback(SimpleNamespace(thread_start=start), "prompt", {"manifest": {"title": {"zh": "old"}}}, None, str(tmp_path)))
    assert decision["edits"] == edits
    assert "package" not in decision
