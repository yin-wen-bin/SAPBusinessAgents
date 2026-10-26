"""SDK-free Harness admission and platform-owned conversation context."""
from __future__ import annotations
import time


def preflight(settings, state, *, thread_id=None, started=None):
    from .harness import HarnessOutcome
    turn_count = int(state.get("turn_count") or 0)
    elapsed = int(time.monotonic() - started) if started is not None else 0
    if not state.get("acceptance_spec") and (state.get("assessment_intent") or {}).get("intent") == "ambiguous":
        return HarnessOutcome(thread_id=thread_id, turn_count=turn_count, status="waiting_input",
            stop_reason="waiting_input", summary={"zh": "需要确认是否要求库存FIFO评估。",
                "en": "Clarification is required about the inventory FIFO assessment."},
            clarification_question=("您的问题同时包含要求和排除库存FIFO/账龄评估的表述。请明确本轮是否需要执行FIFO库存评估。"
                " / The request both includes and excludes an inventory FIFO or aging assessment. "
                "Please confirm whether FIFO inventory assessment is required for this run."),
            missing_evidence=["assessment_scope_ambiguous"], elapsed_seconds=elapsed)
    if turn_count >= settings.max_harness_turns:
        return HarnessOutcome(thread_id=thread_id, turn_count=turn_count, status="inconclusive",
            stop_reason="limit_reached", limit_kind="turns", elapsed_seconds=elapsed,
            summary={"zh": "已达到Harness轮次上限。", "en": "Harness turn limit reached."},
            missing_evidence=["harness_turn_limit"])
    return None

async def monitor_deadline(self, run_id: str, turn: Any) -> None:
    from .harness import _best_effort_interrupt
    previous_phase = "querying"
    while True:
        await asyncio.sleep(0.25)
        from .runtime_input_control import pending
        if pending(self.store, run_id):
            await _best_effort_interrupt(turn)
            return
        budget = self.broker.review_deadline(run_id)
        phase = str(budget["deadline_phase"])
        if phase == "finalizing" and previous_phase != "finalizing":
            await turn.steer(
                "The SAP query phase is now closed. Do not request Catalog, Schema, "
                "SAP GET, Skills, or other external tools. Use only already validated "
                "evidence and finish the structured report now. If evidence is "
                "insufficient, return an honest inconclusive conclusion."
            )
        if phase == "deadline_exceeded":
            self.store.fail_running_harness_tool_calls(
                run_id,
                code="harness_deadline_exceeded",
                message="The free-query hard deadline was reached.",
            )
            await _best_effort_interrupt(turn)
            return
        previous_phase = phase




def conversation_context(store, run_id, *, baseline):
    if baseline:
        return {}  # Never expose candidate/history answers to acceptance phases.
    from .agent_feedback_context import safe_context
    record = store.get_run(run_id)
    session = store.get_free_query_session_by_run(run_id)
    history = []
    if session:
        for item in store.list_free_query_iterations(session["session_id"])[-20:]:
            if item["run_id"] != run_id:
                history.append({"iteration": item["iteration"], "query": store.get_run(item["run_id"]).query,
                    "feedback": item.get("feedback"), "action": item.get("execution_action")})
    return safe_context({"original_query": (session or {}).get("original_query", record.query),
        "previous_user_requests": history,
        "pending_question": store.get_harness_state(run_id).get("platform_clarification_question")})


import asyncio
import json
import secrets
import copy
from typing import Any


