"""Synthetic clocks and isolated SQLite only: no SDK, model, authentication or SAP calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sap_business_agents_platform import runtime_query_clock as clock_module
from sap_business_agents_platform.models import RunStatus, RunCreate, RunMode
from sap_business_agents_platform.runtime_contract import RuntimeContractError
from sap_business_agents_platform.runtime_input_control import request, resume, recover, InputControlError
from sap_business_agents_platform.runtime_query_clock import RunClock, queue_legacy_input
from tests.test_runtime_report_contract import make_broker


@pytest.fixture
def case(tmp_path, monkeypatch):
    wall = [datetime.now(timezone.utc)]
    mono = [1000.0]
    monkeypatch.setattr(clock_module, "now", lambda: wall[0])
    monkeypatch.setattr(clock_module, "time", SimpleNamespace(monotonic=lambda: mono[0]))
    broker, store, run, _ = make_broker(tmp_path)
    store.update_run(run, status=RunStatus.running, started_at=wall[0].isoformat())
    store.update_harness_state(run, {"terminal_protocol": 2, "time_budget": {
        "hard_limit_seconds": 1800, "query_seconds_granted": 900,
        "finalization_seconds_reserved": 300}})
    clock = RunClock(store, broker._run_clocks)
    clock.transition(run, "start")
    def advance(seconds):
        wall[0] += timedelta(seconds=seconds)
        mono[0] += seconds
    return SimpleNamespace(broker=broker, store=store, run=run, clock=clock, advance=advance, wall=wall)


def pause(c):
    item = request(c.store, c.run, "request_query_clarification", {"question": "Which company code?"})
    # Only a cleanup-confirmed control is resumable.
    c.clock.transition(c.run, "pause")
    state = c.store.get_harness_state(c.run)["input_control"]
    c.store.update_harness_state(c.run, {"input_control": {**state, "status": "waiting"}})
    c.store.update_run(c.run, status=RunStatus.waiting_input)
    return item["input_request_id"]


def answer(c, request_id):
    return resume(c.store, c.run, request_id=request_id, value="1710", sensitive={}, protector=None)


def test_saved_incident_replay_35_minute_wait_does_not_consume_remaining_budget(case):
    c = case
    c.advance(48)
    rid = pause(c)
    c.advance(2083)
    assert c.broker._elapsed_seconds(c.run) == 48
    assert c.broker._next_deadline_at(c.run, c.broker.budget_snapshot(c.run)) is None
    answer(c, rid)
    c.advance(30)  # Waiting in the execution queue is not active execution either.
    assert c.broker._elapsed_seconds(c.run) == 48
    assert RunClock(c.store).elapsed(c.run) == 48
    c.clock.transition(c.run, "start")
    c.advance(13)
    budget = c.broker.review_deadline(c.run)
    assert budget["elapsed_seconds"] == 61
    assert budget["hard_limit_seconds"] - budget["elapsed_seconds"] == 1739
    assert budget["deadline_phase"] == "querying"
    assert c.store.get_run(c.run).started_at != c.wall[0].isoformat()
    assert not c.store.list_harness_tool_calls(c.run)


def test_repeated_pauses_and_process_restarts_preserve_fractional_execution(case):
    c = case
    for _ in range(8):
        c.advance(.25)
        rid = pause(c)
        c.advance(3600)
        assert RunClock(c.store).elapsed(c.run) == pytest.approx(c.clock.elapsed(c.run))
        answer(c, rid)
        c.clock = RunClock(c.store)  # A fresh broker/process must not grant another budget.
        c.clock.transition(c.run, "start")
        c.store.update_run(c.run, status=RunStatus.running)
    assert c.clock.elapsed(c.run) == pytest.approx(2)
    assert c.store.get_harness_state(c.run)["execution_clock"]["waiting_seconds"] == 28800


def test_active_restart_and_backward_wall_clock_do_not_restart_budget(case):
    c = case
    c.advance(100)
    assert c.broker._elapsed_seconds(c.run) == 100
    assert RunClock(c.store).elapsed(c.run) == pytest.approx(100)
    c.wall[0] -= timedelta(hours=1)
    c.advance(10)
    assert c.broker._elapsed_seconds(c.run) == 110
    with pytest.raises(RuntimeContractError, match="runtime_execution_clock_unconfirmed"):
        RunClock(c.store).elapsed(c.run)


def test_pause_never_replenishes_exhausted_budget_or_terminal_deadline(case):
    c = case
    c.advance(1780)
    rid = pause(c)
    c.advance(7200)
    answer(c, rid)
    c.clock.transition(c.run, "start")
    c.advance(20)
    budget = c.broker.review_deadline(c.run)
    assert budget["elapsed_seconds"] == 1800
    assert budget["hard_limit_seconds"] - budget["elapsed_seconds"] == 0
    assert budget["deadline_phase"] == "deadline_exceeded"
    c.clock.transition(c.run, "start")
    assert c.broker._elapsed_seconds(c.run) == 1800


def test_terminal_handoff_uses_the_same_cumulative_clock(case):
    c = case
    c.store.update_harness_state(c.run, {"terminal_handoff_elapsed_deadline": 120})
    c.advance(60)
    rid = pause(c)
    c.advance(3600)
    answer(c, rid)
    c.clock.transition(c.run, "start")
    c.advance(61)
    budget = c.broker.review_deadline(c.run)
    assert budget["deadline_phase"] == "deadline_exceeded"
    assert c.store.get_harness_state(c.run)["terminal_handoff_elapsed_deadline"] == 120


def test_duplicate_and_cancelled_answers_cannot_replenish_or_resume(case):
    c = case
    c.advance(50)
    rid = pause(c)
    c.advance(3000)
    def attempt():
        try:
            return answer(c, rid)
        except InputControlError:
            return None
    with ThreadPoolExecutor(2) as executor:
        values = list(executor.map(lambda _: attempt(), range(2)))
    assert sum(v is not None for v in values) == 1
    assert c.broker._elapsed_seconds(c.run) == 50
    c.clock.transition(c.run, "start")
    c.store.update_run(c.run, status=RunStatus.running)
    rid = pause(c)
    c.store.update_run(c.run, cancel_requested=True)
    with pytest.raises(InputControlError):
        answer(c, rid)
    assert c.store.get_harness_state(c.run)["execution_clock"]["mode"] == "waiting"


def test_new_scoped_authorization_gets_only_remaining_time_and_old_token_stays_revoked(case):
    c = case
    old = c.broker.open_session(c.run)
    c.advance(100)
    rid = pause(c)
    c.broker.close_session(c.run)
    c.advance(4000)
    answer(c, rid)
    c.clock.transition(c.run, "start")
    fresh = c.broker.open_session(c.run)
    assert old != fresh and not c.broker.authenticate(c.run, old)
    context = c.broker._run_assistant_context(c.run)
    assert 1698 <= (context.expires_at - datetime.now(timezone.utc)).total_seconds() <= 1701
    assert context.valid()


def test_recovery_pause_is_idempotent_and_cleanup_failure_remains_blocked(case):
    c = case
    c.advance(40)
    request(c.store, c.run, "request_query_clarification", {"question": "Company?"})
    assert recover(c.store, c.run, cleanup_complete=False)
    assert c.store.get_run(c.run).status == RunStatus.inconclusive
    assert c.store.get_harness_state(c.run)["execution_clock"]["mode"] == "active"
    c.advance(10)
    recover(c.store, c.run, cleanup_complete=True)
    c.advance(3000)
    recover(c.store, c.run, cleanup_complete=True)
    assert c.broker._elapsed_seconds(c.run) == 50


def test_legacy_input_reuses_remaining_budget_and_no_pause_is_invented_for_old_runs(case):
    c = case
    c.advance(45)
    pause(c)
    c.advance(4000)
    queue_legacy_input(c.store, c.run, "Synthetic query; company 1710")
    assert c.broker._elapsed_seconds(c.run) == 45
    with pytest.raises(RuntimeContractError):
        queue_legacy_input(c.store, c.run, "Duplicate")
    c.store.create_run("old", RunCreate(mode=RunMode.free_query, query="legacy"))
    c.store.update_run("old", status=RunStatus.waiting_input)
    queue_legacy_input(c.store, "old", "Legacy clarification")
    assert "execution_clock" not in c.store.get_harness_state("old")


@pytest.mark.parametrize("provider", ["codex", "workbuddy"])
@pytest.mark.parametrize("formal", [False, True])
def test_both_provider_controllers_checkpoint_cumulative_outcome_after_cleanup(case, monkeypatch, provider, formal):
    from sap_business_agents_platform import runtime_harness
    from sap_business_agents_platform.harness import HarnessOutcome
    c = case
    if formal:
        c.store.update_harness_state(c.run, {"acceptance_spec": {"fixture": True}})
    c.advance(60)
    async def fake_run(*_):
        c.advance(7)
        return HarnessOutcome(thread_id="synthetic", turn_count=1, status="waiting_input",
                              stop_reason="waiting_input", summary={}, elapsed_seconds=7)
    monkeypatch.setattr(runtime_harness, "_run", fake_run)
    owner = SimpleNamespace(store=c.store, broker=c.broker)
    result = asyncio.run(runtime_harness._reserved_run(owner, SimpleNamespace(provider_id=provider), c.run, "fixture"))
    assert result.elapsed_seconds == (7 if formal else 67)
    if not formal:
        assert result.hard_limit_seconds == 1800
        c.advance(3600)
        assert c.broker._elapsed_seconds(c.run) == 67


@pytest.mark.parametrize("bad", [-1, float("nan"), "0"])
def test_invalid_clock_fails_closed_without_reset(case, bad):
    c = case
    clock = c.store.get_harness_state(c.run)["execution_clock"]
    c.store.update_harness_state(c.run, {"execution_clock": {**clock, "elapsed_seconds": bad}})
    with pytest.raises(RuntimeContractError, match="runtime_execution_clock_invalid"):
        c.broker._elapsed_seconds(c.run)


def test_next_conversation_iteration_gets_its_own_budget_and_completed_time_is_frozen(case):
    c = case
    c.advance(900)
    c.clock.transition(c.run, "stop")
    c.advance(3000)
    assert c.broker._elapsed_seconds(c.run) == 900
    c.store.create_run("next", RunCreate(mode=RunMode.free_query, query="Synthetic follow-up"))
    c.store.update_harness_state("next", {"time_budget": {"hard_limit_seconds": 1800}})
    c.clock.transition("next", "start")
    assert c.broker._elapsed_seconds("next") == 0
    budget = c.broker.review_deadline("next")
    assert budget["hard_limit_seconds"] - budget["elapsed_seconds"] == 1800
    assert c.broker._elapsed_seconds(c.run) == 900


@pytest.mark.parametrize("provider", ["codex", "workbuddy"])
def test_engine_pause_display_and_atomic_input_requeue_use_the_same_clock(case, provider):
    from sap_business_agents_platform.engine import RunCoordinator
    from sap_business_agents_platform.harness import HarnessOutcome
    c = case
    async def native_pause(*_, **kwargs):
        c.advance(48)
        pause(c)
        return HarnessOutcome(thread_id="synthetic", turn_count=1, status="waiting_input",
            stop_reason="waiting_input", summary={}, clarification_question="Company?",
            elapsed_seconds=int(c.clock.elapsed(c.run)), hard_limit_seconds=1800)
    scheduled = []
    async def schedule(run, mode):
        scheduled.append((run, mode))
    owner = SimpleNamespace(store=c.store, settings=c.broker.settings,
        harness=SimpleNamespace(run=native_pause), workbuddy_harness=SimpleNamespace(run=native_pause),
        _future_runtime_binding=lambda _: {},
        _require_harness_history=lambda _: None, _schedule_run=schedule, secret_protector=None,
        normalizer=SimpleNamespace(strip_text=lambda x: x.strip()))
    # Exercise the real coordinator forwarding boundary, not the store substitute.
    owner._set_progress = RunCoordinator._set_progress.__get__(owner, RunCoordinator)
    execute = RunCoordinator._execute_free_query_harness if provider == "codex" else RunCoordinator._execute_workbuddy_harness
    asyncio.run(execute(owner, c.run))
    progress = c.store.get_run(c.run).progress
    assert progress.state == "waiting_input" and progress.elapsed_seconds == 48
    assert progress.hard_limit_seconds == 1800 and progress.next_deadline_at is None
    c.advance(2083)
    rid = c.store.get_harness_state(c.run)["input_control"]["id"]
    asyncio.run(RunCoordinator.provide_input(owner, c.run, "1710", input_request_id=rid))
    assert scheduled == [(c.run, RunMode.free_query)]
    assert c.broker._elapsed_seconds(c.run) == 48
    assert c.store.get_harness_state(c.run)["execution_clock"]["mode"] == "queued"
