"""Task-bound MCP reads through registered OData and approved read-only Skills."""
from __future__ import annotations

import asyncio
import copy
import time

from .common import TestAgentError, digest, now, read_json, safe_path, write_json
from .state import task_plan


def provider_for(repository):
    return PlatformReadTool(platform_capabilities(repository))


class PlatformReadTool:
    def __init__(self, context):
        self.context = context

    async def health(self):
        return await self.context.manager.health("embedded-sap-odata")

    async def execute_plan(self, plan, query=""):
        return await self.context.sap_read.execute_plan(plan, query=query)


def platform_capabilities(repository):
    from dataclasses import replace
    from sap_business_agents_platform.config import Settings
    from sap_business_agents_platform.read_capabilities import build_read_capabilities
    settings = Settings.from_env(repository)
    settings = replace(settings, sap_max_results=min(settings.sap_max_results, 5000),
                       sap_page_size=min(settings.sap_page_size, 500))
    return build_read_capabilities(settings)


def skill_registry_for(repository):
    return PlatformSkillTool(platform_capabilities(repository))


class PlatformSkillTool:
    def __init__(self, context):
        self.context = context

    def get(self, skill_id):
        return self.context.skill_registry.get(skill_id)

    def validate_input(self, skill_id, payload):
        return self.context.skill_registry.validate_input(skill_id, payload)

    async def execute(self, skill_id, payload):
        health = await self.context.manager.health("sapskillhub")
        if health.get("ok") is not True:
            raise TestAgentError("approved_skill_capability_unavailable")
        return await self.context.skills.execute(skill_id, payload)


async def capabilities(repository):
    context = platform_capabilities(repository)
    health = await context.manager.health("embedded-sap-odata")
    skill_health = await context.manager.health("sapskillhub")
    return {"transport": "platform-capability", "sap_read": {"ok": health.get("ok") is True,
            "capability": "sap_read.v2", "plugin": context.sap_read.plugin_metadata("execute_plan")},
            "approved_skill_count": len(context.skills.list_all_approved_skills()) if skill_health.get("ok") else 0,
            "entrypoints": ["native-approved-read-tool", "sap-read CLI", "test_sap_read MCP"],
            "business_read_performed": False}


def json_pointer(value, pointer):
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise TestAgentError("skill_rows_pointer_invalid")
    for key in pointer.split("/")[1:]:
        key = key.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


class ReadBudget:
    def __init__(self):
        self.closed, self.read_count, self.max_reads, self.business_get_count = False, 1, 10, 0
        self.started = time.monotonic()

    def remaining(self, external=False):
        return max(0, (540 if external else 600) - (time.monotonic() - self.started))


async def await_read(store, task_id, awaitable, budget):
    operation = asyncio.create_task(awaitable)
    try:
        while not operation.done():
            if store.get("task", task_id)["cancel_requested"]:
                raise TestAgentError("task_cancelled")
            if not budget.remaining(external=True):
                raise TestAgentError("sap_read_timeout")
            await asyncio.wait({operation}, timeout=0.1)
        return await operation
    finally:
        if not operation.done():
            budget.closed = True
            operation.cancel()
            try:
                await asyncio.wait_for(operation, timeout=10)
            except (asyncio.CancelledError, TimeoutError):
                pass


def bind_sample(value, sample):
    if isinstance(value, dict):
        return {k: bind_sample(v, sample) for k, v in value.items()}
    if isinstance(value, list):
        return [bind_sample(v, sample) for v in value]
    if isinstance(value, str) and value.startswith("$sample."):
        field = value[len("$sample."):]
        if field not in sample or sample[field] is None:
            raise TestAgentError("sample_binding_missing", field=field)
        return sample[field]
    return value


