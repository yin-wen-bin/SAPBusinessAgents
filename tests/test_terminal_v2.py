"""Offline contract/control regressions. No SDK, network or SAP execution."""
import asyncio
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.models import RunCreate, RunMode, RunStatus, utc_now
from sap_business_agents_platform.runtime_harness_contract import terminal_schema, turn_prompt
from sap_business_agents_platform.runtime_input_control import request, resume, pending, outcome, InputControlError
from sap_business_agents_platform.runtime_terminal_contract import TerminalChecks


def envelope():
    return dict(status="inconclusive", intent="offline fixture", summary={"zh": "fixture", "en": "fixture"},
                source_complete=False, business_complete=False, missing_evidence=["fixture"],
                evidence_refs=[], executed_plans=[], presentation=None)


@pytest.fixture
def store(tmp_path):
    s = RunStore(tmp_path / "platform.sqlite3")
    s.create_run("r", RunCreate(mode=RunMode.free_query, query="fixture"))
    s.update_run("r", status=RunStatus.running, started_at=utc_now())
    s.save_harness_state("r", {"terminal_protocol": 2})
    return s


def pause(store, secure=False):
    name = "request_secure_receipt_reference" if secure else "request_query_clarification"
    result = request(store, "r", name, {"question": "Which business identifier?"})
    item = pending(store, "r")
    store.update_harness_state("r", {"input_control": {**item, "status": "waiting"}})
    store.update_run("r", status=RunStatus.waiting_input)
    return result["input_request_id"]


def test_new_terminal_does_not_generate_input_controls_and_legacy_is_frozen():
    old = terminal_schema()
    before = copy.deepcopy(old)
    new = terminal_schema(version=2)
    assert not {"input_kind", "input_field", "clarification_question"} & new["properties"].keys()
    assert new["properties"]["status"]["enum"] == ["completed", "inconclusive"]
    assert Draft202012Validator(new).is_valid(envelope())
    assert not Draft202012Validator(new).is_valid({**envelope(), "input_kind": "INVALID"})
    assert not Draft202012Validator(new).is_valid({"status": "completed"})
    assert not Draft202012Validator(new).is_valid({"input_field": None})
    assert terminal_schema() == before
    assert "request_query_clarification" in turn_prompt("fixture", continuing=False, version=2)


def test_pause_is_durable_idempotent_and_conflicts_do_not_replace_it(store):
    a = request(store, "r", "request_query_clarification", {"question": "Which company?"})
    b = request(store, "r", "request_query_clarification", {"question": "Which company?"})
    assert a == b
    restored = RunStore(store.path)
    assert pending(restored, "r")["id"] == a["input_request_id"]
    with pytest.raises(InputControlError, match="input_request_conflict"):
        request(store, "r", "request_query_clarification", {"question": "Different?"})


@pytest.mark.parametrize("arguments", [{}, {"question": ""}, {"question": "q", "receipt_reference": "private"}])
def test_controls_never_accept_value_arguments(store, arguments):
    with pytest.raises(InputControlError):
        request(store, "r", "request_secure_receipt_reference", arguments)
    assert pending(store, "r") is None


def test_secure_pause_is_not_available_to_formal_cases(store):
    store.update_harness_state("r", {"acceptance_spec": {"case": "fixture"}})
    with pytest.raises(InputControlError, match="acceptance_secure_input_unavailable"):
        request(store, "r", "request_secure_receipt_reference", {"question": "Receipt?"})


def test_stale_and_duplicate_answers_are_rejected_and_only_one_queues(store):
    rid = pause(store)
    with pytest.raises(InputControlError, match="input_request_stale"):
        resume(store, "r", request_id="stale", value="1710", sensitive={}, protector=None)
    def answer():
        try:
            return resume(store, "r", request_id=rid, value="1710", sensitive={}, protector=None)
        except InputControlError:
            return None
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(lambda _: answer(), range(2)))
    assert sum(r is not None for r in results) == 1
    assert store.get_run("r").status == RunStatus.queued
    assert store.get_harness_state("r")["input_control"]["status"] == "consumed"


def test_legacy_client_can_answer_current_control_without_request_id(store):
    pause(store)
    assert "1710" in resume(store, "r", request_id=None, value="1710", sensitive={}, protector=None)


def test_secure_value_is_protected_and_never_added_to_query_or_public_input(store):
    rid = pause(store, secure=True)
    protector = SimpleNamespace(create_secret_ref=lambda **kw: ("secret_fixture", b"encrypted-fixture", {"digest": "opaque"}))
    query = resume(store, "r", request_id=rid, value="", sensitive={"receipt_reference": "PRIVATE-REFERENCE"}, protector=protector)
    assert "PRIVATE-REFERENCE" not in query
    assert "PRIVATE-REFERENCE" not in json.dumps(store.get_run("r").input)
    assert store.get_run("r").input["receipt_reference"]["provided"] is True


