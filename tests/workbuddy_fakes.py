"""SDK simulation in tests only. Production always crosses a process boundary."""
from sap_business_agents_platform.workbuddy_supervisor import WorkBuddySupervisor
from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
from sap_business_agents_platform import workbuddy_worker


def inline_worker(monkeypatch):
    async def run(self, *, task_id, snapshot, operation, payload, seconds, mode="bounded", emit=None, **_):
        request = {"operation": operation, "payload": payload, "snapshot": snapshot,
                   "cli_path": "verified-fixture-cli", "mode": mode}
        def send(kind, **data):
            if kind == "event" and emit:
                emit(data["event"], data["data"])
        try:
            return await workbuddy_worker.execute(request, send)
        finally:
            if task_id:
                self.closed.add(task_id)
    monkeypatch.setattr(WorkBuddySupervisor, "run", run)
    monkeypatch.setattr(WorkBuddyManager, "runtime_snapshot", lambda self, model=None, **_: {
        "provider_id": "workbuddy", "environment_digest": "f" * 64, "model": model,
        "reasoning_effort": None, "configuration_digest": "fixture"})