async def read(store, task_id, case_id, role, provider=None, skill_registry=None):
    if role not in {"sample", "baseline", "anchor"}:
        raise TestAgentError("sap_role_invalid")
    with store.transaction() as db:
        task = store.get("task", task_id, db)
        if task["phase"] not in {"collecting", "verifying", "finalizing"} or task["cancel_requested"]:
            raise TestAgentError("task_not_executing")
        if case_id not in task["queue"]:
            raise TestAgentError("case_not_in_round")
        case = next(c for c in task_plan(store, task)["cases"] if c["id"] == case_id)
        recipe = case.get("sample" if role == "sample" else "baseline")
        if not recipe or bool(isinstance(recipe.get("plan"), dict)) == bool(recipe.get("skill_id")):
            raise TestAgentError("sap_recipe_required")
        if case["mode"] != "live_sap":
            raise TestAgentError("sap_read_not_in_live_case")
        receipt_id = f"{task_id}.{task['round']}.{case_id}.{role}"
        try:
            receipt = store.get("sap_receipt", receipt_id, db)
            if receipt["status"] == "completed":
                return {**receipt, "reused": True}
            raise TestAgentError("sap_read_outcome_unknown", receipt_id=receipt_id)
        except TestAgentError as error:
            if error.code != "record_not_found":
                raise
        sample = {}
        if case.get("sample") and role != "sample":
            sample_id = f"{task_id}.{task['round']}.{case_id}.sample"
            sample_receipt = store.get("sap_receipt", sample_id, db)
            if sample_receipt["status"] != "completed":
                raise TestAgentError("sample_not_ready")
            rows = read_json(safe_path(store.task_dir(task_id), sample_receipt["artifact"]))["rows"]
            if not rows:
                raise TestAgentError("sample_not_found")
            sample = rows[0]
        skill_id = recipe.get("skill_id")
        if skill_id:
            query_plan = {"skill_id": skill_id, "input": bind_sample(copy.deepcopy(recipe.get("input", {})), sample)}
            skill_registry = skill_registry or skill_registry_for(store.repository)
            skill = skill_registry.get(skill_id)
            if skill.get("read_only") is not True or skill.get("validated") is not True:
                raise TestAgentError("skill_not_approved")
            skill_registry.validate_input(skill_id, query_plan["input"])
            if skill_id == "sap-adt-table-export":
                limit = query_plan["input"].get("max_rows")
                if type(limit) is not int or not 1 <= limit <= (100 if role == "sample" else 5000):
                    raise TestAgentError("skill_row_budget_invalid")
        else:
            query_plan = bind_sample(copy.deepcopy(recipe["plan"]), sample)
            # Raw URLs, SQL and credentials are excluded by the existing strict plan schema.
            from sap_business_agents_platform.runtime_query_contract import plans
            plans(query_plan)
            steps = query_plan.get("steps") or [query_plan]
            if role == "sample" and (len(steps) != 1 or type(steps[0].get("top", 100)) is not int
                                      or not 1 <= steps[0].get("top", 100) <= 100):
                raise TestAgentError("sample_budget_invalid")
            for step in steps:
                if step.get("plan_kind", "direct") not in {"direct", "lookup", "multi_step"}:
                    raise TestAgentError("sap_function_not_allowed")
                limit = step.get("top")
                if limit is not None and (type(limit) is not int or not 1 <= limit <= 5000):
                    raise TestAgentError("sap_row_budget_invalid")
        receipt = {"id": receipt_id, "task_id": task_id, "case_id": case_id, "round": task["round"],
                   "role": role, "status": "running", "query_digest": digest(query_plan), "started_at": now()}
        store.put("sap_receipt", receipt_id, receipt, db)
        store.event(task_id, "sap_read_started", {"receipt_id": receipt_id, "role": role}, db)
    budget = ReadBudget()
    try:
        from sap_business_agents_platform import sample_read_budget
        if skill_id:
            raw = await await_read(store, task_id, skill_registry.execute(skill_id, query_plan["input"]), budget)
            completeness = raw.get("completeness", {})
            rows = json_pointer(raw, recipe.get("rows_pointer", "/rows"))
            if not isinstance(rows, list):
                raise TestAgentError("skill_rows_invalid")
            complete = (completeness.get("source_complete") is True and completeness.get("paging_complete", True) is True
                        and raw.get("status") not in {"failed", "blocked", "error", "partial"})
            response = {"ok": raw.get("ok") is not False and raw.get("status") not in {"failed", "blocked", "error"}, "provider_id": "approved-skill:" + skill_id,
                        "source_complete": complete, "source_truncated": completeness.get("source_truncated") is True,
                        "data": {"results": rows}}
        else:
            provider = provider or provider_for(store.repository)
            health = await provider.health()
            if health.get("ok") is not True:
                raise TestAgentError("sap_configuration_unavailable")
            with sample_read_budget.bind(budget):
                response = await await_read(store, task_id, provider.execute_plan(query_plan, query=""), budget)
        if response.get("ok") is not True:
            raise TestAgentError("sap_read_rejected")
        body = {"schema_version": 1, "task_id": task_id, "case_id": case_id, "round": task["round"],
                "role": role, "provider_id": response.get("provider_id"),
                "method": "approved_skill" if skill_id else "GET", "semantic_read_only": True,
                "query_digest": digest(query_plan), "scope": query_plan,
                "source_complete": response.get("source_complete") is True,
                "source_truncated": response.get("source_truncated") is True,
                "rows": response.get("data", {}).get("results", []),
                "read_count": budget.business_get_count, "captured_at": now(), "used_product_query_chain": False}
        if role == "sample":
            if len(body["rows"]) > 100:
                body.update(source_complete=False, source_truncated=True)
            body["rows"] = body["rows"][:100]
        artifact = f"evidence/{task['round']}/{case_id}/{role}.json"
        write_json(safe_path(store.task_dir(task_id), artifact), body)
        receipt.update(status="completed", artifact=artifact, artifact_digest=digest(body),
                       source_complete=body["source_complete"], row_count=len(body["rows"]), completed_at=now())
    except Exception as error:
        code = getattr(error, "code", "sap_read_unavailable")
        receipt.update(status="cancelled" if code == "task_cancelled" else "blocked", code=code, completed_at=now())
    finally:
        budget.closed = True
    with store.transaction() as db:
        store.put("sap_receipt", receipt_id, receipt, db)
        store.event(task_id, "sap_read_finished", {"receipt_id": receipt_id, "status": receipt["status"]}, db)
    return receipt


