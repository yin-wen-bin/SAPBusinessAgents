"""Secret-free source snapshots and native Windows isolation for authoring.

No git checkout/worktree metadata is exposed to the candidate. The snapshot is
copied from a pinned commit, never from local uncommitted or ignored material.
"""
from __future__ import annotations

import asyncio
import errno
import json
import io
import os
import re
import socket
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .authoring_harness import AuthoringHarnessError, content_digest

_SOURCE_ROOTS = {"src", "site", "tests", "docs", "schemas"}
_ROOT_FILES = {"README.md", "pyproject.toml", "requirements.txt", "pytest.ini"}
_PROTECTED = {"agent_identity.py", "agent_lifecycle.py", "agent_authoring.py",
              "authoring_workspace.py", "authoring_harness.py", "authoring_changesets.py",
              "runtime_execution.py", "runtime_changesets.py",
              "security.py", "restricted_artifacts.py", "acceptance.py", "database.py",
              "config.py", "app.py", "sdk_manager.py", "tool_gateway.py", "mcp_server.py"}
_PRIVATE_PART = re.compile(r"(^\.env($|\.)|secret|credential|auth\.json|\.sqlite|\.pem$|\.key$)", re.I)
_TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".astro", ".css", ".json", ".md", ".toml", ".txt", ".yaml", ".yml"}
_CACHES = {"__pycache__", ".pytest_cache", ".astro", "dist", "node_modules", ".authoring-tmp", ".venv"}


def safe_relative(value: str) -> Path:
    name = PurePosixPath(value)
    if not value or re.search(r'[<>:"\\|?*\x00-\x1f]', value) or name.is_absolute() or any(p in {"", ".", ".."} for p in value.split("/")):
        raise AuthoringHarnessError("agent_harness_path_invalid")
    for part in name.parts:
        if part.rstrip(" .") != part or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part):
            raise AuthoringHarnessError("agent_harness_path_invalid")
    return Path(*name.parts)


def _allowed_source(value: str) -> bool:
    path = PurePosixPath(value)
    return (path.parts[0] in _SOURCE_ROOTS or value in _ROOT_FILES) and not any(
        _PRIVATE_PART.search(part) or part in {".git", ".local-data", "node_modules", "dist", "versions", "fixtures", ".codex", ".agents"}
        for part in path.parts) and path.suffix in _TEXT_SUFFIXES