def test_cancelled_or_wrong_kind_requests_cannot_resume(store):
    rid = pause(store, secure=True)
    with pytest.raises(InputControlError, match="input_request_kind_mismatch"):
        resume(store, "r", request_id=rid, value="ordinary", sensitive={}, protector=None)
    store.update_run("r", cancel_requested=True)
    with pytest.raises(InputControlError):
        resume(store, "r", request_id=rid, value="ordinary", sensitive={}, protector=None)


@pytest.mark.parametrize("provider", ["codex", "workbuddy"])
def test_control_outcome_requires_confirmed_runtime_cleanup(store, provider):
    request(store, "r", "request_secure_receipt_reference", {"question": "Receipt?"})
    controller = SimpleNamespace(store=store, broker=SimpleNamespace(snapshot=lambda _: ([], [])))
    context = {"thread_id": provider + ":fixture", "cleanup_state": {"complete": False}}
    with pytest.raises(InputControlError, match="runtime_cleanup_incomplete"):
        outcome(controller, "r", context, 1, 0)
    context["cleanup_state"]["complete"] = True
    result = outcome(controller, "r", context, 1, 0)
    assert result.status == "waiting_input" and result.business_complete is False
    assert result.input_kind == "secure_business_reference" and result.input_field == "receipt_reference"


def test_terminal_duplicates_are_observations_but_two_real_identical_failures_stop():
    checks = TerminalChecks(terminal_schema(version=2))
    bad = {**envelope(), "input_kind": "INVALID"}
    call = lambda n: hashlib.sha256(str(n).encode()).hexdigest()
    first = checks.check(bad, submission_id=call(1), origin="formatter_hook", phase="native_candidate")
    duplicate = checks.check(bad, submission_id=call(1), origin="formatter_hook", phase="native_candidate")
    assert first == duplicate and first["convergence"]["without_progress"] == 1
    assert checks.check(bad, submission_id=call(2), origin="formatter_hook", phase="native_candidate")["stop"]


def test_terminal_deadlines_do_not_reset_on_report_revalidation_or_repair():
    checks = TerminalChecks(terminal_schema(version=2))
    checks.report_ready(1800, now=1000)
    checks.report_ready(1800, now=1200)
    assert checks.handoff_deadline == 1300
    checks.rejected(1800, now=1100)
    assert checks.remaining(1800, now=1210) == 10
    checks.rejected(1800, now=1250)
    assert checks.remaining(1800, now=1290) == 10
    assert checks.remaining(1800, now=1301) == 0


def test_real_broker_authenticates_controls_and_fences_subsequent_business_reads(tmp_path):
    from tests.test_runtime_report_contract import make_broker
    broker, store, run_id, token = make_broker(tmp_path)
    store.update_harness_state(run_id, {"terminal_protocol": 2})
    async def run():
        denied = await broker.handle(run_id, "not-a-capability", "request_query_clarification", {"question": "Company?"})
        assert denied["ok"] is False and pending(store, run_id) is None
        control = await broker.handle(run_id, token, "request_query_clarification", {"question": "Company?"})
        assert control["ok"] and pending(store, run_id)
        read = await broker.handle(run_id, token, "sap_catalog_search", {"query": "must not execute"})
        assert read == {"ok": False, "code": "input_request_pending"}
    asyncio.run(run())


def test_real_workbuddy_driver_returns_control_pause_after_owned_cleanup(tmp_path):
    import time
    from sap_business_agents_platform.workbuddy_driver import WorkBuddyHarnessDriver
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
    from sap_business_agents_platform.runtime_contract import RuntimeRequest
    from sap_business_agents_platform.runtime_input_control import InputPause
    from sap_business_agents_platform.models import RuntimeSnapshot
    from tests.test_runtime_report_contract import make_broker
    binding = RuntimeSnapshot(provider_id="workbuddy", sdk_id="codebuddy-agent-sdk", model="offline-model",
        configuration_digest="fixture", environment_digest="fixture").model_dump(mode="json")
    broker, store, run_id, token = make_broker(tmp_path, runtime=binding)
    state = store.update_harness_state(run_id, {"terminal_protocol": 2})
    async def native(**kwargs):
        result = await kwargs["tool_handler"]("request_query_clarification", {"question": "Company?"})
        assert result["_runtime_report_stop_code"] == "runtime_input_requested"
        raise WorkBuddyError("runtime_input_requested")
    owner = SimpleNamespace(store=store, broker=broker,
        manager=SimpleNamespace(supervisor=SimpleNamespace(run=native, cleanup_deadlines={}, reconcile=lambda: [])))
    driver = WorkBuddyHarnessDriver(owner)
    context = {"run_id": run_id, "state": state, "capability": token, "read_scope": None, "turn_count": 1,
               "thread_id": None, "cleanup_state": {"complete": False}, "report_contract": broker.report_contract(run_id)}
    req = RuntimeRequest("workbuddy", "free_query", "harness", binding, "fixture", terminal_schema(version=2),
        cwd=str(tmp_path), permissions={"acceptance_direct_baseline": False}, deadline=time.monotonic() + 60)
    with pytest.raises(InputPause):
        asyncio.run(driver.harness_turn(req, context))
    assert context["cleanup_state"]["complete"] is True
    assert pending(store, run_id)


