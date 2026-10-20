"""Before-source captures are immutable; never invoke a live SDK or SAP."""
import asyncio
import copy
import json
from pathlib import Path

import pytest

from tests.runtime_native_trace_fixture import OPERATIONS, trace_operation
from tests.runtime_native_trace_fixture import normalized

BEFORE = json.loads((Path(__file__).parent / "fixtures/codex-native-orchestration-before.json").read_text(encoding="utf-8"))
LEGACY_PRESENTATION = json.loads((Path(__file__).parent / "fixtures/report-presentation-before.json").read_text(encoding="utf-8"))


def report_schema_delta(operation, current):
    """Verify ALL non-report members against the immutable historical capture."""
    legacy = copy.deepcopy(current)
    legacy["properties"]["presentation"]["anyOf"][0] = LEGACY_PRESENTATION
    old_hash = next(event[1]["output_schema"] for event in BEFORE[operation]["trace"] if event[0] == "turn")
    assert normalized({"output_schema": legacy})["output_schema"] == old_hash
    from sap_business_agents_platform.runtime_report_contract import presentation_schema
    assert current["properties"]["presentation"]["anyOf"][0] == presentation_schema(strict=True)
    return normalized({"output_schema": current})["output_schema"]


@pytest.mark.parametrize("operation", OPERATIONS)
def test_native_codex_behavior_matches_before_source(operation, monkeypatch, tmp_path):
    value = asyncio.run(trace_operation(operation, monkeypatch, tmp_path))
    expected = BEFORE[operation]
    if operation in {"free_query", "acceptance_baseline", "acceptance_free_query"}:
        from sap_business_agents_platform.runtime_harness_contract import _HARNESS_OUTPUT_SCHEMA, turn_prompt, _developer_instructions
        from sap_business_agents_platform.runtime_report_contract import instructions, HANDOFF_DEFINITION
        from sap_business_agents_platform.acceptance_projection import output_schema
        from sap_business_agents_platform.config import Settings
        spec = None if operation == "free_query" else {
            "record_fields": ["object_id"], "metric_fields": ["object_count"], "required_assessments": []}
        current_schema = output_schema(_HARNESS_OUTPUT_SCHEMA, spec)
        expected = copy.deepcopy(expected)
        settings = Settings(repository_root=tmp_path, data_root=tmp_path / "data")
        current_instructions = _developer_instructions(full_access=operation != "acceptance_baseline", budget={
            "hard_limit_seconds": settings.free_query_run_seconds,
            "finalization_seconds_reserved": settings.free_query_finalization_budget_seconds})
        starting = next(item for item in expected["trace"] if item[0] == "thread_start")
        clarification = "\n" + HANDOFF_DEFINITION
        assert current_instructions.count(clarification) == 1
        assert normalized(current_instructions.replace(clarification, "", 1)) == starting[1]["developer_instructions"]
        starting[1]["developer_instructions"] = normalized(current_instructions)
        event = next(item for item in expected["trace"] if item[0] == "turn")
        event[1]["output_schema"] = report_schema_delta(operation, current_schema)
        prompt = turn_prompt("Inspect supplier", continuing=False, acceptance_spec=spec)
        addition = "\n" + instructions(acceptance=spec is not None)
        assert prompt.count(addition) == 1
        assert normalized(prompt.replace(addition, "", 1)) == event[1]["prompt"]
        event[1]["prompt"] = normalized(prompt)
    if operation == "workflow_authoring.v2":
        # The only planned change to this native seam is the shared candidate
        # boundary instruction. Keep the immutable old capture and verify all
        # other text/options/events/results against it, not a rewritten fixture.
        from copy import deepcopy
        from sap_business_agents_platform.runtime_workflow_authoring import instructions
        addition = (
            "Only the workflow object is the definition. Never copy references, history, intent or other "
            "conversation metadata into workflow_json. Preserve source extensions verbatim; do not add "
            "unknown root fields or alter platform-managed status, validation, acceptance or publication. "
            "When allowedChangePaths is present, change only those JSON Pointer subtrees; otherwise ask "
            "for clarification if related changes outside that scope are necessary. "
        )
        current = instructions()
        assert current.count(addition) == 1
        old = current.replace(addition, "")
        expected = deepcopy(expected)
        event = next(item for item in expected["trace"] if item[0] == "thread_start")
        assert normalized(old) == event[1]["developer_instructions"]
        event[1]["developer_instructions"] = normalized(current)
    assert value == expected