class AuthoringWorkspace:
    def __init__(self, repository: Path, root: Path):
        self.repository, self.root = repository.resolve(), root.resolve()
        self.source = self.root / "source"
        self.agent = self.source / "agent-package"
        self.base_files: dict[str, str] = {}
        self.base_commit = ""
        self.read_only_source = False

    def prepare(self, package: dict[str, Any] | None, *, current_source: bool = False) -> None:
        if self.root.exists():
            raise AuthoringHarnessError("agent_harness_workspace_exists")
        self.root.mkdir(parents=True)
        self.source.mkdir()
        self.base_commit = self._git("rev-parse", "HEAD").decode().strip()
        entries = self._git("ls-tree", "-r", "-z", self.base_commit).split(b"\0")
        selected_roots = sorted({entry.split(b"\t", 1)[1].decode("utf-8").split("/")[0]
                                 for entry in entries if entry and _allowed_source(entry.split(b"\t", 1)[1].decode("utf-8"))})
        if not selected_roots:
            raise AuthoringHarnessError("agent_harness_source_unavailable")
        archive_data = self._git("archive", "--format=zip", self.base_commit, "--", *selected_roots)
        archive = zipfile.ZipFile(io.BytesIO(archive_data))
        total = 0
        for entry in entries:
            if not entry:
                continue
            descriptor, raw_name = entry.split(b"\t", 1)
            mode, kind, object_id = descriptor.decode().split()
            name = raw_name.decode("utf-8")
            if not _allowed_source(name):
                continue
            if mode not in {"100644", "100755"} or kind != "blob":
                raise AuthoringHarnessError("agent_harness_source_link_rejected")
            target = self.source / safe_relative(name)
            if archive.getinfo(name).file_size > 2 * 1024 * 1024:
                raise AuthoringHarnessError("agent_harness_source_too_large")
            data = archive.read(name)
            total += len(data)
            if total > 50 * 1024 * 1024 or len(data) > 2 * 1024 * 1024:
                raise AuthoringHarnessError("agent_harness_source_too_large")
            text = data.decode("utf-8")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="")
            self.base_files[name] = text
        if current_source:
            # Snapshot allowed working-tree source, including untracked implementation
            # files. Record it as the base, not as an SDK-generated modification.
            names = self._git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode("utf-8").split("\0")
            live_files: dict[str, str] = {}
            total = 0
            for name in sorted(set(names)):
                if not name or not _allowed_source(name):
                    continue
                source = self.repository / safe_relative(name)
                if not source.exists():
                    continue
                if source.is_symlink() or not source.resolve().is_relative_to(self.repository) or source.stat().st_nlink > 1:
                    raise AuthoringHarnessError("agent_harness_source_link_rejected")
                if source.stat().st_size > 2 * 1024 * 1024:
                    raise AuthoringHarnessError("agent_harness_source_too_large")
                data = source.read_bytes()
                total += len(data)
                if total > 50 * 1024 * 1024:
                    raise AuthoringHarnessError("agent_harness_source_too_large")
                text = data.decode("utf-8")
                target = self.source / safe_relative(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8", newline="")
                live_files[name] = text
            for name in self.base_files.keys() - live_files.keys():
                (self.source / safe_relative(name)).unlink()
            self.base_files = live_files
        self.base_digest = content_digest(self.base_files)
        if package is not None:
            self.write_package(package)

    def _git(self, *args: str) -> bytes:
        result = subprocess.run(["git", *args], cwd=self.repository, capture_output=True, timeout=30)
        if result.returncode:
            raise AuthoringHarnessError("agent_harness_source_unavailable")
        return result.stdout

    def write_package(self, package: dict[str, Any]) -> None:
        if self.agent.exists():
            self._check_links_and_size()
            wanted = {"manifest.json", "README.md"}
            if package.get("rules") is not None:
                wanted.add("rules.py")
            wanted.update("files/" + str(safe_relative(name).as_posix()) for name in (package.get("files") or {}))
            for path in self.agent.rglob("*"):
                if path.is_file() and path.relative_to(self.agent).as_posix() not in wanted:
                    path.unlink()
        self.agent.mkdir(exist_ok=True)
        (self.agent / "manifest.json").write_text(json.dumps(package["manifest"], ensure_ascii=False, indent=2), encoding="utf-8")
        (self.agent / "README.md").write_text(package.get("readme") or "", encoding="utf-8")
        if package.get("rules") is not None:
            (self.agent / "rules.py").write_text(package["rules"], encoding="utf-8")
        for name, content in (package.get("files") or {}).items():
            target = self.agent / "files" / safe_relative(name)
            if not isinstance(content, str):
                raise AuthoringHarnessError("agent_harness_binary_file_unsupported")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    def read_package(self) -> dict[str, Any]:
        self._check_links_and_size()
        files = {}
        folder = self.agent / "files"
        if folder.exists():
            files = {p.relative_to(folder).as_posix(): p.read_text(encoding="utf-8") for p in folder.rglob("*") if p.is_file()}
        return {"manifest": json.loads((self.agent / "manifest.json").read_text(encoding="utf-8")),
                "readme": (self.agent / "README.md").read_text(encoding="utf-8"),
                "rules": (self.agent / "rules.py").read_text(encoding="utf-8") if (self.agent / "rules.py").exists() else None,
                "files": files}

    def _check_links_and_size(self) -> None:
        total = 0
        # Do not follow a directory junction inserted by an untrusted command.
        for current, dirs, files in os.walk(self.source, followlinks=False):
            for name in [*dirs, *files]:
                p = Path(current) / name
                if p.is_symlink() or getattr(p, "is_junction", lambda: False)() or not p.resolve().is_relative_to(self.source):
                    raise AuthoringHarnessError("agent_harness_path_invalid")
                if p.is_file() and p.suffix not in {".pyc", ".pyo"}:
                    stat = p.stat()
                    total += stat.st_size
                    if stat.st_nlink > 1 or stat.st_size > 2 * 1024 * 1024 or total > 50 * 1024 * 1024:
                        raise AuthoringHarnessError("agent_harness_source_too_large")
            dirs[:] = [name for name in dirs if name not in _CACHES]

    def platform_changes(self) -> list[dict[str, Any]]:
        self._check_links_and_size()
        current = {}
        for folder, dirs, files in os.walk(self.source, followlinks=False):
            dirs[:] = [name for name in dirs if name not in _CACHES and Path(folder) / name != self.agent]
            for name in files:
                path = Path(folder) / name
                current[path.relative_to(self.source).as_posix()] = path
        changes = []
        for name in sorted(set(self.base_files) | set(current)):
            before = self.base_files.get(name)
            after = current[name].read_text(encoding="utf-8") if name in current else None
            if before == after:
                continue
            if not _allowed_source(name) or Path(name).name in _PROTECTED:
                raise AuthoringHarnessError("agent_harness_protected_file_changed")
            changes.append({"path": name, "before": before, "after": after})
        return changes


def _toml_literal(value: str) -> str:
    if "'" in value or "\n" in value or "\r" in value:
        raise AuthoringHarnessError("agent_harness_path_invalid")
    return "'" + value + "'"


def sandbox_overrides(workspace: AuthoringWorkspace) -> list[str]:
    if os.name != "nt":
        raise AuthoringHarnessError("agent_harness_platform_unsupported")
    roots = {":root": "deny", ":minimal": "read", str(workspace.repository): "deny", str(Path.home()): "deny",
             str(workspace.root): "deny", str(workspace.source): "write", str(Path(sys.base_prefix).resolve()): "read",
             str(Path(sys.prefix).resolve()): "read"}
    if workspace.read_only_source:
        for name in sorted(_SOURCE_ROOTS | _ROOT_FILES):
            target = workspace.source / name
            if target.exists():
                roots[str(target)] = "read"
    for name in workspace.base_files:
        if not workspace.read_only_source and Path(name).name in _PROTECTED:
            roots[str(workspace.source / safe_relative(name))] = "read"
    filesystem = ",".join(f"{_toml_literal(path)}={_toml_literal(access)}" for path, access in roots.items())
    return ["--permission-profile", "sapba-authoring", "-c", "windows.sandbox='elevated'", "-c",
            "permissions.sapba-authoring={filesystem={" + filesystem + "},network={enabled=false}}"]


def isolated_environment() -> dict[str, str]:
    """Full child environment, not SDK-style overrides merged into the parent.

    Windows needs SystemRoot to load Winsock and enforce native policy correctly.
    Never inherit arbitrary SAP, secret, plugin or interpreter-injection variables.
    """
    allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE",
               "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "PROGRAMFILES",
               "PROGRAMFILES(X86)", "COMMONPROGRAMFILES", "PATHEXT"}
    result = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    result.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    if os.name == "nt":
        system_root = next((value for key, value in result.items() if key.upper() == "SYSTEMROOT"), None)
        if not system_root or not Path(system_root).is_absolute():
            raise AuthoringHarnessError("agent_harness_environment_invalid")
        result["PATH"] = os.pathsep.join([str(Path(sys.prefix) / "Scripts"),
                                          str(Path(sys.base_prefix)),
                                          str(Path(system_root) / "System32"), system_root])
    return result


