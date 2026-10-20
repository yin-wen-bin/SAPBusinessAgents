"""Explicit once-only original-case validation, isolated; never enables/publishes.

freeze reuses current checks without model calls. run calls the real shared
sample service exactly once for the requested Provider. No Campaign is created.
"""
import argparse
import asyncio
import importlib.util
import json
import shutil
import sys
import time
import uuid
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import uvicorn

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform.models import RunCreate, RunMode, RunStatus
from sap_business_agents_platform.runtime import CodexRuntimeProbe, WorkBuddyRuntimeProbe
from sap_business_agents_platform.runtime_policy import orchestration_digest
from sap_business_agents_platform.sample_discovery import SAMPLE_MODEL
from sap_business_agents_platform.sdk_manager import SDKManager
from sap_business_agents_platform.workbuddy_environment import atomic_json, digest
from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
from workbuddy_live_cases.live_guard import current_binding, binding_identity

ROOT = Path(__file__).resolve().parents[1]
PARAMETERS = {"purchase_order": "4500001466", "schedule_line_delivery_date": "2022-11-15",
              "purchasing_organization": "1010"}


def control_module():
    spec = importlib.util.spec_from_file_location("shared_sample_controls", ROOT / "scripts/verify-workbuddy-operations.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def codex_binding():
    settings = Settings.from_env(ROOT)
    manager = SDKManager(ROOT / "config/sdks.json", ROOT, selection_path=settings.sdk_runtime_state_path,
        runtime_probes={"codex": CodexRuntimeProbe(), "workbuddy": WorkBuddyRuntimeProbe(ROOT)})
    return manager.runtime_snapshot_for_model("codex", SAMPLE_MODEL)


def freeze(batch):
    assert not batch.exists(), "verification_new_batch_required"
    manager = WorkBuddyManager(ROOT)
    module = control_module()
    module.require_idle(manager)
    value = {"controls": module.controls(manager), "parameters": PARAMETERS, "selected_fields": ["plant"],
             "operation_digest": orchestration_digest("sample_discovery"), "scope": "targeted_sample_only",
             "manifest": AgentRepository(ROOT / "agents").get("mm-po-gr-status"), "bindings": {}, "blocked": {}}
    value["manifest_digest"] = digest(value["manifest"])
    # A blocked Provider is not probed, replaced or silently granted eligibility.
    for provider, get in (("codex", codex_binding), ("workbuddy", lambda: current_binding(manager, "deepseek-v4.1-flash"))):
        try:
            value["bindings"][provider] = get()
        except Exception as exc:
            value["blocked"][provider] = getattr(exc, "code", "verification_binding_unavailable")
    atomic_json(batch / "freeze.json", value)
    print(json.dumps({"stage": "frozen", "bound_providers": list(value["bindings"]), "blocked": value["blocked"],
                      "model_calls": 0, "sap_calls": 0}), flush=True)


def checked(batch, provider):
    value = json.loads((batch / "freeze.json").read_text(encoding="utf-8"))
    assert provider in value["bindings"], "verification_provider_binding_blocked"
    assert orchestration_digest("sample_discovery") == value["operation_digest"], "verification_orchestration_changed"
    manager = WorkBuddyManager(ROOT)
    assert control_module().controls(manager) == value["controls"], "verification_codex_or_environment_changed"
    assert digest(AgentRepository(ROOT / "agents").get("mm-po-gr-status")) == value["manifest_digest"]
    current = codex_binding() if provider == "codex" else current_binding(manager, "deepseek-v4.1-flash")
    assert binding_identity(current) == binding_identity(value["bindings"][provider]), "verification_binding_changed"
    return value


async def run(batch, provider, port):
    value = checked(batch, provider)
    report_path = batch / (provider + "-report.json")
    assert not report_path.exists(), "verification_case_already_attempted"
    manager = WorkBuddyManager(ROOT)
    control_module().require_idle(manager)
    settings = Settings.from_env(ROOT)
    target = batch / provider
    target.mkdir()
    (target / "sdk-runtimes").mkdir()
    # Only isolated copies; source account/configuration files are never edited.
    for name in ("default.json", "authentication.json"):
        shutil.copy2(settings.data_root / "sdk-runtimes" / name, target / "sdk-runtimes" / name)
    isolated = replace(settings, data_root=target, draft_root=target / "drafts", internal_api_url=f"http://127.0.0.1:{port}")
    app = create_app(isolated)
    manager = app.state.sdk_manager.workbuddy
    store, broker = app.state.store, app.state.harness_broker
    snapshot = value["bindings"][provider]
    run_id = "scope_" + provider + "_" + uuid.uuid4().hex[:16]
    store.create_run(run_id, RunCreate(mode=RunMode.free_query, query="Original-case bounded sample discovery",
        input=PARAMETERS), runtime=snapshot, query_origin="system")
    report = {"phase": "starting", "run_id": run_id, "provider": provider, "automatic_retry": False,
              "operation_digest": value["operation_digest"], "model": snapshot["model"], "jobs": []}
    atomic_json(report_path, report)
    original = manager.supervisor.run
    async def observed(**kwargs):
        checked(batch, provider)
        assert kwargs["operation"] == "sample_discovery" and kwargs["snapshot"] == snapshot
        report["jobs"].append(kwargs["task_id"])
        result = await original(**kwargs)
        assert result.get("actual_model") == snapshot["actual_model"] and result.get("model_identity_source") == "assistant_message"
        return result
    manager.supervisor.run = observed
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False))
    serving = asyncio.create_task(server.serve())
    start = time.monotonic()
    try:
        async with asyncio.timeout(60):
            while not server.started:
                if serving.done(): serving.result(); raise ValueError("verification_isolated_start_failed")
                await asyncio.sleep(.1)
        store.update_run(run_id, status=RunStatus.running)
        report["phase"] = "running"; atomic_json(report_path, report)
        print(json.dumps({"stage": "started", "provider": provider, "run_id": run_id}), flush=True)
        with manager.supervisor.verification(snapshot, {"sample_discovery"}) if provider == "workbuddy" else nullcontext():
            result = await app.state.agent_sample_discovery.service.discover(run_id, value["manifest"], PARAMETERS,
                revision=1, selected_fields=["plant"])
        atomic_json(target / "sample-result.json", result)
        checked(batch, provider)
        proof = result.get("field_sources", {}).get("plant", {}).get("scope_proof")
        assert result["status"] == "ready" and proof, "verification_sample_not_ready"
        assert proof["verified_inputs"] == sorted(PARAMETERS) and len(proof["sources"]) == 3 and len(proof["relations"]) == 2
        assert all(result["input"][k] == v for k, v in PARAMETERS.items())
        assert result["input"]["plant"] and result["source_complete"] is None
        assert set(result["field_sources"]) == {"plant"}
        state = store.get_harness_state(run_id).get("sample_execution", {})
        assert 0 < state.get("business_get_count", 0) <= 10
        assert not store.get_harness_state(run_id).get("cleanup_incomplete")
        for job_id in report["jobs"]:
            job = json.loads((manager.environment.root / "jobs" / (digest(job_id) + ".json")).read_text(encoding="utf-8"))
            assert job["status"] == "completed" and job["cleanup_complete"] and job["validation_job"]
        report.update(phase="passed", proof_digest=proof["proof_digest"], condition_count=len(proof["verified_inputs"]),
            verified_node_count=len(proof["sources"]), sample_value_digest=digest(result["input"]["plant"]),
            selection_method=result.get("selection_method"), process_cleanup_complete=True)
        if provider == "workbuddy":
            assert report["jobs"]
            manager.record_operation_validation(snapshot, "sample_discovery", job_id=report["jobs"][-1],
                permission_modes=["bounded"], evidence={"kind": "live_validation", "platform_contract_passed": True,
                    "checks": {"full_scope_proven": True, "source_complete_claimed": False, "proof_digest": proof["proof_digest"]},
                    "live_readonly_broker": True})
    except Exception as exc:
        report.update(phase="failed", failure_code=getattr(exc, "code", "verification_sample_failed"))
    finally:
        report.update(elapsed_seconds=round(time.monotonic()-start, 2),
            sample_execution=store.get_harness_state(run_id).get("sample_execution"),
            tool_calls=[{"tool": c["tool_name"], "status": c["status"], "code": (c.get("output") or {}).get("code")}
                        for c in store.list_harness_tool_calls(run_id)])
        store.update_run(run_id, status=RunStatus.completed if report["phase"] == "passed" else RunStatus.inconclusive)
        server.should_exit = True
        await serving
        atomic_json(report_path, report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return report["phase"] == "passed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["freeze", "codex", "workbuddy"])
    parser.add_argument("--batch", required=True)
    parser.add_argument("--port", type=int, default=8777)
    args = parser.parse_args()
    batch = Path(args.batch).resolve()
    assert batch.is_relative_to(ROOT / ".local-data/workbuddy-validation") and batch != ROOT / ".local-data/workbuddy-validation"
    if args.command == "freeze": freeze(batch)
    else: raise SystemExit(0 if asyncio.run(run(batch, args.command, args.port)) else 1)