@pytest.mark.parametrize("operation", ["free_query", "acceptance_baseline", "acceptance_free_query"])
@pytest.mark.parametrize("provider", ["codex", "workbuddy"])
def test_common_harness_stages_schema_and_result_do_not_depend_on_driver(operation, provider, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from sap_business_agents_platform.runtime_harness import run
    from sap_business_agents_platform.runtime_contract import RuntimeResult, RuntimeSession
    from sap_business_agents_platform.config import Settings
    from sap_business_agents_platform.database import RunStore
    from sap_business_agents_platform.models import RunCreate, RunMode
    from sap_business_agents_platform.harness import HarnessToolBroker
    from tests.test_harness import FakeSapRead, FakeSkills
    from tests.runtime_native_trace_fixture import normalized
    settings = Settings(repository_root=tmp_path, data_root=tmp_path / "data")
    store = RunStore(settings.database_path)
    store.create_run("offline", RunCreate(mode=RunMode.free_query, query="Inspect supplier"))
    store.update_run("offline", status="planning")
    if operation != "free_query":
        store.update_harness_state("offline", {"acceptance_spec": {
            "record_fields": ["object_id"], "metric_fields": ["object_count"], "required_assessments": []},
            "acceptance_direct_baseline": operation == "acceptance_baseline"})
    broker = HarnessToolBroker(settings, store, FakeSapRead(), FakeSkills())
    class Driver:
        provider_id, engineering_copy = provider, False
        def binding(self, *args):
            return {"model": "frozen", "provider_id": provider}
        async def cleanup(self, *args):
            pass
        async def harness_turn(self, request, context):
            assert request.operation == operation and request.context == ({} if operation != "free_query" else {
                "original_query": "Inspect supplier", "previous_user_requests": [], "pending_question": None})
            # Compare the complete common business schema to the immutable old
            # native trace; only native encoding and identity may differ.
            assert normalized({"output_schema": request.output_schema})["output_schema"] == report_schema_delta(operation, request.output_schema)
            payload = {"status": "inconclusive", "intent": "read", "clarification_question": "",
                "input_kind": None, "input_field": None, "summary": {"zh": "证据不足", "en": "Insufficient evidence"},
                "source_complete": False, "business_complete": False, "missing_evidence": ["offline_probe"],
                "evidence_refs": [], "executed_plans": [], "presentation": None}
            if operation != "free_query":
                payload["acceptance_projection"] = None
            context["thread_id"] = "thread-frozen"
            return RuntimeResult(payload, RuntimeSession(provider, "thread-frozen"), cleanup_complete=True)
    outcome = asyncio.run(run(SimpleNamespace(settings=settings, store=store, broker=broker),
        Driver(), "offline", "Inspect supplier", None, "frozen", "max"))
    assert normalized(outcome) == BEFORE[operation]["result"]
    assert store.get_harness_state("offline")["turn_count"] == 1


def test_workbuddy_driver_reports_worker_identity_and_timeout_without_guessing(tmp_path):
    from types import SimpleNamespace
    from sap_business_agents_platform.runtime_contract import RuntimeRequest
    from sap_business_agents_platform.workbuddy_driver import WorkBuddyHarnessDriver, WorkBuddySampleDriver
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
    response = {"text": '{"status":"completed"}', "output_format": "native_json_schema", "actual_model": "verified-native"}
    events = []
    async def native(**_):
        return response
    store = SimpleNamespace(get_run=lambda _: SimpleNamespace(runtime=SimpleNamespace(model_dump=lambda **_: {"provider_id": "workbuddy"})),
        get_harness_state=lambda _: {}, update_harness_state=lambda *_, **__: None, update_run=lambda *_, **__: None,
        append_event=lambda *args: events.append(args))
    async def clean(*_, **__):
        return True
    owner = SimpleNamespace(store=store, broker=SimpleNamespace(cancel_tools=clean),
        manager=SimpleNamespace(supervisor=SimpleNamespace(run=native, reconcile=lambda: [], cleanup_deadlines={})))
    request = RuntimeRequest("workbuddy", "free_query", "read", {"provider_id": "workbuddy"}, "question",
        {"type": "object", "properties": {"status": {"const": "completed"}}, "required": ["status"]},
        cwd=str(tmp_path), permissions={"acceptance_direct_baseline": False}, deadline=__import__('time').monotonic() + 600)
    result = asyncio.run(WorkBuddyHarnessDriver(owner).harness_turn(request, {
        "run_id": "owned", "state": {}, "turn_count": 1, "cleanup_state": {},
        "report_contract": __import__('sap_business_agents_platform.runtime_report_contract', fromlist=['freeze']).freeze('owned', 'offline', request.binding)}))
    assert result.actual_model == "verified-native"
    async def timeout(**_):
        raise WorkBuddyError("workbuddy_deadline_exceeded")
    async def cleanup(*_, **__):
        return True
    supervisor = SimpleNamespace(run=timeout, cleanup_deadlines={}, reconcile=lambda: [])
    service = SimpleNamespace(settings=SimpleNamespace(data_root=tmp_path), store=store,
        broker=SimpleNamespace(cancel_tools=cleanup))
    context = SimpleNamespace(remaining=lambda: 600)
    import sap_business_agents_platform.workbuddy_sample as sample
    from unittest.mock import patch
    with patch.object(sample, "sample_prompt", lambda *_: "frozen"):
        with pytest.raises(TimeoutError):
            asyncio.run(WorkBuddySampleDriver(SimpleNamespace(supervisor=supervisor)).sample_turn(
                service, "owned", context, "cap", {}))
