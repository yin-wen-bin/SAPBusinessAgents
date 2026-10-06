"""Internal staged validation, not a public tool or capability registration API.

The platform validator must check the complete business contract, not just JSON.
This module never marks offline fixtures or a model compatibility probe as live.
"""
from typing import Any, Callable
from .workbuddy_environment import WorkBuddyError


async def validate_operation(manager, snapshot: dict, operation: str, *, execute: Callable,
                             verify: Callable, permission_mode: str, job_id: str) -> dict[str, Any]:
    release = manager.environment.release(snapshot.get("environment_digest"))
    if release.get("sdk_version") != "0.3.247" or not release.get("sdk_wheel_digest"):
        raise WorkBuddyError("workbuddy_verified_sdk_wheel_required")
    # execute invokes the real business implementation with its isolated target;
    # verification grants no extra Broker scope and is inherited only in this task.
    with manager.supervisor.verification(snapshot, {operation}):
        result = await execute()
    evidence = await verify(result)
    if not isinstance(evidence, dict) or evidence.get("platform_contract_passed") is not True:
        raise WorkBuddyError("workbuddy_operation_validation_required")
    manager.record_operation_validation(snapshot, operation, job_id=job_id,
        permission_modes=[permission_mode], evidence={**evidence, "kind": "live_validation"})
    return evidence