async def isolated_command(workspace: AuthoringWorkspace, command: list[str], *, timeout: float = 30) -> tuple[int, bytes, bytes]:
    from codex_cli_bin import bundled_codex_path
    args = [str(bundled_codex_path()), "sandbox", *sandbox_overrides(workspace), "-C", str(workspace.source), "--", *command]
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                  stderr=asyncio.subprocess.PIPE, env=isolated_environment())
    async def collect(stream: asyncio.StreamReader) -> bytes:
        data = bytearray()
        while chunk := await stream.read(8192):
            data.extend(chunk)
            if len(data) > 2 * 1024 * 1024:
                raise AuthoringHarnessError("agent_harness_output_limit")
        return bytes(data)
    tasks = [asyncio.create_task(collect(process.stdout)), asyncio.create_task(collect(process.stderr))]
    try:
        out, err = await asyncio.wait_for(asyncio.gather(*tasks), max(.001, timeout))
        await asyncio.wait_for(process.wait(), max(.001, timeout))
        return process.returncode, out, err
    finally:
        if process.returncode is None:
            # Kill only this command's owned process tree; never a shared service.
            killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T", "/F",
                                                         stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(killer.wait(), 10)
            await asyncio.wait_for(process.wait(), 10)
        for task in tasks:
            if not task.done():
                task.cancel()


