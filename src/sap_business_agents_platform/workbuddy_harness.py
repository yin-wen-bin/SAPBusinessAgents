"""Compatibility facade over shared Harness orchestration and an owned driver."""
from __future__ import annotations

import time
from .harness import AcceptanceReportValidationError


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
