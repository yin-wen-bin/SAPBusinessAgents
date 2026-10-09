"""One sample-discovery lifecycle and evidence/result gate for every driver."""
import asyncio
import json
import time

from jsonschema import Draft202012Validator


async def discover(service, run_id, manifest, supplied_inputs, **kwargs):
    from contextlib import nullcontext
    from .runtime_contract import cleanup_binding
    driver = kwargs["driver"]
    reserve = getattr(getattr(getattr(driver, "manager", None), "supervisor", None), "reserve", None)
    queued_at = time.monotonic()
    async with reserve() if driver.provider_id == "workbuddy" and callable(reserve) else nullcontext():
        if kwargs.get("started") is not None and driver.provider_id == "workbuddy":
            kwargs["started"] += time.monotonic() - queued_at
        with cleanup_binding():
            return await _discover(service, run_id, manifest, supplied_inputs, **kwargs)


async def _discover(service, run_id, manifest, supplied_inputs, *, revision, model, started,
                   selected_fields, driver, binding):
    from .sample_discovery import (SampleDiscoveryContext, SampleDiscoveryError,
        SAMPLE_MODEL, SAMPLE_SECONDS, SAMPLE_QUERY_SECONDS, sample_output_schema)
    if driver.provider_id == "codex" and model != SAMPLE_MODEL:
        raise SampleDiscoveryError("sample_model_must_be_gpt_5_6_sol")
    context = SampleDiscoveryContext(manifest, supplied_inputs, revision,
        selected_fields=selected_fields, started=time.monotonic() if started is None else started)
    context.on_progress = lambda value: service.store.update_harness_state(run_id, {"sample_execution": value})
    context.progress("preparing")
    def adapt(value):
        return {**value, "model": model} if driver.provider_id != "codex" else value
    gaps = context.preflight_gaps()
    if gaps:
        return adapt(context.result("needs_input", missing=gaps, codes=["sample_input_requires_manual_entry"]))
    if not context.missing_fields():
        return adapt(context.result("needs_input", codes=["sample_inputs_already_provided"]))
    service.broker._sample_contexts[run_id] = context
    reserved = SAMPLE_SECONDS - SAMPLE_QUERY_SECONDS
    service.store.update_harness_state(run_id, {"time_budget": {
        "hard_limit_seconds": context.max_seconds,
        "query_seconds_granted": max(1, context.max_seconds - reserved),
        "finalization_seconds_reserved": reserved, "extension_count": 0,
        "extension_reasons": [], "deadline_phase": "querying", "progress_marker": 0}})
    capability = service.broker.open_session(run_id)
    from .runtime_contract import await_business, drain_cleanup, begin_cleanup, RuntimeContractError
    primary = None
    try:
        text = await await_business(driver.sample_turn(service, run_id, context, capability, binding),
                                    deadline=context.started + context.max_seconds)
        if service.store.get_run(run_id).cancel_requested:
            return adapt(context.result("inconclusive", codes=["sample_discovery_cancelled"]))
        context.remaining()
        if context.ready_result:
            return adapt(context.ready_result)
        context.progress("checking_sample")
        payload = json.loads(text)
        if list(Draft202012Validator(sample_output_schema()).iter_errors(payload)):
            raise SampleDiscoveryError("sample_projection_invalid")
        return adapt({**context.validate_result(payload, lambda ref: service.broker._read_evidence(run_id, ref)),
            "selection_method": "runtime"})
    except asyncio.CancelledError as exc:
        primary = exc
        service.quiesce(run_id)
        begin_cleanup()
        try:
            await drain_cleanup(driver.interrupt_sample(service, run_id))
        except BaseException:
            service.store.update_harness_state(run_id, {"cleanup_incomplete": True})
            primary.detail = dict(getattr(primary, "detail", {}) or {}, cleanup_failure_code="runtime_cleanup_incomplete")
        raise
    except Exception as exc:
        primary = exc
        service.quiesce(run_id)
        begin_cleanup()
        try:
            await drain_cleanup(driver.interrupt_sample(service, run_id))
        except BaseException:
            service.store.update_harness_state(run_id, {"cleanup_incomplete": True})
            exc.detail = dict(getattr(exc, "detail", {}) or {}, cleanup_failure_code="runtime_cleanup_incomplete")
        if driver.provider_id != "codex":
            service.store.append_event(run_id, "workbuddy_output_rejected", {"operation": "sample_discovery",
                "code": getattr(exc, "code", "sample_runtime_unavailable"), **getattr(exc, "detail", {})})
        timed_out = isinstance(exc, TimeoutError) or getattr(exc, "code", None) == "runtime_deadline_exceeded"
        code = (exc.code if isinstance(exc, SampleDiscoveryError) or getattr(exc, "code", "") == "runtime_cleanup_incomplete"
            else "sample_discovery_timeout" if timed_out else "sample_runtime_unavailable")
        return adapt({**context.result("timed_out" if timed_out else "inconclusive", codes=[code]),
            "failed_stage": context.phase})
    finally:
        context.closed = True
        context.progress(context.phase)
        pending = [task for task in context.tool_tasks if not task.done()]
        for task in pending:
            task.cancel()
        begin_cleanup()
        try:
            if pending:
                await drain_cleanup(asyncio.gather(*pending, return_exceptions=True))
        except BaseException:
            service.store.update_harness_state(run_id, {"cleanup_incomplete": True})
            if primary is None:
                raise RuntimeContractError("runtime_cleanup_incomplete") from None
            primary.detail = dict(getattr(primary, "detail", {}) or {}, cleanup_failure_code="runtime_cleanup_incomplete")
        finally:
            service._turns.pop(run_id, None)
            service.broker.close_session(run_id)
            service.broker._sample_contexts.pop(run_id, None)


def native_sample_allowed(kind, custom_kind):
    return kind not in {"webSearch", "commandExecution", "fileChange", "computerUse",
        "collabAgentToolCall", "dynamicToolCall"} and custom_kind not in {"forbidden", "web_search"}