@pytest.mark.parametrize("clean", [False, True])
def test_restart_recovers_pause_without_new_model_or_sap_turn(store, clean):
    from sap_business_agents_platform.runtime_input_control import recover
    request(store, "r", "request_query_clarification", {"question": "Company?"})
    assert recover(store, "r", cleanup_complete=clean)
    assert store.get_run("r").status == (RunStatus.waiting_input if clean else RunStatus.inconclusive)
    assert store.get_harness_state("r").get("cleanup_incomplete", False) is (not clean)


def test_formal_missing_query_input_stops_without_interactive_wait(store):
    from sap_business_agents_platform.runtime_input_control import tools_for_task
    from sap_business_agents_platform.mcp_server import _SAP_TOOLS
    assert "request_secure_receipt_reference" not in {t["name"] for t in tools_for_task(_SAP_TOOLS, formal=True)}
    store.update_harness_state("r", {"acceptance_spec": {"fixture": True}})
    request(store, "r", "request_query_clarification", {"question": "Missing case company"})
    result = outcome(SimpleNamespace(store=store, broker=SimpleNamespace(snapshot=lambda _: ([], []))),
        "r", {"cleanup_state": {"complete": True}}, 1, 0)
    assert result.status == "inconclusive" and result.missing_evidence == ["acceptance_input_missing"]
    assert pending(store, "r") is None


def test_exact_deployed_legacy_contract_remains_frozen_and_v2_cannot_use_it(tmp_path):
    from tests.test_runtime_report_contract import make_broker
    from sap_business_agents_platform.runtime_report_contract import save_contract, digest, ReportContractError
    from sap_business_agents_platform.runtime_legacy_protocol import CHECKER_BINDING
    broker, s, run_id, _ = make_broker(tmp_path)
    frozen = copy.deepcopy(s.get_harness_state(run_id)["report_contract"])
    frozen["checker_binding"] = copy.deepcopy(CHECKER_BINDING)
    frozen["digest"] = digest({k: v for k, v in frozen.items() if k != "digest"})
    ref = save_contract(broker.settings.data_root, frozen)
    s.update_harness_state(run_id, {"terminal_protocol": 1, "report_contract": frozen, "report_contract_file": ref})
    broker.open_session(run_id)
    assert broker.report_contract(run_id) == frozen
    assert s.get_harness_state(run_id)["report_contract"] == frozen
    s.update_harness_state(run_id, {"terminal_protocol": 2})
    with pytest.raises(ReportContractError):
        broker.report_contract(run_id)


def test_report_ready_closes_external_reads_and_never_resets_reserve(tmp_path):
    from tests.test_runtime_report_contract import make_broker, report
    broker, s, run_id, token = make_broker(tmp_path)
    s.update_harness_state(run_id, {"terminal_protocol": 2})
    elapsed = [100]
    broker._elapsed_seconds = lambda _: elapsed[0]
    async def scenario():
        assert (await broker.handle(run_id, token, "sap_final_report_validate", {"report": report()}))["ok"]
        first = s.get_harness_state(run_id)["terminal_handoff_elapsed_deadline"]
        elapsed[0] += 10
        assert (await broker.handle(run_id, token, "sap_final_report_validate", {"report": report()}))["ok"]
        assert first == s.get_harness_state(run_id)["terminal_handoff_elapsed_deadline"]
        denied = await broker.handle(run_id, token, "sap_catalog_search", {"query": "no new read"})
        assert denied["code"] == "harness_finalization_only"
        elapsed[0] = first
        assert broker.review_deadline(run_id)["deadline_phase"] == "deadline_exceeded"
    asyncio.run(scenario())