def verify_observation(store, task, case, observed_path):
    from .comparison import compare
    prefix = f"evidence/{task['round']}/{case['id']}/execution/"
    if not observed_path.startswith(prefix):
        raise TestAgentError("observation_outside_case_execution")
    bodies = {}
    for role in ("baseline", "anchor"):
        receipt = store.get("sap_receipt", f"{task['id']}.{task['round']}.{case['id']}.{role}")
        if receipt["status"] != "completed":
            raise TestAgentError("live_read_receipt_incomplete")
        body = read_json(safe_path(store.task_dir(task["id"]), receipt["artifact"]))
        if digest(body) != receipt["artifact_digest"]:
            raise TestAgentError("live_evidence_digest_mismatch")
        bodies[role] = body
    observed = read_json(safe_path(store.task_dir(task["id"]), observed_path))
    from datetime import datetime
    try:
        before = datetime.fromisoformat(bodies["baseline"]["captured_at"])
        captured = datetime.fromisoformat(observed["captured_at"].replace("Z", "+00:00"))
        after = datetime.fromisoformat(bodies["anchor"]["captured_at"])
        if not before <= captured <= after:
            raise TestAgentError("source_anchor_order_invalid")
    except (KeyError, ValueError, TypeError) as error:
        if isinstance(error, TestAgentError):
            raise
        raise TestAgentError("source_anchor_order_invalid") from None
    return compare(bodies["baseline"], observed, case["comparison"], bodies["anchor"])
