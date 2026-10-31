"""Working-tree snapshots and conflict-checked, recoverable patch application."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .common import TestAgentError, atomic_bytes, digest, now, read_json, safe_path, write_json
from .inventory import source_allowed, source_files


def git(root, *args):
    result = subprocess.run(["git", "-c", "core.longpaths=true", "-c", "core.hooksPath=.git/hooks", *args], cwd=root, capture_output=True, timeout=90)
    if result.returncode:
        raise TestAgentError("snapshot_git_failed", operation=args[0])
    return result.stdout.decode("utf-8").strip()


def snapshot(store, task_id, expected_source_digest):
    directory = store.task_dir(task_id)
    destination = directory / "source"
    files = source_files(store.repository)
    if digest(files) != expected_source_digest:
        raise TestAgentError("source_changed_since_plan")
    if destination.exists():
        raise TestAgentError("snapshot_already_exists")
    destination.mkdir(parents=True)
    total = 0
    try:
        for name, expected in files.items():
            data = safe_path(store.repository, name).read_bytes()
            total += len(data)
            if total > 512 * 1024 * 1024:
                raise TestAgentError("snapshot_too_large")
            if digest(data) != expected:
                raise TestAgentError("source_changed_during_snapshot")
            target = safe_path(destination, name)
            atomic_bytes(target, data)
        if source_files(store.repository) != files:
            raise TestAgentError("source_changed_during_snapshot")
        git(destination, "init", "--template=", "-b", "main")
        git(destination, "config", "core.longpaths", "true")
        git(destination, "config", "user.name", "SAPBA isolated test")
        git(destination, "config", "user.email", "isolated-test@localhost")
        git(destination, "config", "core.autocrlf", "false")
        git(destination, "add", "--all")
        git(destination, "commit", "--allow-empty", "-m", "Isolated current-source test snapshot")
        write_json(directory / "base-files.json", files)
        return {"source": str(destination), "base_digest": digest(files), "commit": git(destination, "rev-parse", "HEAD")}
    except BaseException:
        # Failed snapshots remain for diagnosis; no potentially shared directory is removed.
        write_json(directory / "snapshot-error.json", {"status": "incomplete", "at": now()})
        raise


def changes(store, task_id):
    directory = store.task_dir(task_id)
    base = read_json(directory / "base-files.json")
    source = directory / "source"
    current = source_files(source)
    return {name: {"before": base.get(name), "after": current.get(name)}
            for name in sorted(base.keys() | current.keys()) if base.get(name) != current.get(name)}


def _hash(path):
    return digest(path.read_bytes()) if path.is_file() else None


def apply_patch(store, task_id):
    from .state import application_gate
    with store.transaction() as db:
        task = store.get("task", task_id, db)
        if task.get("application", {}).get("status") == "applied":
            return task["application"]
        application_gate(store, task)
        directory = store.task_dir(task_id)
        journal_path = directory / "application.json"
        if journal_path.exists() and read_json(journal_path)["status"] in {"writing", "prepared", "recovery_required"}:
            raise TestAgentError("application_recovery_required")
        patch = changes(store, task_id)
        if digest(patch) != task.get("verified_patch_digest"):
            raise TestAgentError("patch_changed_after_verification")
        if not patch:
            return {"status": "no_changes"}
        conflicts = []
        for name, hashes in patch.items():
            if not source_allowed(name):
                raise TestAgentError("patch_path_forbidden")
            if _hash(safe_path(store.repository, name)) != hashes["before"]:
                conflicts.append(name)
        if conflicts:
            raise TestAgentError("application_conflict", paths=conflicts)
        journal = {"status": "prepared", "at": now(), "patch": patch, "written": []}
        for name, hashes in patch.items():
            original = safe_path(store.repository, name)
            if hashes["before"] is not None:
                atomic_bytes(safe_path(directory / "backups", name), original.read_bytes())
        write_json(journal_path, journal)
        try:
            journal["status"] = "writing"
            write_json(journal_path, journal)
            for name, hashes in patch.items():
                target = safe_path(store.repository, name)
                if _hash(target) != hashes["before"]:
                    raise TestAgentError("application_conflict", paths=[name])
                if hashes["after"] is None:
                    target.unlink()
                else:
                    data = safe_path(directory / "source", name).read_bytes()
                    if digest(data) != hashes["after"]:
                        raise TestAgentError("patch_changed_after_verification")
                    atomic_bytes(target, data)
                journal["written"].append(name)
                write_json(journal_path, journal)
            journal["status"] = "applied"
            write_json(journal_path, journal)
        except BaseException:
            _rollback(store.repository, directory, journal)
            write_json(journal_path, journal)
            raise
        task["application"] = {"status": "applied", "paths": sorted(patch), "at": now()}
        store.put("task", task_id, task, db)
        store.event(task_id, "patch_applied", task["application"], db)
        return task["application"]


def _rollback(repository, directory, journal):
    conflicts = []
    # Inspect every patch path too: a crash may precede persisting 'written'.
    for name, hashes in reversed(list(journal["patch"].items())):
        target = safe_path(repository, name)
        actual = _hash(target)
        if actual == hashes["before"]:
            continue
        if actual != hashes["after"]:
            conflicts.append(name)
            continue
        if hashes["before"] is None:
            target.unlink()
        else:
            data = safe_path(directory / "backups", name).read_bytes()
            if digest(data) != hashes["before"]:
                raise TestAgentError("application_backup_corrupt")
            atomic_bytes(target, data)
    journal["status"] = "recovery_required" if conflicts else "rolled_back"
    journal["conflicts"] = conflicts


def recover_application(store, task_id):
    with store.transaction() as db:
        task = store.get("task", task_id, db)
        directory = store.task_dir(task_id)
        journal = read_json(directory / "application.json")
        if journal["status"] == "applied":
            if any(_hash(safe_path(store.repository, n)) != h["after"] for n, h in journal["patch"].items()):
                raise TestAgentError("application_conflict")
            task["application"] = {"status": "applied", "paths": sorted(journal["patch"]), "at": now()}
        else:
            _rollback(store.repository, directory, journal)
            task["application"] = {"status": journal["status"], "conflicts": journal["conflicts"]}
        write_json(directory / "application.json", journal)
        store.put("task", task_id, task, db)
        return task["application"]


def dependency_environment(store, task_id):
    """Return exact commands for native Codex to provision the isolated target."""
    source = prepare_target(store, task_id)
    python = store.repository / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        import sys
        python = Path(sys.executable)
    return {"cwd": str(source), "python": str(python), "environment": {
        "PYTHONPATH": str(source / "src"), "SAPBA_DATA_ROOT": str(source / ".local-data"),
        "SAPBA_DRAFT_ROOT": str(source / ".prototype" / "authoring"),
        "SAPBA_SAP_ENV_FILE": str(store.repository / ".env"),
    }, "api_command": [str(python), "-m", "sap_business_agents_platform.cli", "--port", "<free-loopback-port>"],
        "site_install": ["npm", "ci", "--ignore-scripts"], "site_start": ["npm", "run", "dev", "--", "--port", "<free-loopback-port>"],
        "site_cwd": str(source / "site"),
        "notes": ["Bind SAPBA_INTERNAL_API_URL and SAPBA_LOCAL_UI_ORIGINS to the isolated ports.",
                  "Set PUBLIC_SAPBA_API_URL to the isolated API and PUBLIC_SITE_BASE=/ before building the target site.",
                  "Reuse approved runtime authentication/configuration without copying credentials into source or reports.",
                  "Use native tools to start and stop only the processes owned by this test task."]}


def prepare_target(store, task_id):
    """Lifecycle writes go to a disposable target, never the repair candidate."""
    task = store.get("task", task_id)
    directory = store.task_dir(task_id)
    candidate = directory / "source"
    target = directory / "targets" / str(task["round"]) / "source"
    manifest = source_files(candidate)
    if digest(manifest) != task["round_source_digest"]:
        raise TestAgentError("source_changed_mid_round")
    stamp = target.parent / "target-source.json"
    if stamp.exists():
        if read_json(stamp)["source_digest"] != task["round_source_digest"]:
            raise TestAgentError("target_source_changed")
        return target
    if target.exists():
        raise TestAgentError("target_prepare_incomplete")
    target.mkdir(parents=True)
    for name, expected in manifest.items():
        data = safe_path(candidate, name).read_bytes()
        if digest(data) != expected:
            raise TestAgentError("source_changed_during_snapshot")
        atomic_bytes(safe_path(target, name), data)
    git(target, "init", "--template=", "-b", "main")
    git(target, "config", "core.longpaths", "true")
    git(target, "config", "user.name", "SAPBA isolated target")
    git(target, "config", "user.email", "isolated-test@localhost")
    git(target, "config", "core.autocrlf", "false")
    git(target, "add", "--all")
    git(target, "commit", "--allow-empty", "-m", "Disposable product target")
    atomic_bytes(target / ".git/info/exclude", b"__sapba_auto_test_target.json\n")
    write_json(stamp, {"source_digest": task["round_source_digest"], "round": task["round"]})
    return target
