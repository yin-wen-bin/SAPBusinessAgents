"""Build a new immutable release. Never install into or run with platform Python."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def stable(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def install(arguments):
    root = Path(__file__).resolve().parents[1]
    python = Path(arguments.python).resolve(strict=True)
    if (root / ".venv").resolve() in python.parents:
        raise ValueError("workbuddy_platform_interpreter_forbidden")
    if os.name != "nt":
        raise ValueError("workbuddy_windows_installer_required")
    # The pinned release is absent from the current PyPI index, but its
    # historical official wheel is available. Require verified local bytes;
    # never fall back to a newer SDK or repack an existing installation.
    if not arguments.sdk_wheel or not arguments.wheel_sha256:
        raise ValueError("workbuddy_verified_sdk_wheel_required")
    wheel = Path(arguments.sdk_wheel).resolve(strict=True)
    if sha(wheel) != arguments.wheel_sha256 or wheel.suffix != ".whl":
        raise ValueError("workbuddy_wheel_digest_mismatch")
    runtime_root = root / ".local-data/runtimes/workbuddy"
    runtime_root.mkdir(parents=True, exist_ok=True)
    # Serialize installation/switch across API and installer.
    import msvcrt
    with (runtime_root / "maintenance.lock").open("a+b") as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        building = Path(tempfile.mkdtemp(prefix="preparing-", dir=runtime_root))
        try:
            subprocess.run([str(python), "-I", "-m", "venv", str(building)], check=True, timeout=60)
            executable = building / "Scripts/python.exe"
            dependency_lock = root / "config/workbuddy-requirements.lock"
            subprocess.run([str(executable), "-I", "-m", "pip", "install", "--require-hashes", "--only-binary=:all:",
                            "--no-compile", "--disable-pip-version-check", "-r", str(dependency_lock)], check=True, timeout=300)
            sdk = str(wheel)
            subprocess.run([str(executable), "-I", "-m", "pip", "install", "--no-deps", "--only-binary=:all:",
                            "--no-compile", "--disable-pip-version-check", sdk], check=True, timeout=300)
            information = json.loads(subprocess.check_output([str(executable), "-I", "-c",
                "import importlib.metadata as m,json; print(json.dumps({'sdk_version':m.version('codebuddy-agent-sdk'),'dependencies':{n:m.version(n) for n in ('anyio','typing-extensions','idna')}}))"], text=True))
            if information["sdk_version"] != "0.3.247":
                raise ValueError("workbuddy_sdk_version_mismatch")
            cli = building / "Lib/site-packages/codebuddy_agent_sdk/bin/codebuddy-headless.exe"
            if not cli.is_file():
                raise ValueError("workbuddy_bundled_cli_missing")
            # Offline CLI fingerprint only. No auth/model/SAP calls during installation.
            cli_version = subprocess.check_output([str(cli), "--version"], text=True, timeout=15).strip()
            shutil.copy2(root / "src/sap_business_agents_platform/workbuddy_worker.py", building / "worker.py")
            inventory = {file.relative_to(building).as_posix(): sha(file) for file in sorted(building.rglob("*"))
                         if file.is_file()}
            manifest = {"schema_version": "1.0", **information, "cli_version": cli_version,
                        "python": "Scripts/python.exe", "cli": cli.relative_to(building).as_posix(),
                        "worker": "worker.py", "files": inventory,
                        "dependency_lock_digest": sha(dependency_lock), "sdk_wheel_digest": arguments.wheel_sha256,
                        "python_bootstrap_digest": sha(python)}
            key = stable(manifest)
            manifest["environment_digest"] = key
            releases = runtime_root / "releases"
            releases.mkdir(exist_ok=True)
            destination = releases / key
            if destination.exists():
                raise ValueError("workbuddy_release_already_exists")
            (building / "environment.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            building.rename(destination)
            state_path = runtime_root / "state.json"
            state = json.loads(state_path.read_text()) if state_path.exists() else {"enabled": False, "models": {}, "capabilities": {}}
            state.update(environment_digest=key, revision=int(state.get("revision", 0)) + 1,
                         authenticated=False, model_catalog_complete=False,
                         installation={"python": str(python), "python_sha256": sha(python),
                                       "sdk_wheel": str(wheel), "wheel_sha256": arguments.wheel_sha256})
            temporary = state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
            os.replace(temporary, state_path)
            print(json.dumps({"environment_digest": key, "sdk_version": information["sdk_version"], "status": "installed_not_validated"}))
        finally:
            # A failed preparation is retained for diagnostics, not recursively deleted.
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True)
    parser.add_argument("--sdk-wheel")
    parser.add_argument("--wheel-sha256")
    parser.add_argument("--supervised-start", action="store_true")
    arguments = parser.parse_args()
    if arguments.supervised_start and sys.stdin.buffer.read(1) != b"1":
        raise ValueError("workbuddy_supervisor_start_required")
    install(arguments)
