"""Compatibility facade over shared Harness orchestration and an owned driver."""
from __future__ import annotations

import time
from .harness import AcceptanceReportValidationError


def native_terminal_instructions(schema):
    """Explain native envelope encoding; never alter the authoritative Schema.

    The pinned CLI validates the formatter separately from the SAP report tool.
    Nullable clarification fields are not identifiers from the user's query.
    Keep this provider-only guidance outside shared business prompts and Codex.
    """
    instructions = "\nSDK terminal encoding: use StructuredOutput with the entire frozen output object, not a fragment."
    fields = ("input_kind", "input_field")
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not all(name in required and isinstance(properties.get(name), dict)
               and isinstance(properties[name].get("type"), list)
               and "null" in properties[name]["type"]
               and isinstance(properties[name].get("enum"), list)
               and None in properties[name]["enum"] for name in fields):
        return instructions
    json_nulls = '{"input_kind": null, "input_field": null}'
    instructions += (
        "\nThe required nullable fields input_kind and input_field describe the secure-reference "
        "clarification path already defined in the shared task instructions; they are NOT SAP "
        "query parameters or queried column names. Use their non-null enum values only when "
        "those instructions require that clarification. Otherwise explicitly include " + json_nulls + ". "
        "Use literal JSON null, not an empty string or the strings 'null', 'None' or 'undefined'. "
        "Do not omit a required nullable field to fix an enum error. Copy these two members into "
        "the full final envelope, not as a standalone response. A successful "
        "sap_final_report_validate validates the presentation, not this complete native envelope; "
        "finish only after the full StructuredOutput object satisfies the unchanged Schema."
    )
    return instructions


def parse_output(text, schema, *, formal=False):
    from .runtime_diagnostics import checked_output
    from .runtime_contract import RuntimeContractError
    from .workbuddy_compat import translate
    try:
        return checked_output(text, schema, operation="acceptance_free_query" if formal else "free_query")
    except RuntimeContractError as exc:
        failure = translate(exc)
        if formal:
            raise AcceptanceReportValidationError(failure.detail["validation_issues"]) from None
        raise failure from None


class WorkBuddyHarnessController:
    def __init__(self, settings, store, broker, manager):
        self.settings, self.store, self.broker, self.manager = settings, store, broker, manager

    async def interrupt(self, run_id):
        return await self.manager.supervisor.cancel(run_id)

    async def run(self, run_id, query, thread_id=None, model=None, reasoning_effort=None):
        from .runtime_harness import run
        from .workbuddy_driver import WorkBuddyHarnessDriver
        return await run(self, WorkBuddyHarnessDriver(self), run_id, query, thread_id, model, reasoning_effort)

    def preflight(self, state):
        from .runtime_harness import preflight
        return preflight(self.settings, state)

    def conversation_context(self, run_id, *, baseline: bool):
        from .runtime_harness import conversation_context
        return conversation_context(self.store, run_id, baseline=baseline)

    def validate_result(self, run_id, raw):
        from .runtime_harness_result import finalize
        return finalize(self.settings, self.store, self.broker, run_id, raw,
            thread_id=None, turn_count=int(self.store.get_harness_state(run_id).get("turn_count", 0)) + 1,
            run_started=time.monotonic())
