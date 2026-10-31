from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .common import TestAgentError, atomic_bytes, digest, read_json, safe_path, write_json
from .state import claim, record, task_plan


ASSETS = Path(__file__).parent / "assets"


def generate_scripts(store, plan):
    root = store.root / "suites" / plan["id"]
    atomic_bytes(root / "browser.mjs", (ASSETS / "browser.mjs").read_bytes())
    for case in plan["cases"]:
        entry = ("# Generated deterministic entrypoint; Codex runs in the host conversation.\n"
                 "import sys\nfrom sap_business_agents_test_agent.cli import main\n"
                 "raise SystemExit(main([" + repr("--repository") + ", " + repr(str(store.repository)) + ", "
                 + repr("run-case") + ", " + repr("--task") + ", sys.argv[1], "
                 + repr("--case") + ", " + repr(case["id"]) + "]))\n")
        atomic_bytes(root / (case["id"] + ".py"), entry.encode("utf-8"))
        if case["kind"] == "browser":
            entry_js = "import {run} from './browser.mjs';\nawait run(process.argv[2], " + json.dumps(case["id"]) + ");\n"
            atomic_bytes(root / (case["id"] + ".spec.mjs"), entry_js.encode("utf-8"))
    write_json(root / "scripts.json", {"plan_id": plan["id"], "plan_digest": plan["digest"],
               "scripts": {c["id"]: c["id"] + ".py" for c in plan["cases"]}})


def python_for(store):
    candidate = store.repository / ".venv" / "Scripts" / "python.exe"
    return str(candidate if candidate.exists() else Path(sys.executable))


def _command(store, task, case, evidence_root):
    source = store.task_dir(task["id"]) / "source"
    env = dict(os.environ)
    # Test child processes use isolated state, not the user's active product state.
    env.update(PYTHONPATH=os.pathsep.join([str(source / "src"), str(source)]),
               SAPBA_DATA_ROOT=str(source / ".local-data"), SAPBA_DRAFT_ROOT=str(source / ".prototype/authoring"))
    if case["kind"] in {"pytest", "node-test"}:
        refs = case.get("test_refs", [])
        if not refs:
            raise TestAgentError("test_refs_required")
        for ref in refs:
            filename = ref.split("::", 1)[0]
            if not filename.startswith(("tests/", "site/tests/", "agents/")) or not safe_path(source, filename).is_file():
                raise TestAgentError("test_reference_invalid")
        if case["kind"] == "pytest":
            command = [python_for(store), "-m", "pytest", "-q", *refs]
        else:
            node = shutil.which("node")
            if not node:
                raise TestAgentError("node_unavailable")
            command = [node, "--test", *refs]
        # Offline regressions must not inherit SAP secrets; fake Providers remain usable.
        for key in list(env):
            if key.upper().startswith(("SAP_", "SAPBA_SAP", "SAP_ADT_")):
                env.pop(key)
        env["SAPBA_SAP_ENV_FILE"] = str(source / ".local-data" / "no-sap-credentials.env")
        return command, source, env
    if case["kind"] != "browser":
        raise TestAgentError("native_execution_required")
    binding = task.get("target")
    if not binding:
        raise TestAgentError("isolated_target_not_bound")
    if binding.get("round") != task["round"] or binding.get("source_digest") != task["round_source_digest"]:
        raise TestAgentError("isolated_target_stale")
    actions = case["actions"]
    sample = {}
    if case.get("sample"):
        receipt = store.get("sap_receipt", f"{task['id']}.{task['round']}.{case['id']}.sample")
        if receipt["status"] != "completed":
            raise TestAgentError("sample_not_ready")
        rows = read_json(safe_path(store.task_dir(task["id"]), receipt["artifact"]))["rows"]
        if not rows:
            raise TestAgentError("sample_not_found")
        sample = rows[0]
    if case["mode"] == "live_sap":
        receipt = store.get("sap_receipt", f"{task['id']}.{task['round']}.{case['id']}.baseline")
        if receipt["status"] != "completed":
            raise TestAgentError("independent_baseline_not_ready")
    from .sap import bind_sample
    browser_case = {**case, "actions": bind_sample(actions, sample)}
    configuration = {"case": browser_case, "target": binding, "output": str(evidence_root)}
    configuration_path = evidence_root / "browser-input.json"
    write_json(configuration_path, configuration)
    node = shutil.which("node")
    if not node:
        raise TestAgentError("node_unavailable")
    script = store.root / "suites" / task["plan_id"] / (case["id"] + ".spec.mjs")
    return [node, str(script), str(configuration_path)], source, env