def _permission_denied(observation: Any) -> bool:
    """Only a real access denial proves a boundary; missing files/timeouts do not."""
    if not isinstance(observation, dict) or set(observation) != {"errno", "winerror"}:
        return False
    winerror = observation["winerror"]
    if winerror is not None:
        return type(winerror) is int and winerror in {5, 10013}
    return type(observation["errno"]) is int and observation["errno"] in {errno.EACCES, errno.EPERM}


async def sandbox_preflight(workspace: AuthoringWorkspace, *, accept_loopback_access: bool = False, command_runner: Any = None) -> dict[str, Any]:
    """Use harmless canaries; do not test isolation by reading a real secret."""
    canary = workspace.root / "controller-canary.txt"
    canary.write_text("isolation-canary", encoding="utf-8")
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        code = f"""
import json,socket
from pathlib import Path
ok={{}}
def error_code(e):
    return {{'errno':e.errno, 'winerror':getattr(e,'winerror',None)}}
p=Path({str(workspace.source / 'probe.tmp')!r})
p.write_text('probe'); ok['workspace_write']=p.read_text()=='probe'; p.unlink()
try: Path({str(canary)!r}).read_text(); ok['outside_read_error']=None
except OSError as e: ok['outside_read_error']=error_code(e)
try: Path({str(canary)!r}).write_text('changed'); ok['outside_write_error']=None
except OSError as e: ok['outside_write_error']=error_code(e)
try: socket.create_connection(('127.0.0.1',{port}),timeout=1).close(); ok['network_error']=None
except OSError as e: ok['network_error']=error_code(e)
print(json.dumps(ok))
"""
        runner = command_runner or isolated_command
        status, out, _ = await runner(workspace, [sys.executable, "-I", "-c", code], timeout=30)
    try:
        result = json.loads(out.decode("utf-8").strip())
    except (ValueError, UnicodeError):
        raise AuthoringHarnessError("agent_harness_sandbox_preflight_failed") from None
    names = {"workspace_write", "outside_read_error", "outside_write_error", "network_error"}
    if not isinstance(result, dict) or set(result) != names:
        raise AuthoringHarnessError("agent_harness_sandbox_preflight_failed")
    checks = {"workspace_write": result["workspace_write"] is True,
              "outside_read_denied": _permission_denied(result["outside_read_error"]),
              "outside_write_denied": _permission_denied(result["outside_write_error"]),
              "network_denied": _permission_denied(result["network_error"])}
    # An explicitly accepted, successful loopback connection is a recorded
    # limitation, never a claimed network isolation PASS. Runtime failures still
    # fail closed, even under the acceptance policy.
    loopback_accepted = accept_loopback_access is True and result["network_error"] is None
    mandatory = (checks["workspace_write"], checks["outside_read_denied"], checks["outside_write_denied"],
                 checks["network_denied"] or loopback_accepted)
    if status or not all(mandatory) or canary.read_text() != "isolation-canary":
        raise AuthoringHarnessError("agent_harness_sandbox_preflight_failed")
    return {"status": "passed_with_limitations" if loopback_accepted else "passed", "checks": checks,
            "limitations": ["loopback_access_accepted"] if loopback_accepted else [],
            "policy_digest": content_digest({"overrides": sandbox_overrides(workspace),
                                              "accept_loopback_access": accept_loopback_access})}
