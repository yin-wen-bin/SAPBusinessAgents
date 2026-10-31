"""Task-bound MCP SAP reads through the existing registered GET-only Provider."""
from __future__ import annotations

import asyncio
import copy
import time

from .common import TestAgentError, digest, now, read_json, safe_path, write_json
from .state import task_plan


def provider_for(repository):
    from sap_business_agents_platform.config import Settings
    from sap_business_agents_platform.sap_read import EmbeddedODataProvider
    settings = Settings.from_env(repository)
    # Credentials are owned by Settings/Provider, never accepted as tool arguments.
    return EmbeddedODataProvider(base_url=settings.sap_base_url, username=settings.sap_username,
        password=settings.sap_password, client=settings.sap_client, verify_ssl=settings.sap_verify_ssl,
        auth_type=settings.sap_auth_type, timeout_seconds=settings.sap_odata_timeout_seconds,
        max_results=min(settings.sap_max_results, 5000), page_size=min(settings.sap_page_size, 100),
        max_concurrent_requests=1, relationship_catalog_path=repository / "config/business-relationships.json",
        service_registry_path=settings.odata_service_registry_path, catalog_seed_path=settings.catalog_seed_path,
        curated_catalog_path=repository / "config/catalog-curated-terms.json",
        normalization_catalog_path=repository / "config/sap-value-normalization.json")


class ReadBudget:
    def __init__(self):
        self.closed, self.read_count, self.max_reads, self.business_get_count = False, 1, 10, 0
        self.started = time.monotonic()

    def remaining(self, external=False):
        return max(0, (540 if external else 600) - (time.monotonic() - self.started))


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


async def read(store, task_id, case_id, role, provider=None):
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
        if not recipe or not isinstance(recipe.get("plan"), dict):
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
        query_plan = bind_sample(copy.deepcopy(recipe["plan"]), sample)
        # Raw URLs, SQL and credentials are excluded by the existing strict plan schema.
        from sap_business_agents_platform.runtime_query_contract import plans
        plans(query_plan)
        steps = query_plan.get("steps") or [query_plan]
        if role == "sample" and (len(steps) != 1 or not 1 <= steps[0].get("top", 100) <= 100):
            raise TestAgentError("sample_budget_invalid")
        for step in steps:
            if step.get("plan_kind", "direct") not in {"direct", "lookup", "multi_step"}:
                raise TestAgentError("sap_function_not_allowed")
            if step.get("top", 100) is None or not 1 <= step.get("top", 100) <= 5000:
                raise TestAgentError("sap_row_budget_invalid")
        receipt = {"id": receipt_id, "task_id": task_id, "case_id": case_id, "round": task["round"],
                   "role": role, "status": "running", "query_digest": digest(query_plan), "started_at": now()}
        store.put("sap_receipt", receipt_id, receipt, db)
        store.event(task_id, "sap_read_started", {"receipt_id": receipt_id, "role": role}, db)
    budget = ReadBudget()
    try:
        from sap_business_agents_platform import sample_read_budget
        provider = provider or provider_for(store.repository)
        health = await provider.health()
        if health.get("ok") is not True:
            raise TestAgentError("sap_configuration_unavailable")
        with sample_read_budget.bind(budget):
            response = await asyncio.wait_for(provider.execute_plan(query_plan, query=""), timeout=540)
        if response.get("ok") is not True:
            raise TestAgentError("sap_read_rejected")
        body = {"schema_version": 1, "task_id": task_id, "case_id": case_id, "round": task["round"],
                "role": role, "provider_id": response.get("provider_id"), "method": "GET",
                "query_digest": digest(query_plan), "scope": query_plan,
                "source_complete": response.get("source_complete") is True,
                "source_truncated": response.get("source_truncated") is True,
                "rows": response.get("data", {}).get("results", []),
                "read_count": budget.business_get_count, "captured_at": now(), "used_product_query_chain": False}
        if role == "sample":
            body["rows"] = body["rows"][:100]
        artifact = f"evidence/{task['round']}/{case_id}/{role}.json"
        write_json(safe_path(store.task_dir(task_id), artifact), body)
        receipt.update(status="completed", artifact=artifact, artifact_digest=digest(body),
                       source_complete=body["source_complete"], row_count=len(body["rows"]), completed_at=now())
    except Exception as error:
        code = getattr(error, "code", "sap_read_unavailable")
        receipt.update(status="blocked", code=code, completed_at=now())
    finally:
        budget.closed = True
    with store.transaction() as db:
        store.put("sap_receipt", receipt_id, receipt, db)
        store.event(task_id, "sap_read_finished", {"receipt_id": receipt_id, "status": receipt["status"]}, db)
    return receipt


def verify_observation(store, task, case, observed_path):
    from .comparison import compare
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
