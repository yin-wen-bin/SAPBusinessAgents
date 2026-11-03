"""One sample-discovery lifecycle and evidence/result gate for every driver."""
import asyncio
import copy
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
    readiness = context.readiness()
    gaps = readiness["blocking_fields"]
    if gaps:
        return adapt(context.result("needs_input", missing=gaps,
            codes=["sample_input_requires_manual_entry", *readiness.get("codes", [])]))
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
        await await_business(_read_singleton_candidate(service, run_id, context, capability),
                             deadline=context.started + context.max_seconds)
        if service.store.get_run(run_id).cancel_requested:
            return adapt(context.result("inconclusive", codes=["sample_discovery_cancelled"]))
        if context.ready_result:
            return adapt(context.ready_result)
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


def _singleton_candidate_plan(context):
    """An enum is a search constraint, never evidence that a SAP row exists."""
    from .sample_discovery import _INPUT
    missing = context.missing_fields()
    if len(missing) != 1 or context.scope_graph.active or len(context.plans) != 1:
        return None
    name = missing[0]
    spec = context.properties.get(name, {})
    values = spec.get("enum")
    if (spec.get("type") not in {"string", "integer", "number", "boolean"}
            or not isinstance(values, list) or len(values) != 1):
        return None
    candidate = values[0]
    if list(Draft202012Validator(spec).iter_errors(candidate)):
        return None
    declaration = context.plans[0]
    bindings = [(field, op) for field, bound, op in context._bindings(declaration) if bound == name]
    if len(bindings) != 1 or bindings[0][1] != "eq":
        return None
    plan = copy.deepcopy(declaration)
    for item in plan.get("filters") or []:
        if not isinstance(item, dict):
            return None
        value = item.get("value")
        match = _INPUT.fullmatch(str(value or ""))
        if match:
            bound = match.group(1)
            if bound != name and bound not in context.supplied_inputs:
                return None
            item["value"] = candidate if bound == name else context.supplied_inputs[bound]
        elif "{{" in str(value):
            return None
    plan["top"] = 1
    return plan


async def _read_singleton_candidate(service, run_id, context, capability):
    """Use the ordinary authenticated Broker, including scope, budget and audit gates."""
    from .sample_discovery import SampleDiscoveryError
    plan = _singleton_candidate_plan(context)
    if plan is None:
        return
    source = tuple(plan[key] for key in ("service_name", "odata_version", "entity_set"))
    schema = await service.broker.handle(run_id, capability, "sap_schema_get", {
        "service_name": source[0], "odata_version": source[1], "entity_sets": [source[2]],
        "max_fields": 5000})
    if schema.get("ok") is not True or service.store.get_run(run_id).cancel_requested:
        return
    keys = context.live_keys.get(source) or []
    if not keys or not set(keys).issubset(plan.get("select_fields") or []):
        return
    plan["order_by"] = list(keys)
    try:
        context.check_plan(plan)
        context.check_stable_keys(plan)
    except SampleDiscoveryError:
        return
    validated = await service.broker.handle(run_id, capability, "sap_query_validate", {"plan": plan})
    if validated.get("ok") is not True or service.store.get_run(run_id).cancel_requested:
        return
    await service.broker.handle(run_id, capability, "sap_query_execute", {
        "plan": validated.get("validated_plan") or plan})
    # Only tool_completed's existing evidence-cell validator may set ready_result.


def native_sample_allowed(kind, custom_kind):
    return kind not in {"webSearch", "commandExecution", "fileChange", "computerUse",
        "collabAgentToolCall", "dynamicToolCall"} and custom_kind not in {"forbidden", "web_search"}
