from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Protocol

from .database import RunStore


class WorkloadClass(StrEnum):
    deterministic = "deterministic"
    free_query = "free_query"
    feedback_review = "feedback_review"
    role_matching = "role_matching"


JobHandler = Callable[[str], Awaitable[None]]


class RunScheduler(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def enqueue(
        self,
        workload_class: WorkloadClass,
        subject_id: str,
        *,
        priority: int = 100,
    ) -> dict[str, Any]: ...

    async def cancel(self, job_id: str) -> dict[str, Any]: ...

    def queue_position(self, job_id: str) -> int | None: ...


@dataclass(slots=True)
class _Lane:
    queue: asyncio.PriorityQueue[tuple[int, int, str | None]]
    workers: list[asyncio.Task[None]]


class LocalRunScheduler:
    """SQLite-backed local scheduler with production-shaped leases and lanes."""

    def __init__(
        self,
        store: RunStore,
        handlers: dict[WorkloadClass, JobHandler],
        *,
        worker_counts: dict[WorkloadClass, int],
        lease_seconds: int = 60,
    ) -> None:
        self.store = store
        self.handlers = dict(handlers)
        self.workloads = set(self.handlers)
        self.worker_counts = {
            workload: max(1, int(worker_counts.get(workload, 1)))
            for workload in self.workloads
        }
        self.lease_seconds = max(15, int(lease_seconds))
        self.instance_id = f"local-{uuid.uuid4().hex[:12]}"
        self._counter = 0
        self._started = False
        self._stopping = False
        self._lanes = {
            workload: _Lane(asyncio.PriorityQueue(), []) for workload in self.workloads
        }

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stopping = False
        for workload, lane in self._lanes.items():
            for index in range(self.worker_counts[workload]):
                lane.workers.append(
                    asyncio.create_task(
                        self._worker(workload),
                        name=f"sapba-{workload.value}-worker-{index + 1}",
                    )
                )
        await self.recover()

    async def stop(self) -> None:
        if not self._started:
            return
        self._stopping = True
        for workload, lane in self._lanes.items():
            for _ in lane.workers:
                self._counter += 1
                await lane.queue.put((10_000, self._counter, None))
        workers = [task for lane in self._lanes.values() for task in lane.workers]
        if workers:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*workers, return_exceptions=True), timeout=5
                )
            except TimeoutError:
                for task in workers:
                    task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
        for lane in self._lanes.values():
            lane.workers.clear()
        self._lanes = {
            workload: _Lane(asyncio.PriorityQueue(), [])
            for workload in self.workloads
        }
        self._started = False
        self._stopping = False

    async def enqueue(
        self,
        workload_class: WorkloadClass,
        subject_id: str,
        *,
        priority: int = 100,
    ) -> dict[str, Any]:
        job_id = f"job_{uuid.uuid4().hex[:20]}"
        job = self.store.create_execution_job(
            job_id=job_id,
            workload_class=workload_class.value,
            subject_id=subject_id,
            priority=priority,
        )
        await self._put(job)
        return job

    async def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_execution_job(job_id)
        if job["status"] in {"completed", "failed", "cancelled"}:
            return job
        return self.store.finish_execution_job(job_id, status="cancelled")

    def queue_position(self, job_id: str) -> int | None:
        return self.store.execution_queue_position(job_id)

    async def recover(self) -> None:
        for job in self.store.list_execution_jobs(statuses=("queued", "running")):
            if str(job["workload_class"]) not in {item.value for item in self.workloads}:
                continue
            if job["status"] == "running":
                job = self.store.requeue_execution_job(job["job_id"])
            await self._put(job)

    async def _put(self, job: dict[str, Any]) -> None:
        workload = WorkloadClass(str(job["workload_class"]))
        if workload not in self._lanes:
            return
        self._counter += 1
        await self._lanes[workload].queue.put(
            (int(job["priority"]), self._counter, str(job["job_id"]))
        )

    async def _worker(self, workload: WorkloadClass) -> None:
        lane = self._lanes[workload]
        while True:
            _priority, _counter, job_id = await lane.queue.get()
            heartbeat: asyncio.Task[None] | None = None
            try:
                if job_id is None:
                    return
                lease_owner = f"{self.instance_id}:{workload.value}"
                job = self.store.claim_execution_job(
                    job_id,
                    lease_owner=lease_owner,
                    lease_expires_at=self._lease_expiry(),
                )
                if job is None:
                    continue
                heartbeat = asyncio.create_task(
                    self._heartbeat(job_id, lease_owner),
                    name=f"sapba-job-heartbeat-{job_id}",
                )
                handler = self.handlers[workload]
                await handler(str(job["subject_id"]))
                latest = self.store.get_execution_job(job_id)
                if latest["status"] == "running":
                    self.store.finish_execution_job(job_id, status="completed")
            except asyncio.CancelledError:
                if job_id is not None:
                    if self._stopping:
                        self.store.requeue_execution_job(job_id)
                    else:
                        self.store.finish_execution_job(job_id, status="cancelled")
                raise
            except Exception as exc:
                if job_id is not None:
                    self.store.finish_execution_job(
                        job_id,
                        status="failed",
                        error={"type": type(exc).__name__},
                    )
            finally:
                if heartbeat is not None:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
                lane.queue.task_done()

    async def _heartbeat(self, job_id: str, lease_owner: str) -> None:
        interval = max(5, self.lease_seconds // 3)
        while True:
            await asyncio.sleep(interval)
            self.store.heartbeat_execution_job(
                job_id,
                lease_owner=lease_owner,
                lease_expires_at=self._lease_expiry(),
            )

    def _lease_expiry(self) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=self.lease_seconds)).isoformat()
