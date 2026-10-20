"""Opt-in continuation of four SAP read-only WorkBuddy validation operations.

Keeps the completed non-SAP batch immutable. No model/authentication probes,
Runtime enablement, publication or business algorithm changes.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform.acceptance import agent_execution_digest
from sap_business_agents_platform.workflows import agent_digest
from sap_business_agents_platform.workbuddy_environment import atomic_json, file_hash, digest
from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
from workbuddy_live_cases import live_guard
from workbuddy_validation_auth import verified_account, seed_isolated_authentication

ROOT = Path(__file__).resolve().parents[1]
OPERATIONS = {"free_query", "sample_discovery", "acceptance_baseline", "acceptance_free_query"}
COMMANDS = {"limited_sample", "complete_unique_key", "sample", "acceptance"}


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load(ROOT / "scripts/verify-workbuddy-operations.py", "wb_sap_parent_runner")


def prerequisites(manager, required):
    admission = manager.snapshot()["operation_capabilities"]
    for op in required:
        modes = {"bounded", "trusted_local"} if op == "review_agent_feedback" else (
            {"trusted_local"} if op == "workflow_authoring.v2" else {"bounded"})
        assert admission[op]["status"] == "validated" and modes.issubset(admission[op].get("permission_modes", [])), "verification_prerequisites_not_ready"


def source_identity():
    agents = AgentRepository(ROOT / "agents")
    manifest, package = agents.get("mm-po-gr-status"), agents.package("mm-po-gr-status")
    return {"agent_id": manifest["slug"], "version": manifest["version"],
            "agent_digest": agent_digest(manifest),
            "execution_digest": agent_execution_digest(manifest, package["rules_source"])}


def prepare(manager, batch, parent):
    assert not batch.exists(), "verification_new_batch_required"
    runner.require_idle(manager)
    runner.check_frozen(manager, parent)
    prerequisites(manager, runner.NON_SAP)
    previous = json.loads((parent / "runtime-snapshot.json").read_text(encoding="utf-8"))
    assert live_guard.binding_identity(live_guard.current_binding(manager, previous["model"])) == live_guard.binding_identity(previous), "verification_parent_binding_changed"
    previous_freeze = json.loads((parent / "freeze.json").read_text(encoding="utf-8"))
    assert previous_freeze["scope"] == "china_non_sap", "verification_parent_scope_invalid"
    result = json.loads((parent / "result-summary.json").read_text(encoding="utf-8"))
    validated = result["valid_operations"] if "valid_operations" in result else result["qualification"]["validated"]
    assert result["phase"] == "passed" and validated == len(runner.NON_SAP), "verification_parent_not_complete"
    if "valid_operations" in result:
        assert result["model"] == previous["model"] and result["environment_digest"] == previous["environment_digest"], "verification_parent_summary_binding_changed"
    fixtures = {**previous_freeze["fixture_digests"],
                "verify-workbuddy-free-query.py": file_hash(ROOT / "scripts/verify-workbuddy-free-query.py"),
                "workbuddy_validation_auth.py": file_hash(ROOT / "scripts/workbuddy_validation_auth.py"),
                "workbuddy_validation_diagnostics.py": file_hash(ROOT / "scripts/workbuddy_validation_diagnostics.py"),
                Path(__file__).name: file_hash(Path(__file__))}
    queries = {
        "limited_sample": "只读查询采购订单4500001466的抬头。本次仅取样：从SAP最多读取一条抬头样本，读取请求必须显式限制为一条；不是只限制页面展示，也不要先完整读取再显示一条。只显示采购订单号、公司代码和供应商，并如实说明本次有限样本不代表完整来源。不扩展到收货、付款、库存、其它订单或邮件。",
        "complete_unique_key": "只读查询采购订单4500001466的抬头，显示采购订单号、公司代码和供应商。先用本轮实时字段定义确认订单号能唯一识别完整抬头，再按该订单号精确读取全部匹配结果，不主动截断；保留平台读取、分页和时间限制。不扩展到收货、付款、库存、其它订单或邮件。"
    }
    frozen = {**copy.deepcopy(previous_freeze), "scope": "china_sap_readonly",
              "allowed_operations": sorted(OPERATIONS), "fixture_digests": fixtures,
              "parent_batch": str(parent.relative_to(ROOT)),
              "parent_files": {name: file_hash(parent / name) for name in ("freeze.json", "runtime-snapshot.json", "result-summary.json")},
              "source_agent": source_identity(),
              "authentication_account_digest": digest(verified_account(manager, previous["model"])),
              "case_parameters": {
                  "free_query": {case: {"purchase_order": "4500001466", "query": question} for case, question in queries.items()},
                  "sample": {"schedule_line_delivery_date": "2022-11-15", "purchasing_organization": "1010", "purchase_order": "4500001466", "selected_fields": ["plant"]},
                  "acceptance": {"case_count": 1, "schedule_line_delivery_date": "2022-11-15", "purchasing_organization": "1010", "purchase_order": "4500001466"}},
              "automatic_retry": False}
    with manager.environment.maintenance():
        assert live_guard.binding_identity(live_guard.current_binding(manager, previous["model"])) == live_guard.binding_identity(previous), "verification_parent_binding_changed"
        atomic_json(batch / "runtime-snapshot.json", previous)
        atomic_json(batch / "catalog-snapshot.json", json.loads((parent / "catalog-snapshot.json").read_text(encoding="utf-8")))
        atomic_json(batch / "freeze.json", frozen)
    return {"phase": "prepared", "scope": frozen["scope"], "model_calls": 0, "sap_calls": 0, "target_operations": 4}


def check_batch(manager, batch):
    bound = live_guard.check_batch(manager, batch)
    frozen = json.loads((batch / "freeze.json").read_text(encoding="utf-8"))
    assert frozen["scope"] == "china_sap_readonly" and set(frozen["allowed_operations"]) == OPERATIONS, "verification_sap_scope_invalid"
    parent = runner.batch_path(frozen["parent_batch"])
    assert all(file_hash(parent / name) == value for name, value in frozen["parent_files"].items()), "verification_parent_record_changed"
    assert runner.controls(manager) == frozen["controls"], "verification_codex_or_environment_changed"
    assert source_identity() == frozen["source_agent"], "verification_source_agent_changed"
    assert digest(verified_account(manager, bound["model"])) == frozen["authentication_account_digest"], "verification_authentication_proof_changed"
    prerequisites(manager, runner.NON_SAP)
    return bound


def require_passed(batch, command):
    report = json.loads((batch / (command + "-summary.json")).read_text(encoding="utf-8"))
    assert report["phase"] == "passed" and not report["cleanup_pending"], "verification_prerequisites_not_ready"


async def dispatch(manager, command, batch):
    destination = batch / (command + "-summary.json")
    assert not destination.exists(), "verification_case_already_attempted"
    snapshot = check_batch(manager, batch)
    runner.require_idle(manager)
    if command != "limited_sample":
        require_passed(batch, "limited_sample")
    if command in {"sample", "acceptance"}:
        require_passed(batch, "complete_unique_key")
        prerequisites(manager, {"free_query"})
    if command == "acceptance":
        require_passed(batch, "sample")
        prerequisites(manager, {"sample_discovery"})
    expected = {"free_query"} if command in {"limited_sample", "complete_unique_key"} else (
        {"sample_discovery"} if command == "sample" else {"acceptance_baseline", "acceptance_free_query"})
    live_guard.configure(batch, expected, {"bounded"})
    report = {"command": command, "phase": "running", "automatic_retry": False,
              "expected_operations": [] if command == "limited_sample" else sorted(expected)}
    atomic_json(destination, report)
    started = time.monotonic()
    case_path = batch / (command + "-data/report.json")
    try:
        if command in {"limited_sample", "complete_unique_key"}:
            module = load(ROOT / "scripts/verify-workbuddy-free-query.py", "wb_sap_free_case")
            code = await module.verify(SimpleNamespace(case=command, purchase_order="4500001466", batch=batch, data_root=case_path.parent))
            assert code == 0, "verification_free_case_failed"
        else:
            module = runner.load_case(command, batch)
            case_path = module.REPORT
            native_create_app = module.create_app
            def isolated_app(settings):
                seed_isolated_authentication(settings, batch)
                return native_create_app(settings)
            module.create_app = isolated_app
            native_attach = live_guard.attach_validation_monitor
            def guarded_attach(owner, binding, path):
                assert binding == snapshot
                check_batch(owner, batch)
                native_attach(owner, binding, path)
                native = owner.supervisor.run
                register = owner.record_operation_validation
                async def guarded_run(**kwargs):
                    check_batch(owner, batch)
                    handler = kwargs.get("tool_handler")
                    assert handler, "verification_platform_broker_required"
                    async def guarded_tool(name, arguments):
                        check_batch(owner, batch)
                        result = await handler(name, arguments)
                        check_batch(owner, batch)
                        return result
                    kwargs["tool_handler"] = guarded_tool
                    result = await native(**kwargs)
                    check_batch(owner, batch)
                    return result
                def guarded_register(*args, **kwargs):
                    check_batch(owner, batch)
                    return register(*args, **kwargs)
                owner.supervisor.run, owner.record_operation_validation = guarded_run, guarded_register
            live_guard.attach_validation_monitor = guarded_attach
            try:
                await module.main()
            finally:
                live_guard.attach_validation_monitor = native_attach
        saved = json.loads(case_path.read_text(encoding="utf-8"))
        assert saved["phase"] in {"completed", "passed"}, "verification_case_failed"
        assert all(s["phase"] == "passed" for s in saved.get("stages", [])), "verification_case_failed"
        if command in {"limited_sample", "complete_unique_key"}:
            assert saved["qualified"] is (command == "complete_unique_key"), "verification_qualification_mismatch"
        else:
            assert set(saved["qualified_operations"]) == expected, "verification_qualification_mismatch"
        report.update(phase="passed", qualified_operations=[] if command == "limited_sample" else sorted(expected))
    except BaseException as error:
        code = getattr(error, "code", str(error))
        report.update(phase="failed", failure_code=code if isinstance(code, str) and code.replace("_", "").isalnum() and len(code) <= 120 else type(error).__name__)
    finally:
        await manager.supervisor.close()
        report.update(report=str(case_path.relative_to(ROOT)), elapsed_seconds=round(time.monotonic() - started, 2),
                      cleanup_pending=bool(manager.supervisor.pending()), enabled=manager.snapshot()["enabled"])
        try:
            check_batch(manager, batch)
            runner.require_idle(manager)
            report["binding_and_controls_unchanged"] = True
        except Exception as error:
            report.update(phase="failed", binding_failure=str(error) if isinstance(error, AssertionError) else type(error).__name__)
        if case_path.exists():
            saved = json.loads(case_path.read_text(encoding="utf-8"))
            report.update(case_failure_code=saved.get("failure_code"), case_result=saved.get("result"))
        atomic_json(destination, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "check", *sorted(COMMANDS)])
    parser.add_argument("--batch", required=True)
    parser.add_argument("--parent")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--manifest", help="Delegate to the shared case runner")
    args = parser.parse_args()
    if args.execute:
        from runtime_validation_delegate import delegate
        delegated = delegate(ROOT, args.batch, args.manifest)
        if delegated is not None:
            return delegated
    batch = runner.batch_path(args.batch)
    if not args.execute:
        print(json.dumps({"execute": False, "command": args.command, "model_calls": 0, "sap_calls": 0}))
        return 0
    manager = WorkBuddyManager(ROOT)
    if args.command == "prepare":
        if not args.parent:
            parser.error("--parent is required for prepare")
        report = prepare(manager, batch, runner.batch_path(args.parent))
    elif args.command == "check":
        check_batch(manager, batch)
        runner.require_idle(manager)
        report = {"phase": "passed", "model_calls": 0, "sap_calls": 0}
    else:
        report = asyncio.run(dispatch(manager, args.command, batch))
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return int(report["phase"] not in {"prepared", "passed"} or report.get("cleanup_pending", False))


if __name__ == "__main__":
    raise SystemExit(main())