def _terminal_handoff_outcome(self, driver, run_id, context, turn_count, run_started, error):
    """Preserve a typed terminal failure, never promote a validated candidate."""
    from .runtime_terminal_contract import VERSION
    from .harness import (HarnessOutcome, _persistent_harness_counts, _budgeted_tool_call_count,
        _evidence_sources_complete)
    detail = getattr(error, "detail", {}) or {}
    code = getattr(error, "code", "")
    if not isinstance(detail, dict) or not isinstance(code, str):
        return None
    if isinstance(code, str) and code.startswith(driver.provider_id + "_"):
        code = "runtime_" + code[len(driver.provider_id) + 1:]
    if detail.get("terminal_contract") != VERSION or code not in {
            "runtime_terminal_correction_stalled", "runtime_structured_output_missing", "runtime_structured_output_invalid"}:
        return None
    candidate = detail.get("last_rejected_candidate") or {}
    issues = candidate.get("validation_issues", []) if isinstance(candidate, dict) else []
    failure = {"code": code, "validation_issues": issues,
        "attempts_without_progress": (detail.get("terminal_convergence") or {}).get("without_progress", 0),
        "phase": detail.get("terminal_phase", "final_handoff"),
        "cleanup_complete": context.get("cleanup_state", {}).get("complete") is True,
        "cleanup_failure_code": detail.get("cleanup_failure_code"),
        "candidate_report_refs": [call.get("output", {}).get("validation_ref")
            for call in self.store.list_harness_tool_calls(run_id)
            if call.get("tool_name") == "sap_final_report_validate" and isinstance(call.get("output"), dict)
            and call["output"].get("validation_ref")]}
    self.store.update_harness_state(run_id, {"terminal_handoff_failure": failure})
    self.store.append_event(run_id, "runtime_terminal_handoff_failed", failure)
    if detail.get("cleanup_failure_code"):
        self.store.update_harness_state(run_id, {"cleanup_incomplete": True})
    calls, evidence = self.broker.snapshot(run_id)
    budget = self.broker.budget_snapshot(run_id)
    web_count, discovered, activated = _persistent_harness_counts(self.store, run_id)
    return HarnessOutcome(thread_id=context["thread_id"], turn_count=turn_count,
        status="inconclusive", stop_reason="capability_unavailable",
        summary={"zh": "最终结构化结果未通过格式校验或未完成交接；已保留证据，未将报告候选冒充成功结果。",
            "en": "The final structured result failed validation or handoff; evidence was preserved and a report candidate was not promoted to success."},
        source_complete=_evidence_sources_complete(evidence), business_complete=False,
        missing_evidence=[code], tool_calls=calls, evidence=evidence,
        budgeted_tool_call_count=_budgeted_tool_call_count(calls), web_search_count=web_count,
        discovered_tool_count=discovered, activated_tool_count=activated,
        elapsed_seconds=int(time.monotonic()-run_started), hard_limit_seconds=budget["hard_limit_seconds"],
        query_seconds_granted=budget["query_seconds_granted"], finalization_seconds_reserved=budget["finalization_seconds_reserved"],
        deadline_phase="completed")

async def run(self, driver, run_id, query, thread_id=None, model=None, reasoning_effort=None):
    auth = getattr(self, "authentication_service", None)
    if auth:
        record = self.store.get_run(run_id)
        snapshot = record.runtime.model_dump(mode="json") if record.runtime else {}
        async with auth.use(driver.provider_id, snapshot):
            result = await _reserved_run(self, driver, run_id, query, thread_id, model, reasoning_effort)
            auth.assert_binding(driver.provider_id, snapshot)
            return result
    return await _reserved_run(self, driver, run_id, query, thread_id, model, reasoning_effort)


async def _reserved_run(self, driver, run_id, query, thread_id=None, model=None, reasoning_effort=None):
    from contextlib import nullcontext
    from .runtime_contract import cleanup_binding
    supervisor = getattr(getattr(self, "manager", None), "supervisor", None)
    reserve = getattr(supervisor, "reserve", None)
    parallel = not self.store.get_harness_state(run_id).get('acceptance_spec')
    async with reserve(task_id=run_id, parallel=parallel) if driver.provider_id == "workbuddy" and callable(reserve) else nullcontext():
        if driver.provider_id == "workbuddy":
            from .models import utc_now
            record = self.store.get_run(run_id)
            anchor = self.store.get_harness_state(run_id).get("budget_start") or {}
            if anchor.get("source_started_at") != record.started_at or not anchor.get("execution_started_at"):
                self.store.update_harness_state(run_id, {"budget_start": {
                    "source_started_at": record.started_at, "execution_started_at": utc_now()}})
        with cleanup_binding():
            return await _run(self, driver, run_id, query, thread_id, model, reasoning_effort)


