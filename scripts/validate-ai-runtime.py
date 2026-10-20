"""Private validation CLI. Inspection is read-only; run/apply require --execute.

No command logs in, selects a default model, activates a protocol or probes a
model implicitly. A frozen manifest is required for live execution.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from contextlib import ExitStack

from sap_business_agents_platform.runtime_validation import Authorization, Registry, ValidationError, plan, run
from sap_business_agents_platform.runtime_validation_inventory import inventory
from sap_business_agents_platform.runtime_validation_store import BatchStore, atomic, capture, process_binding, read, verify_archive, locked
from sap_business_agents_platform.runtime_validation_freeze import prepare, verify_manifest


def safe_output(root, path):
    path = Path(path).resolve()
    allowed = (root / ".local-data/runtime-validation-audit").resolve()
    if path == allowed or not path.is_relative_to(allowed):
        raise ValidationError("validation_report_path_invalid")
    return path


def finish_owned_process(job, child, *, seconds=10):
    """Confirm the entire owned tree, even after its main process has exited."""
    deadline = time.monotonic() + seconds
    if not job.empty():
        job.terminate()
    if child is not None and child.poll() is None:
        try:
            child.wait(timeout=max(.001, deadline-time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    while not job.empty() and time.monotonic() < deadline:
        time.sleep(.02)
    return job.empty() and (child is None or child.poll() is not None)


def context(manifest, registry):
    binding = manifest["runtime"]
    grants = {key: Authorization(**value) for key, value in manifest.get("authorizations", {}).items()}
    parameters, targets = manifest.get("parameters", {}), manifest.get("targets", {})
    planned = plan(registry, manifest["selected"], binding, grants, parameters, targets)
    return planned, grants, parameters, targets


def retained_evidence(root, manifest, registry):
    from sap_business_agents_platform.runtime_validation_qualification import active, select
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyEnvironment
    if manifest['runtime'].get('provider_id') != 'workbuddy':
        return {}
    env = WorkBuddyEnvironment(root)
    state = env.state()
    if not active(state):
        return {}
    from sap_business_agents_platform.workbuddy_validation import case_bindings
    current = case_bindings(manifest['runtime'], env.release(manifest['runtime']['environment_digest']), registry)
    result = {}
    for key in registry.closure(manifest['selected']):
        bound = manifest.get('case_bindings', {}).get(key)
        if bound and current.get(key) == bound:
            record = select(env, state, bound, manifest['runtime']['environment_digest'])
            if record:
                result[key] = {**record.get('result', {'status': 'passed', 'case_id': key}),
                    'qualification_record_id': record['id'], 'origin': record['kind']}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run", "report", "audit-reuse", "apply-reuse"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--model")
    parser.add_argument("--provider", default="workbuddy")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--batch", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--freeze", action="store_true", help="Archive the selected dependency/import closure; no execution")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--frozen-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root, registry = args.root.resolve(), Registry()
    manifest = read(args.manifest) if args.manifest else None
    if args.command == "plan":
        if manifest:
            value, _, _, _ = context(manifest, registry)
            retained = retained_evidence(root, manifest, registry)
            for item in value.cases:
                if item['case_id'] in retained:
                    item.update(action='retain', reason=None, origin=retained[item['case_id']]['origin'])
            result = asdict(value)
        else:
            selected = args.case or [c.id for c in registry.entries.values() if c.operations]
            result = asdict(plan(registry, selected, {"provider_id": args.provider, "model": args.model}))
        if args.freeze:
            if not manifest or not args.batch:
                raise ValidationError("validation_freeze_manifest_required")
            batch = safe_output(root, args.batch / "manifest.json").parent
            if (batch / "manifest.json").exists():
                raise ValidationError("validation_batch_already_frozen")
            frozen = prepare(root, batch, manifest)
            result["source_snapshot"] = frozen['source_snapshot']
    elif args.command == "report":
        if not args.batch:
            raise ValidationError("validation_batch_required")
        directory = safe_output(root, args.batch / "report.json").parent
        attempts = [read(p) for p in sorted((directory / "attempts").glob("*.json"))]
        from sap_business_agents_platform.runtime_validation_report import summarize
        result = {"attempts": attempts, "read_only": True,
            'this_batch': summarize(registry, {r['case_id']: BatchStore.existing_result(r) for r in attempts}),
            "note": "Attempt completion is not operation qualification; all required cases/modes must pass."}
        manifest = manifest or (read(directory / 'manifest.json') if (directory / 'manifest.json').exists() else None)
        if manifest and manifest['runtime'].get('provider_id') == 'workbuddy':
            from sap_business_agents_platform.workbuddy_environment import WorkBuddyEnvironment
            from sap_business_agents_platform.runtime_validation_qualification import active, projection
            from sap_business_agents_platform.workbuddy_validation import case_bindings
            env = WorkBuddyEnvironment(root)
            state = env.state()
            result['qualification_protocol_active'] = active(state)
            result['historical_pass_record_ids'] = [key for key, record in state.get('qualification_records', {}).items() if record.get('kind') == 'live_pass']
            if active(state):
                try:
                    release = env.release(manifest['runtime']['environment_digest'])
                    result['current_qualification'] = projection(env, state, release, manifest['runtime'], case_bindings(manifest['runtime'], release), registry)
                except (ValidationError, OSError, ValueError) as exc:
                    result['current_qualification'] = {'status': 'unknown', 'reason': getattr(exc, 'code', 'validation_evidence_unavailable')}
    elif args.command in {"audit-reuse", "apply-reuse"}:
        if not args.receipt:
            if args.command == "apply-reuse":
                raise ValidationError("validation_reuse_receipt_required")
            from sap_business_agents_platform.runtime_validation_report import audit_history
            result = audit_history(root, args.model)
        else:
            from sap_business_agents_platform.runtime_validation_qualification import ReuseReceipt, audit_reuse, ledger
            from sap_business_agents_platform.workbuddy_environment import WorkBuddyEnvironment
            env = WorkBuddyEnvironment(root)
            receipt = ReuseReceipt(**read(args.receipt))
            if args.command == "apply-reuse":
                if not args.execute:
                    raise ValidationError("validation_reuse_authorization_required")
                from sap_business_agents_platform.workbuddy_validation import apply_receipt
                result = {"record_id": apply_receipt(root, receipt)}
            else:
                state = env.state()
                result = audit_reuse(env, state, ledger(state).get(receipt.source_record_id, {}), receipt)
    else:
        if not args.execute or not manifest or not args.batch:
            raise ValidationError("validation_execution_authorization_required")
        batch = safe_output(root, args.batch / "report.json").parent
        archive = manifest["source_snapshot"]
        verify_manifest(manifest)
        verify_archive(archive["directory"], archive["digest"])
        if not args.frozen_child:
            # New interpreter, frozen package first; never reuse already-imported
            # modules from the running API. No new SDK installation is involved.
            source = Path(archive["directory"]) / "source"
            entry = source / "scripts/validate-ai-runtime.py"
            if not entry.is_file():
                raise ValidationError("validation_frozen_entry_missing")
            boot = "import sys,os,runpy;assert sys.stdin.buffer.read(1)==b'1';sys.path[:0]=[sys.argv[1],sys.argv[2],os.path.dirname(sys.argv[2])];p=sys.argv[3];sys.argv=sys.argv[3:];runpy.run_path(p,run_name='__main__')"
            command = [sys.executable, "-I", "-B", "-c", boot, str(source / "src"), str(source / "scripts"), str(entry),
                "run", "--root", str(root), "--manifest", str(args.manifest.resolve()), "--batch", str(batch), "--execute", "--frozen-child"]
            from sap_business_agents_platform.workbuddy_job import WindowsJob, process_identity
            import os
            if os.name != 'nt':
                raise ValidationError('validation_owned_process_platform_unsupported')
            job, child = WindowsJob(), None
            try:
                child = subprocess.Popen(command, cwd=root, stdin=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
                job.attach(child.pid)  # Child cannot import/dispatch before this handshake.
                atomic(batch / 'process.json', {'pid': child.pid, 'start_id': process_identity(child.pid),
                    'interpreter': sys.executable, 'source_digest': archive['digest'], 'status': 'running'})
                child.stdin.write(b'1')
                child.stdin.flush()
                child.stdin.close()
                return child.wait()
            finally:
                try:
                    cleaned = finish_owned_process(job, child)
                    atomic(batch / 'process-cleanup.json', {'cleanup_complete': cleaned,
                        'pid': child.pid if child else None, 'source_digest': archive['digest']})
                finally:
                    job.close()
        process = process_binding(archive)
        planned, grants, parameters, targets = context(manifest, registry)
        retained = retained_evidence(root, manifest, registry)
        for item in planned.cases:
            if item['case_id'] in retained:
                item.update(action='retain', reason=None)
        if manifest["runtime"].get("provider_id") != "workbuddy":
            raise ValidationError("validation_provider_live_adapter_unavailable")
        from runtime_validation_cases import WorkBuddyCases
        adapter = WorkBuddyCases(root, batch, manifest, process)
        with ExitStack() as resources:
            for resource in sorted({r for item in planned.cases for r in registry.entries[item['case_id']].resources}):
                resources.enter_context(locked(root / '.local-data/runtime-validation-locks/workbuddy' / (resource + '.lock')))
            if adapter.manager._qualification_busy():
                raise ValidationError('validation_runtime_not_idle')
            result = asyncio.run(run(planned, registry, BatchStore(batch), adapter, grants, parameters, targets, retained=retained))
        atomic(batch / "report.json", result)
    if args.output:
        atomic(safe_output(root, args.output), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(args.command == 'run' and any(r.get('status') != 'passed' or r.get('qualification_error') for r in result.values()))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValidationError, KeyError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({"error": getattr(exc, "code", "validation_input_or_artifact_invalid")}), file=sys.stderr)
        raise SystemExit(2)
