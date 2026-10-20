"""SDK-free admission scheduling; not a model or business-output contract."""
import asyncio
from dataclasses import dataclass
import time


@dataclass(frozen=True)
class RuntimeAdmissionRequest:
    provider_id: str | None = None
    operation: str | None = None
    permission_mode: str = 'bounded'
    snapshot: dict | None = None
    deadline: float | None = None

    def check_deadline(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            from .sdk_manager import SDKManagerError
            raise SDKManagerError('Admission deadline exceeded.', code='runtime_deadline_exceeded')


@dataclass(frozen=True)
class PreparedRuntime:
    snapshot: dict
    operation: str | None
    permission_mode: str


async def prepare(planner, request):
    callback = getattr(planner, 'prepare_admission', None)
    if callable(callback):
        result = await asyncio.to_thread(callback, request)
        request.check_deadline()
        return result.snapshot
    return await snapshot(planner.snapshot)


def selected_status(manager, selected):
    admission = getattr(manager, 'admission_status', None)
    if callable(admission):
        return admission(selected)
    # Compatibility for injected/legacy managers, not production SDKManager.
    return next((item for item in manager.list() if item.get('provider_id') == selected), None)


async def snapshot(loader):
    # ContextVars are copied by to_thread. All gates complete before persistence.
    return await asyncio.to_thread(loader)


async def driver_binding(driver, run_id, state, model, reasoning_effort):
    if driver.provider_id == 'workbuddy':
        return await asyncio.to_thread(driver.binding, run_id, state, model, reasoning_effort)
    return driver.binding(run_id, state, model, reasoning_effort)
