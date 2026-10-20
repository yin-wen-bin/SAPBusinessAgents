"""One authorized no-SAP equivalent-Schema terminal contrast, not qualification.

Reuse the historical synthetic prompt/site/model/release/budget. Only the wire
Schema encoding changes; the original business Schema validates the terminal.
No account probe, installation, retry, SDK enablement or capability registration.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import time
import uuid
from pathlib import Path

from sap_business_agents_platform.workbuddy_environment import atomic_json, digest, file_hash
from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
from sap_business_agents_platform.workbuddy_diagnostics import safe_progress, safe_native_failure
from workbuddy_schema_contrast import equivalent_union_schema

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_BATCH = ROOT / ".local-data/workbuddy-validation/china-20261012-formatter-new-release"
EXPECTED_RELEASE = "88695af7b74f20c5fef3949898c72e22368952328227cc788b0d87ef2d439096"
SPEC = importlib.util.spec_from_file_location("schema_contrast_baseline", ROOT / "scripts/verify-workbuddy-formatter-terminal.py")
baseline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(baseline)


def payloads():
    from sap_business_agents_platform.workbuddy_harness import native_terminal_instructions
    schema, example = baseline.probe_contract()
    wire = equivalent_union_schema(schema)
    prompt = ("Return the following complete synthetic object through StructuredOutput, then finish. No tools other than that local formatter. "
        "Keep both clarification fields as literal JSON null, not strings. Do not add SAP facts, plans, evidence or a presentation.\n"
        + json.dumps(example, ensure_ascii=False))
    system = ("Native terminal encoding probe only. No SAP, external systems or native investigation. "
        + native_terminal_instructions(schema))
    return schema, wire, {"system_prompt": system, "prompt": prompt}


def guard_sources():
    paths = (Path(__file__), ROOT / "scripts/workbuddy_schema_contrast.py", ROOT / "scripts/verify-workbuddy-formatter-terminal.py")
    return {path.relative_to(ROOT).as_posix(): file_hash(path) for path in paths}


def original_identity(manager):
    binding = baseline.probe_binding(manager, ORIGINAL_BATCH)
    assert binding["environment_digest"] == EXPECTED_RELEASE, "verification_original_environment_changed"
    report = json.loads((ORIGINAL_BATCH / "terminal-report.json").read_text(encoding="utf-8"))
    assert report["phase"] == "failed" and report["binding"] == binding, "verification_original_case_binding_changed"
    schema, wire, prompts = payloads()
    assert report["schema_sha256"] == digest(schema), "verification_original_contract_changed"
    assert report["budget_seconds"] == baseline.MODEL_CHECK_SECONDS == 90, "verification_original_budget_changed"
    job_path = manager.environment.root / "jobs" / (digest(report["task_id"]) + ".json")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    encoded = prompts["prompt"].encode("utf-8")
    assert job["request_metadata"]["input_sha256"] == hashlib.sha256(encoded).hexdigest(), "verification_original_prompt_changed"
    assert job["request_metadata"]["input_length"] == len(encoded), "verification_original_prompt_changed"
    assert job["cleanup_complete"] and not job["validation_job"], "verification_original_cleanup_missing"
    return binding, schema, wire, prompts, {"report_sha256": file_hash(ORIGINAL_BATCH / "terminal-report.json"),
        "job_sha256": file_hash(job_path), "task_id": report["task_id"], "schema_sha256": digest(schema),
        "prompt_sha256": hashlib.sha256(encoded).hexdigest(), "system_prompt_sha256": digest(prompts["system_prompt"]),
        "budget_seconds": report["budget_seconds"]}


def qualification_state(manager):
    state = manager.environment.state()
    return {key: digest(state.get(key)) for key in ("capabilities", "models", "authenticated", "authentication_revision",
        "authentication_environment", "authentication_site", "default_model_id", "enabled", "current_environment")}


def prepare(manager, batch):
    assert not batch.exists(), "verification_new_batch_required"
    baseline.runner.require_idle(manager)
    binding, schema, wire, prompts, original = original_identity(manager)
    from jsonschema import Draft202012Validator
    _, example = baseline.probe_contract()
    Draft202012Validator(schema).validate(example)
    Draft202012Validator(wire).validate(example)
    release = manager.environment.release()
    request = {"canonical_schema": schema, "wire_schema": wire, **prompts}
    atomic_json(batch / "before.json", {"scope": "no_sap_equivalent_schema_contrast", "binding": binding,
        "controls": baseline.controls(), "sources": guard_sources(), "original_case": original,
        "release_manifest_sha256": file_hash(Path(release["directory"]) / "environment.json"),
        "request_sha256": digest(request), "qualification_state": qualification_state(manager),
        "operation_qualification_granted": False, "model_compatibility_registered": False})
    atomic_json(batch / "request.json", request)
    return {"phase": "prepared", "binding": binding, "canonical_schema_sha256": digest(schema),
        "wire_schema_sha256": digest(wire), "model_calls": 0, "sap_calls": 0}


def check_frozen(manager, batch):
    before = json.loads((batch / "before.json").read_text(encoding="utf-8"))
    assert baseline.controls() == before["controls"], "verification_codex_or_source_changed"
    assert guard_sources() == before["sources"], "verification_fixture_changed"
    binding, schema, wire, prompts, original = original_identity(manager)
    assert binding == before["binding"] and original == before["original_case"], "verification_original_binding_changed"
    assert qualification_state(manager) == before["qualification_state"], "verification_qualification_or_model_changed"
    release = manager.environment.release()
    assert file_hash(Path(release["directory"]) / "environment.json") == before["release_manifest_sha256"], "verification_release_changed"
    request = json.loads((batch / "request.json").read_text(encoding="utf-8"))
    assert digest(request) == before["request_sha256"] and request == {"canonical_schema": schema, "wire_schema": wire, **prompts}, "verification_request_changed"
    return before, request


def safe_code(error):
    code = getattr(error, "code", None) or (str(error) if isinstance(error, AssertionError) else None)
    return code if isinstance(code, str) and code.replace("_", "").isalnum() and len(code) <= 120 else "verification_contrast_failed"


async def terminal(manager, batch):
    destination = batch / "terminal-report.json"
    assert not destination.exists(), "verification_probe_already_attempted"
    baseline.runner.require_idle(manager)
    before, request = check_frozen(manager, batch)
    task = "wb_schema_contrast_" + uuid.uuid4().hex[:16]
    report = {"phase": "running", "task_id": task, "binding": before["binding"], "operation": "model_check",
        "probe_kind": "equivalent_union_schema_terminal", "budget_seconds": before["original_case"]["budget_seconds"],
        "canonical_schema_sha256": digest(request["canonical_schema"]), "wire_schema_sha256": digest(request["wire_schema"]),
        "original_case": before["original_case"], "model_calls": 0, "authentication_calls": 0, "sap_calls": 0,
        "operation_qualification_granted": False, "model_compatibility_registered": False, "automatic_retry": False}
    # Persist the once-only marker before dispatch, including response loss cases.
    atomic_json(destination, report)
    def emit(kind, data):
        safe = safe_progress(kind, data)
        if safe:
            if kind == "agent_runtime_query_submitted":
                report["model_calls"] = 1
            with (batch / "safe-events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"event": kind, **safe}, ensure_ascii=True) + "\n")
            report["last_progress"] = {"event": kind, **safe}
            atomic_json(destination, report)
    started = time.monotonic()
    try:
        seconds = report["budget_seconds"]
        result = await manager.supervisor.run(task_id=task, snapshot=before["binding"], operation="model_check", probe=True,
            mode="bounded", seconds=seconds, tool_handler=None, emit=emit,
            payload={"cwd": str(batch), "output_schema": request["wire_schema"], "timeout_ms": seconds * 1000,
                "system_prompt": request["system_prompt"], "prompt": request["prompt"]})
        check_frozen(manager, batch)
        # Unmodified original checker, not the experimental representation.
        checks = baseline.verify_terminal(result, request["canonical_schema"])
        job = json.loads((manager.environment.root / "jobs" / (digest(task) + ".json")).read_text(encoding="utf-8"))
        assert job["status"] == "completed" and job.get("cleanup_complete") and not job.get("validation_job"), "verification_cleanup_or_terminal_missing"
        report.update(phase="passed", checks={**checks, "canonical_schema_validated": True})
    except Exception as error:
        report.update(phase="failed", failure_code=safe_code(error))
        details = safe_native_failure(getattr(error, "detail", None))
        if details:
            report["failure_diagnostic"] = details
    finally:
        try:
            await manager.supervisor.close()
        except Exception as error:
            report.update(phase="failed", cleanup_failure_code=safe_code(error))
        report.update(elapsed_seconds=round(time.monotonic() - started, 2), cleanup_pending=bool(manager.supervisor.pending()))
        try:
            check_frozen(manager, batch)
            baseline.runner.require_idle(manager)
            report["binding_and_controls_unchanged"] = True
        except Exception as error:
            report.update(phase="failed", binding_failure_code=safe_code(error))
        atomic_json(destination, report)
    return {key: report.get(key) for key in ("phase", "task_id", "elapsed_seconds", "failure_code", "cleanup_pending", "binding_and_controls_unchanged", "checks")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "check", "terminal"])
    parser.add_argument("--batch", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    batch = baseline.runner.batch_path(args.batch)
    if not args.execute:
        print(json.dumps({"phase": "dry_run", "model_calls": 0, "sap_calls": 0}))
        return 0
    manager = WorkBuddyManager(ROOT)
    if args.command == "prepare":
        result = prepare(manager, batch)
    elif args.command == "check":
        before, request = check_frozen(manager, batch)
        baseline.runner.require_idle(manager)
        result = {"phase": "passed", "binding": before["binding"], "model_calls": 0, "sap_calls": 0}
    else:
        result = asyncio.run(terminal(manager, batch))
    print(json.dumps(result, ensure_ascii=False))
    return int(result["phase"] not in {"prepared", "passed"} or result.get("cleanup_pending", False))


if __name__ == "__main__":
    raise SystemExit(main())