def run_case(store, task_id, case_id):
    owned = claim(store, task_id, case_id)
    if owned.get("already_recorded"):
        return owned
    task = store.get("task", task_id)
    case, token = owned["case"], owned["ticket"]["token"]
    relative = f"evidence/{task['round']}/{case_id}/execution"
    output = safe_path(store.task_dir(task_id), relative)
    output.mkdir(parents=True, exist_ok=True)
    if owned["blocked_dependencies"]:
        write_json(output / "dependencies.json", {"dependencies": owned["blocked_dependencies"]})
        return record(store, task_id, case_id, token, {"status": "blocked", "code": "dependency_not_passed",
                      "evidence": [relative + "/dependencies.json"]})
    if case["kind"] == "native":
        write_json(output / "native-instructions.json", {"case": case, "ticket": owned["ticket"],
                   "source": task["workspace"]["source"], "target": task.get("target"),
                   "evidence_directory": str(output)})
        return {"status": "awaiting_native_execution", "ticket": owned["ticket"], "case": case,
                "evidence_directory": str(output), "instructions": "Use the current Codex browser/computer/MCP tools, then finish-case with evidence."}
    try:
        command, cwd, env = _command(store, task, case, output)
        timeout = min(max(int(case.get("timeout_seconds", 600)), 1), 3600)
        with (output / "process.log").open("wb") as log:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
            write_json(output / "owned-process.json", {"pid": process.pid, "parent_pid": os.getpid(), "token": token})
            started, cancelled, timed_out = time.monotonic(), False, False
            while process.poll() is None:
                cancelled = store.get("task", task_id)["cancel_requested"]
                timed_out = time.monotonic() - started > timeout
                if cancelled or timed_out:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
                    break
                time.sleep(0.15)
        result = {"status": "cancelled" if cancelled else "failed" if timed_out or process.returncode else "passed",
                  "code": "task_cancelled" if cancelled else "case_timeout" if timed_out else "assertion_failed" if process.returncode else "checks_passed",
                  "layer": case["kind"], "exit_code": process.returncode,
                  "evidence": [relative + "/process.log"]}
        if case["kind"] == "browser":
            browser_result = output / "result.json"
            if browser_result.is_file():
                browser = read_json(browser_result)
                result.update(status=browser["status"], code=browser["code"])
                result["evidence"].extend(relative + "/" + name for name in browser.get("evidence", []))
                result["evidence"].append(relative + "/result.json")
            elif not process.returncode:
                result.update(status="blocked", code="browser_result_missing")
        write_json(output / "execution.json", {**result, "command": command, "cwd": str(cwd)})
        result["evidence"].append(relative + "/execution.json")
    except (TestAgentError, OSError) as error:
        result = {"status": "blocked", "code": getattr(error, "code", "test_executor_unavailable"),
                  "evidence": [relative + "/executor-error.json"]}
        write_json(output / "executor-error.json", {"code": result["code"]})
    if case["mode"] == "live_sap" and result["status"] == "passed":
        # Native Codex obtains a fresh anchor after browser completion, then finishes the claim.
        write_json(output / "pending-result.json", result)
        return {"status": "awaiting_source_anchor", "ticket": owned["ticket"], "result": result,
                "result_path": str(output / "pending-result.json"), "observation": relative + "/observation.json"}
    return record(store, task_id, case_id, token, result)


def finish_case(store, task_id, case_id, token, result):
    task = store.get("task", task_id)
    case = next(c for c in task_plan(store, task)["cases"] if c["id"] == case_id)
    if result.get("status") == "passed" and case["mode"] == "live_sap":
        from .sap import verify_observation
        observation = result.get("observation")
        if not observation:
            raise TestAgentError("observed_business_rows_required")
        compared = verify_observation(store, task, case, observation)
        result = {**result, **compared, "comparison_verified": True}
        result["evidence"] = list(dict.fromkeys([*result.get("evidence", []), observation]))
    return record(store, task_id, case_id, token, result)
