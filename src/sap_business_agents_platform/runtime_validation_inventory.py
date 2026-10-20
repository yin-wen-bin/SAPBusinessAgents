"""Read-only evidence inventory. Does not instantiate a manager or reconcile jobs."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def inventory(root: Path) -> dict:
    root = root.resolve()
    runtime = root / ".local-data/runtimes/workbuddy"
    state_path = runtime / "state.json"
    state = read(state_path) if state_path.exists() else {}
    records, active = [], []
    jobs = {}
    for path in sorted((runtime / "jobs").glob("*.json")):
        try:
            job = read(path)
        except (ValueError, OSError):
            continue
        jobs[job.get("task_id", "")] = (path, job)
        if job.get("status") in {"running", "queued", "cleanup_pending"}:
            active.append({k: job.get(k) for k in ("task_id", "operation", "status", "cleanup_complete", "pid")})
    for key, item in state.get("qualification_records", {}).items():
        path, job = jobs.get(item.get("job_id"), (None, {}))
        binding = item.get("binding", {})
        present = {
            "owned_job": path is not None,
            "completed_cleanup": job.get("status") == "completed" and job.get("cleanup_complete") is True,
            "dependency_manifest": bool(item.get("dependency_manifest")),
            "source_archive": bool(item.get("source_snapshot")),
            "business_report_reference": bool(item.get("checker_report")),
            "case_binding": bool(binding.get("case_id")),
        }
        records.append({"record_id": key, "kind": item.get("kind"), "operation": binding.get("operation"),
            "model": binding.get("model", {}).get("requested_model"), "mode": binding.get("permission_mode"),
            "job_id": item.get("job_id"), "evidence": present,
            "assessment": "audit_candidate" if all(present.values()) else "evidence_missing",
            "missing": [name for name, found in present.items() if not found]})
    legacy = []
    for key, item in state.get("capabilities", {}).items():
        path, job = jobs.get(item.get("job_id"), (None, {}))
        legacy.append({"source_key": key, "status": item.get("status"), "job_id": item.get("job_id"),
            "owned_job": path is not None, "cleanup_complete": job.get("cleanup_complete"),
            "model": job.get("runtime_model"), "assessment": "legacy_provenance_review_required"})
    database = root / ".local-data/platform.sqlite3"
    platform_active = []
    if database.exists():
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
            for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                if not table.replace("_", "").isalnum():
                    continue
                columns = {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
                if "status" not in columns:
                    continue
                values = db.execute(f'SELECT status, count(*) FROM "{table}" WHERE status IN '
                    "('running','queued','planning','executing','starting','cancelling','cleanup_pending','preparing') GROUP BY status").fetchall()
                platform_active.extend({"table": table, "status": status, "count": count} for status, count in values)
    controls = {}
    for relative in ("pyproject.toml", "config/sdks.json", ".local-data/sdk-runtimes/default.json",
            ".local-data/sdk-runtimes/authentication.json", ".local-data/runtimes/workbuddy/state.json",
            "src/sap_business_agents_platform/codex_driver.py", "src/sap_business_agents_platform/codex_planner.py",
            "src/sap_business_agents_platform/runtime_policy.py"):
        path = root / relative
        if path.is_file():
            controls[relative] = sha(path)
    source_files = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root).decode().split("\0")
    sources = {name: sha(root / name) for name in source_files if name and (root / name).is_file()}
    return {"format": "sapba.validation.inventory/1", "read_only": True, "model_calls": 0, "sap_calls": 0,
        "qualification_writes": 0, "enabled": state.get("enabled", False), "default_model": state.get("default_model_id"),
        "environment_digest": state.get("environment_digest"), "controls": controls,
        "source_files": sources, "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip(),
        "dependencies": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
        "records": records, "legacy": legacy, "record_counts": dict(Counter(r["kind"] for r in records)),
        "active_jobs": active, "active_platform_records": platform_active,
        "process_inventory": "not_asserted; records alone do not prove process cleanup"}