async def _run(
        self, driver,
        run_id: str,
        query: str,
        thread_id: str | None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> HarnessOutcome:
    from .harness import (HarnessOutcome, AcceptanceReportValidationError,
        _persistent_harness_counts, _latest_validated_presentation, _effective_missing_evidence,
        _evidence_sources_complete, _budgeted_tool_call_count, _deadline_presentation)
    run_started = time.monotonic()
    cleanup_state: dict[str, Any] = {}
    resuming_thread = bool(thread_id)
    state = self.store.get_harness_state(run_id)
    turn_count = int(state.get("turn_count") or 0) + 1
    guarded = preflight(self.settings, state, thread_id=thread_id, started=run_started)
    if guarded:
        return guarded
    from .runtime_contract import RuntimeSession, RuntimeContractError
    if isinstance(thread_id, RuntimeSession):
        thread_id = thread_id.require_provider(driver.provider_id)
    elif driver.provider_id == "codex" and str(thread_id or "").startswith("workbuddy:"):
        raise RuntimeContractError("runtime_session_provider_mismatch")
    from .runtime_query_contract import ReadScope
    if "terminal_protocol" not in state:
        state = self.store.update_harness_state(run_id, {"terminal_protocol": 1 if state.get("execution_snapshot") else 2})
    protocol = state["terminal_protocol"]
    scope = ReadScope(query, self.store.get_run(run_id).input)
    # Unsupported mappings are platform scope-contract failures, not a vague
    # model question. Engine error persistence preserves these value-free issues.
    scope.require_resolved()
    capability = self.broker.open_session(run_id)
    if callable(getattr(self.broker, "bind_read_scope", None)):
        self.broker.bind_read_scope(run_id, scope)
    session = self.store.get_free_query_session_by_run(run_id)
    workspace_key = str(session["session_id"]) if session else run_id
    workspace = self.settings.data_root / "harness" / workspace_key / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    from .authoring_workspace import AuthoringWorkspace
    direct_baseline = state.get("acceptance_direct_baseline") is True
    full_access = driver.engineering_copy and not direct_baseline
    # An independent acceptance baseline receives an empty, read-only task
    # directory.  Ordinary free queries keep their engineering work copy.
    engineering = None if not full_access else AuthoringWorkspace(
        self.settings.repository_root,
        self.settings.data_root / "harness" / run_id / ("engineering-" + secrets.token_hex(8)),
    )
    try:
        from .runtime_admission import driver_binding
        snapshot = copy.deepcopy(await driver_binding(driver, run_id, state, model, reasoning_effort))
        if engineering is not None:
            engineering.prepare(None, current_source=True)
            workspace = engineering.source
            snapshot.update({"workspace_id": engineering.root.name,
                             "base_commit": engineering.base_commit,
                             "base_digest": engineering.base_digest})
        else:
            workspace = self.settings.data_root / "acceptance" / run_id / "baseline-workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            snapshot.update({"workspace_id": run_id, "candidate_access": False})
        self.store.update_harness_state(run_id, {"execution_snapshot": snapshot})
    except BaseException:
        self.broker.close_session(run_id)
        raise
    from .runtime_contract import RuntimeRequest, RuntimeSession
    from .runtime_harness_contract import _developer_instructions, terminal_schema, turn_prompt
    operation = "acceptance_baseline" if direct_baseline else "acceptance_free_query" if state.get("acceptance_spec") else "free_query"
    budget = self.broker.budget_snapshot(run_id)
    remaining = max(0, budget["hard_limit_seconds"] - self.broker._elapsed_seconds(run_id))
    request = RuntimeRequest(driver.provider_id, operation, "harness", {"provider_id": driver.provider_id, **snapshot},
        turn_prompt(query, continuing=resuming_thread or turn_count > 1, acceptance_spec=state.get("acceptance_spec"), version=protocol),
        terminal_schema(state.get("acceptance_spec"), version=protocol), cwd=str(workspace),
        instructions=_developer_instructions(full_access=full_access, budget=budget),
        permissions={"full_access": full_access, "acceptance_direct_baseline": direct_baseline},
        session=RuntimeSession(driver.provider_id, thread_id) if thread_id else None,
        deadline=time.monotonic() + remaining, tool_session={"run_id": run_id, "capability": capability},
        context=conversation_context(self.store, run_id, baseline=bool(state.get("acceptance_spec"))))
    context = {"run_id": run_id, "state": state, "workspace": workspace, "capability": capability,
        "thread_id": thread_id, "turn_count": turn_count, "cleanup_state": cleanup_state, "final_response": "", "read_scope": scope}
    context["report_contract"] = self.broker.report_contract(run_id)
    from .runtime_input_control import InputPause, outcome as input_outcome
    final_response = ""
    try:
        from .runtime_contract import await_business
        result = await await_business(driver.harness_turn(request, context), deadline=request.deadline)
        from .runtime_report_convergence import raise_if_stopped
        raise_if_stopped(self.store.get_harness_state(run_id))
        request.remaining()
        thread_id = context["thread_id"]
        if not result.cleanup_complete:
            from .runtime_contract import RuntimeContractError
            raise RuntimeContractError("runtime_cleanup_incomplete")
        if protocol == 2:
            from .runtime_input_control import pending, outcome
            if pending(self.store, run_id):
                return outcome(self, run_id, context, turn_count, run_started)
        final_response = result.final_response
        result.session.require_provider(driver.provider_id)
        if context.get("native_complete") is False:
            raise RuntimeError("runtime_structured_terminal_missing")
        self.store.update_harness_state(run_id, {"turn_count": turn_count, "thread_id": thread_id})
    except InputPause:
        return input_outcome(self, run_id, context, turn_count, run_started)
    except (TimeoutError, RuntimeContractError) as exc:
        if isinstance(exc, RuntimeContractError) and exc.code != "runtime_deadline_exceeded":
            terminal_outcome = _terminal_handoff_outcome(self, driver, run_id, context, turn_count, run_started, exc)
            if terminal_outcome is not None:
                return terminal_outcome
            raise
        if getattr(exc, "detail", {}).get("cleanup_failure_code"):
            self.store.update_harness_state(run_id, {"cleanup_incomplete": True})
        # Native execution has already run its bounded owned-process cleanup.
        thread_id, final_response = context["thread_id"], context["final_response"]
        self.store.fail_running_harness_tool_calls(
            run_id,
            code="harness_deadline_exceeded",
            message="The free-query hard deadline was reached.",
        )
        calls, evidence = self.broker.snapshot(run_id)
        budget = self.broker.budget_snapshot(run_id)
        raw_calls = self.store.list_harness_tool_calls(run_id)
        web_search_count, discovered, activated = _persistent_harness_counts(
            self.store, run_id
        )
        recovered = _latest_validated_presentation(raw_calls)
        if recovered is not None and not state.get("acceptance_spec"):
            try:
                partial_payload = json.loads(final_response)
            except (json.JSONDecodeError, TypeError):
                partial_payload = {}
            known_evidence = {
                str(item.get("evidence_ref"))
                for item in evidence
                if item.get("evidence_ref")
            }
            evidence_refs = [
                str(item)
                for item in partial_payload.get("evidence_refs") or []
                if str(item) in known_evidence
            ]
            execute_evidence = {
                str(call.get("evidence_ref"))
                for call in calls
                if call.get("tool") == "sap_query_execute"
                and call.get("status") == "completed"
                and call.get("evidence_ref")
            }
            executed_plans = [
                item
                for item in partial_payload.get("executed_plans") or []
                if isinstance(item, dict)
                and str(item.get("evidence_ref") or "") in execute_evidence
            ]
            missing_evidence = _effective_missing_evidence(
                partial_payload.get("missing_evidence"), raw_calls
            )
            self.store.append_event(
                run_id,
                "validated_report_recovered",
                {"reason": "turn_completion_timeout"},
            )
            return HarnessOutcome(
                thread_id=thread_id,
                turn_count=turn_count,
                status="inconclusive",
                stop_reason="limit_reached",
                summary={
                    "zh": str(
                        (partial_payload.get("summary") or {}).get("zh")
                        or "已恢复通过引用校验的业务报告。"
                    ),
                    "en": str(
                        (partial_payload.get("summary") or {}).get("en")
                        or "The evidence-validated business report was recovered."
                    ),
                },
                source_complete=_evidence_sources_complete(evidence),
                business_complete=False,
                missing_evidence=sorted(set(missing_evidence + ["harness_structured_terminal_missing"])),
                evidence_refs=evidence_refs,
                executed_plans=executed_plans,
                tool_calls=calls,
                evidence=evidence,
                web_search_count=web_search_count,
                discovered_tool_count=discovered,
                activated_tool_count=activated,
                presentation=recovered,
                budgeted_tool_call_count=_budgeted_tool_call_count(calls),
                elapsed_seconds=int(time.monotonic() - run_started),
                hard_limit_seconds=budget["hard_limit_seconds"],
                query_seconds_granted=budget["query_seconds_granted"],
                finalization_seconds_reserved=budget["finalization_seconds_reserved"],
                extension_count=budget["extension_count"],
                extension_reasons=budget["extension_reasons"],
                deadline_phase="completed",
            )
        return HarnessOutcome(
            thread_id=thread_id,
            turn_count=turn_count,
            status="inconclusive",
            stop_reason="limit_reached",
            summary={
                "zh": f"已达到本轮{budget['hard_limit_seconds']}秒上限；已保留取得的证据。",
                "en": f"The run's {budget['hard_limit_seconds']}-second limit was reached; collected evidence was preserved.",
            },
            missing_evidence=["harness_deadline_exceeded"],
            tool_calls=calls,
            evidence=evidence,
            web_search_count=web_search_count,
            discovered_tool_count=discovered,
            activated_tool_count=activated,
            budgeted_tool_call_count=_budgeted_tool_call_count(calls),
            elapsed_seconds=int(time.monotonic() - run_started),
            limit_kind="runtime_seconds",
            presentation=_deadline_presentation(evidence),
            hard_limit_seconds=budget["hard_limit_seconds"],
            query_seconds_granted=budget["query_seconds_granted"],
            finalization_seconds_reserved=budget["finalization_seconds_reserved"],
            extension_count=budget["extension_count"],
            extension_reasons=budget["extension_reasons"],
            deadline_phase="completed",
        )
    except Exception as exc:
        from .runtime_report_convergence import ReportCorrectionStopped
        terminal_outcome = _terminal_handoff_outcome(self, driver, run_id, context, turn_count, run_started, exc)
        if terminal_outcome is not None:
            return terminal_outcome
        if isinstance(exc, ReportCorrectionStopped):
            calls, evidence = self.broker.snapshot(run_id)
            budget = self.broker.budget_snapshot(run_id)
            self.store.append_event(run_id, "report_correction_stalled", exc.detail)
            return HarnessOutcome(thread_id=context["thread_id"], turn_count=turn_count,
                status="inconclusive", stop_reason="capability_unavailable",
                summary={"zh": "报告结构多次纠错仍未收敛；已保留证据，未生成未经验证的业务结论。",
                    "en": "Report correction made no further progress; evidence was preserved without publishing unvalidated conclusions."},
                source_complete=_evidence_sources_complete(evidence), business_complete=False,
                missing_evidence=[exc.code], tool_calls=calls, evidence=evidence,
                elapsed_seconds=int(time.monotonic() - run_started),
                hard_limit_seconds=budget["hard_limit_seconds"],
                query_seconds_granted=budget["query_seconds_granted"],
                finalization_seconds_reserved=budget["finalization_seconds_reserved"],
                deadline_phase="completed")
        if getattr(exc, "code", "") == "runtime_cleanup_incomplete":
            self.store.update_harness_state(run_id, {"cleanup_incomplete": True})
            raise
        if isinstance(exc, AcceptanceReportValidationError):
            raise
        thread_id = context["thread_id"]
        deadline_budget = self.broker.budget_snapshot(run_id)
        if (
            deadline_budget.get("deadline_phase") == "deadline_exceeded"
            and not self.store.get_run(run_id).cancel_requested
        ):
            self.store.fail_running_harness_tool_calls(
                run_id,
                code="harness_deadline_exceeded",
                message="The free-query hard deadline was reached.",
            )
            calls, evidence = self.broker.snapshot(run_id)
            web_search_count, discovered, activated = _persistent_harness_counts(
                self.store, run_id
            )
            return HarnessOutcome(
                thread_id=thread_id,
                turn_count=turn_count,
                status="inconclusive",
                stop_reason="limit_reached",
                summary={
                    "zh": f"已达到本轮{deadline_budget['hard_limit_seconds']}秒上限；已保留取得的证据。",
                    "en": f"The run's {deadline_budget['hard_limit_seconds']}-second limit was reached; collected evidence was preserved.",
                },
                missing_evidence=["harness_deadline_exceeded"],
                tool_calls=calls,
                evidence=evidence,
                web_search_count=web_search_count,
                discovered_tool_count=discovered,
                activated_tool_count=activated,
                budgeted_tool_call_count=_budgeted_tool_call_count(calls),
                elapsed_seconds=int(time.monotonic() - run_started),
                limit_kind="runtime_seconds",
                presentation=_deadline_presentation(evidence),
                hard_limit_seconds=deadline_budget["hard_limit_seconds"],
                query_seconds_granted=deadline_budget["query_seconds_granted"],
                finalization_seconds_reserved=deadline_budget[
                    "finalization_seconds_reserved"
                ],
                extension_count=deadline_budget["extension_count"],
                extension_reasons=deadline_budget["extension_reasons"],
                deadline_phase="completed",
            )
        if "interrupted" in str(exc).casefold() or self.store.get_run(run_id).cancel_requested:
            calls, evidence = self.broker.snapshot(run_id)
            web_search_count, discovered, activated = _persistent_harness_counts(
                self.store, run_id
            )
            return HarnessOutcome(
                thread_id=thread_id,
                turn_count=turn_count,
                status="inconclusive",
                stop_reason="interrupted",
                summary={"zh": "查询已中断。", "en": "The query was interrupted."},
                missing_evidence=["run_interrupted"],
                tool_calls=calls,
                evidence=evidence,
                web_search_count=web_search_count,
                discovered_tool_count=discovered,
                activated_tool_count=activated,
            )
        if "capability_isolation_failed" not in str(exc):
            calls, evidence = self.broker.snapshot(run_id)
            if calls or evidence:
                elapsed = int(time.monotonic() - run_started)
                time_exhausted = elapsed >= max(
                    1, deadline_budget["hard_limit_seconds"]
                )
                code = str(getattr(exc, "code", "") or f"{driver.provider_id}_harness_runtime_error")[:100]
                self.store.append_event(
                    run_id,
                    "harness_runtime_degraded",
                    {"code": code, "time_exhausted": time_exhausted},
                )
                web_search_count, discovered, activated = _persistent_harness_counts(
                    self.store, run_id
                )
                return HarnessOutcome(
                    thread_id=thread_id,
                    turn_count=turn_count,
                    status="inconclusive",
                    stop_reason="limit_reached" if time_exhausted else "capability_unavailable",
                    summary={
                        "zh": (
                            "Harness 达到运行时间上限；已保留本次只读查询证据。"
                            if time_exhausted
                            else "Harness 运行时中断；已保留本次只读查询证据。"
                        ),
                        "en": (
                            "The Harness reached its runtime limit; collected read-only evidence was preserved."
                            if time_exhausted
                            else "The Harness runtime was interrupted; collected read-only evidence was preserved."
                        ),
                    },
                    missing_evidence=[
                        "harness_deadline_exceeded"
                        if time_exhausted
                        else "harness_runtime_unavailable"
                    ],
                    tool_calls=calls,
                    evidence=evidence,
                    web_search_count=web_search_count,
                    discovered_tool_count=discovered,
                    activated_tool_count=activated,
                    budgeted_tool_call_count=_budgeted_tool_call_count(calls),
                    elapsed_seconds=elapsed,
                    limit_kind="runtime_seconds" if time_exhausted else None,
                    hard_limit_seconds=deadline_budget["hard_limit_seconds"],
                    query_seconds_granted=deadline_budget["query_seconds_granted"],
                    finalization_seconds_reserved=deadline_budget[
                        "finalization_seconds_reserved"
                    ],
                    extension_count=deadline_budget["extension_count"],
                    extension_reasons=deadline_budget["extension_reasons"],
                    deadline_phase=(
                        "completed"
                        if time_exhausted
                        else deadline_budget["deadline_phase"]
                    ),
                )
        raise
    finally:
        import sys
        primary = sys.exception()
        if cleanup_state.get("started") and cleanup_state.get("complete") is not True:
            self.store.update_harness_state(run_id, {"cleanup_incomplete": True})
        from .runtime_contract import drain_cleanup, begin_cleanup
        begin_cleanup()
        try:
            await drain_cleanup(driver.cleanup(run_id, context))
        except BaseException as cleanup_error:
            self.store.update_harness_state(run_id, {"cleanup_incomplete": True})
            if primary is None:
                raise
            primary.detail = dict(getattr(primary, "detail", {}) or {},
                                  cleanup_failure_code="runtime_cleanup_incomplete")
        finally:
            failure = self.store.get_harness_state(run_id).get("terminal_handoff_failure")
            if failure:
                complete = cleanup_state.get("complete") is True and not self.store.get_harness_state(run_id).get("cleanup_incomplete")
                self.store.update_harness_state(run_id, {"terminal_handoff_failure": {**failure,
                    "cleanup_complete": complete, "cleanup_failure_code": None if complete else "runtime_cleanup_incomplete"}})
                self.store.append_event(run_id, "terminal_handoff_cleanup_confirmed" if complete else "terminal_handoff_cleanup_pending",
                    {"provider_id": driver.provider_id, "cleanup_complete": complete})
            self.broker.close_session(run_id)
        from .runtime_changesets import RuntimeChangeSets
        try:
            change_set = (
                RuntimeChangeSets(self.settings.data_root / "runtime-change-sets").create(
                    engineering, source_id=run_id
                )
                if engineering is not None
                else None
            )
            if change_set:
                self.store.update_harness_state(run_id, {"change_set_id": change_set["change_set_id"]})
                self.store.append_event(run_id, "runtime_changeset_created", {
                    "change_set_id": change_set["change_set_id"], "status": change_set["status"], "can_apply": False})
        except Exception as error:
            self.store.append_event(run_id, "runtime_changeset_blocked", {
                "code": getattr(error, "code", "runtime_changeset_collection_failed")})
    if not final_response and self.store.get_run(run_id).cancel_requested:
        calls, evidence = self.broker.snapshot(run_id)
        web_search_count, discovered, activated = _persistent_harness_counts(
            self.store, run_id
        )
        return HarnessOutcome(
            thread_id=thread_id,
            turn_count=turn_count,
            status="inconclusive",
            stop_reason="interrupted",
            summary={"zh": "查询已中断。", "en": "The query was interrupted."},
            missing_evidence=["run_interrupted"],
            tool_calls=calls,
            evidence=evidence,
            web_search_count=web_search_count,
            discovered_tool_count=discovered,
            activated_tool_count=activated,
        )
    if cleanup_state.get("started") and cleanup_state.get("complete") is not True:
        raise RuntimeError("runtime_cleanup_incomplete")
    try:
        payload = json.loads(final_response)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("Runtime Harness did not return its structured final result.") from exc
    from .runtime_diagnostics import checked_output
    try:
        payload = checked_output(final_response, request.output_schema, operation=operation)
    except RuntimeContractError as exc:
        self.store.append_event(run_id, "harness_output_rejected", {"operation": operation,
            "code": exc.code, "validation_issues": exc.detail["validation_issues"]})
        if state.get("acceptance_spec"):
            raise AcceptanceReportValidationError(exc.detail["validation_issues"]) from None
        raise
    from .runtime_harness_result import finalize
    outcome = finalize(self.settings, self.store, self.broker, run_id, payload,
        thread_id=thread_id, turn_count=turn_count, run_started=run_started)
    self.store.update_harness_state(run_id, {"platform_clarification_question":
        outcome.clarification_question if outcome.status == "waiting_input" else None})
    return outcome


async def tool_call(controller, context, name, arguments):
    """Uniform callback for native drivers; real Broker enforces this at admission."""
    from .runtime_query_contract import plans
    run_id, scope, broker = context["run_id"], context["read_scope"], controller.broker
    broker.review_deadline(run_id)
    # The production Broker checks normalized plans and Skill inputs itself, so
    # Codex MCP calls and worker callbacks have exactly the same enforcement.
    if not callable(getattr(broker, "bind_read_scope", None)):
        if name in {"sap_query_validate", "sap_query_execute"}:
            plans(arguments.get("plan"))
            plan = broker.normalizer.normalize_plan(arguments["plan"])
            scope.check(plan)
            arguments = {**arguments, "plan": plan}
            if name == "sap_query_execute":
                validated = await broker.handle(run_id, context["capability"], "sap_query_validate", arguments)
                if validated.get("ok") is not True:
                    return validated
                plan = validated.get("validated_plan")
                scope.check(plan)
                arguments = {**arguments, "plan": plan}
        elif name == "sap_skill_execute":
            scope.check_skill(arguments)
    return await broker.handle(run_id, context["capability"], name, arguments)

async def report_tool_completed(controller, run_id, turn):
    """A report can finish early only after the platform saved its validated pair."""
    from .harness import (_validated_payload_from_store, _best_effort_interrupt, AcceptanceReportValidationError)
    from .runtime_report_convergence import stop_state, raise_if_stopped
    failure = stop_state(controller.store.get_harness_state(run_id))
    if failure:
        interrupt_error = await _best_effort_interrupt(turn)
        controller.store.append_event(run_id, "report_validation_stopped", {
            "code": failure.get("stop_code") or "acceptance_report_validation_failed",
            "validation_issues": failure.get("validation_issues") or [], "interrupt_error": interrupt_error})
        raise_if_stopped(controller.store.get_harness_state(run_id))
    payload = _validated_payload_from_store(controller.store, run_id, controller.broker.snapshot(run_id)[1])
    if payload is not None:
        interrupt_error = await _best_effort_interrupt(turn)
        controller.store.append_event(run_id, "validated_report_completed_early", {"interrupt_error": interrupt_error})
        return payload
    return None

