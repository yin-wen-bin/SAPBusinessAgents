from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class SiteReleaseError(RuntimeError):
    def __init__(self, message: str, *, code: str, phase: str) -> None:
        super().__init__(message)
        self.code = code
        self.phase = phase


class SiteReleaseManager:
    """Prepare immutable UI builds and switch only the Web UI process.

    Building is deliberately separate from switching: a validation or build
    failure cannot stop the currently healthy API or Web UI.
    """

    def __init__(self, repository_root: Path, data_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.data_root = data_root.resolve()
        self.build_root = (self.data_root / "site-builds").resolve()
        self.log_root = (self.data_root / "site-releases").resolve()
        self.build_root.mkdir(parents=True, exist_ok=True)
        self.log_root.mkdir(parents=True, exist_ok=True)

    def available(self, source_root: Path | None = None) -> bool:
        root = (source_root or self.repository_root).resolve()
        return (root / "site" / "package.json").is_file()

    def current_fingerprint(self) -> str | None:
        pointer = self.build_root / "current.json"
        if not pointer.is_file():
            return None
        value = self._read_json(pointer).get("fingerprint")
        return str(value) if value else None

    def prepare(
        self,
        source_root: Path,
        *,
        operation_id: str,
        agent_id: str,
        version: str,
        catalog_module: str,
    ) -> dict[str, Any]:
        source_root = source_root.resolve()
        site_root = source_root / "site"
        if not self.available(source_root):
            raise SiteReleaseError(
                "The Web UI source is unavailable.",
                code="site_source_unavailable",
                phase="building_site",
            )
        node = shutil.which("node.exe") or shutil.which("node")
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if not node or not npm:
            raise SiteReleaseError(
                "Node.js and npm are required to build the Web UI.",
                code="site_build_tool_unavailable",
                phase="building_site",
            )
        linked_modules = self._ensure_node_modules(site_root)
        node_version = self._run_capture([node, "--version"], source_root, 15).strip()
        fingerprint = self.fingerprint(source_root, node_version=node_version)
        final_root = self._within_build_root(self.build_root / fingerprint)
        final_dist = final_root / "dist"
        marker = final_dist / "sapba-build.json"
        if self._valid_dist(final_dist) and marker.is_file():
            cached = self._read_json(marker)
            if cached.get("fingerprint") == fingerprint:
                operation_logs = self.log_root / operation_id
                operation_logs.mkdir(parents=True, exist_ok=True)
                astro = self.repository_root / "site" / "node_modules" / "astro" / "bin" / "astro.mjs"
                self._verify_candidate(
                    site_root,
                    final_dist,
                    operation_logs,
                    node=node,
                    astro=astro,
                    fingerprint=fingerprint,
                    agent_id=agent_id,
                    version=version,
                    catalog_module=catalog_module,
                )
                return {
                    "fingerprint": fingerprint,
                    "dist_path": str(final_dist),
                    "reused": True,
                    "node_version": node_version,
                    "linked_node_modules": linked_modules,
                }

        temporary_root = self._within_build_root(
            self.build_root / f".{fingerprint}.tmp-{os.getpid()}-{operation_id[-8:]}"
        )
        if temporary_root.exists():
            shutil.rmtree(temporary_root)
        temporary_dist = temporary_root / "dist"
        temporary_dist.mkdir(parents=True)
        operation_logs = self.log_root / operation_id
        operation_logs.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "PUBLIC_SITE_BASE": "/",
            "PUBLIC_SAPBA_API_URL": self._api_url(),
        }
        try:
            self._run_logged(
                [sys.executable, str(source_root / "scripts" / "documentation.py"), "--check"],
                source_root,
                operation_logs / "documentation.log",
                120,
                env,
                "site_documentation_invalid",
                "checking_documentation",
            )
            self._run_logged(
                [npm, "run", "validate"], site_root,
                operation_logs / "catalog-validation.log", 180, env,
                "site_catalog_validation_failed", "checking_catalog",
            )
            self._run_logged(
                [npm, "run", "catalog"], site_root,
                operation_logs / "catalog-generation.log", 180, env,
                "site_catalog_generation_failed", "building_site",
            )
            astro = self.repository_root / "site" / "node_modules" / "astro" / "bin" / "astro.mjs"
            if not astro.is_file():
                raise SiteReleaseError(
                    "Astro is unavailable.", code="site_build_tool_unavailable", phase="building_site"
                )
            self._run_logged(
                [node, str(astro), "build", "--outDir", str(temporary_dist)],
                site_root,
                operation_logs / "site-build.log",
                300,
                env,
                "site_build_failed",
                "building_site",
            )
            if not self._valid_dist(temporary_dist):
                raise SiteReleaseError(
                    "The Web UI build is incomplete.",
                    code="site_build_incomplete",
                    phase="building_site",
                )
            (temporary_dist / "sapba-build.json").write_text(
                json.dumps(
                    {
                        "fingerprint": fingerprint,
                        "agent_id": agent_id,
                        "version": version,
                        "catalog_module": catalog_module,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            self._verify_candidate(
                site_root,
                temporary_dist,
                operation_logs,
                node=node,
                astro=astro,
                fingerprint=fingerprint,
                agent_id=agent_id,
                version=version,
                catalog_module=catalog_module,
            )
            if final_root.exists():
                # Immutable caches are never overwritten. A conflicting cache
                # is evidence of an invalid fingerprint or external mutation.
                raise SiteReleaseError(
                    "A conflicting immutable Web UI cache already exists.",
                    code="site_build_cache_conflict",
                    phase="building_site",
                )
            temporary_root.replace(final_root)
        finally:
            if temporary_root.exists():
                shutil.rmtree(temporary_root, ignore_errors=True)
        return {
            "fingerprint": fingerprint,
            "dist_path": str(final_dist),
            "reused": False,
            "node_version": node_version,
            "linked_node_modules": linked_modules,
        }

    def switch(
        self,
        build: dict[str, Any],
        *,
        operation_id: str,
        agent_id: str,
        version: str,
        catalog_module: str,
    ) -> dict[str, Any]:
        script = self.repository_root / "scripts" / "Switch-SAPBusinessAgentsSite.ps1"
        if not script.is_file():
            return {
                "status": "failed",
                "failure_code": "site_switch_script_unavailable",
                "rolled_back": False,
            }
        result_path = self.log_root / operation_id / "site-switch-result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(script),
            "-DistPath", str(build["dist_path"]),
            "-Fingerprint", str(build["fingerprint"]),
            "-ExpectedAgentId", agent_id,
            "-ExpectedVersion", version,
            "-ExpectedModule", catalog_module,
            "-ResultPath", str(result_path),
        ]
        try:
            subprocess.run(
                command,
                cwd=self.repository_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
        except (OSError, subprocess.TimeoutExpired):
            return {
                "status": "failed",
                "failure_code": "site_switch_unavailable",
                "rolled_back": False,
            }
        if not result_path.is_file():
            return {
                "status": "failed",
                "failure_code": "site_switch_result_missing",
                "rolled_back": False,
            }
        result = self._read_json(result_path)
        if result.get("status") != "completed":
            return {
                **result,
                "status": "failed",
                "failure_code": str(result.get("failure_code") or "site_refresh_failed"),
            }
        self._remove_old_builds(str(build["fingerprint"]))
        return result

    def fingerprint(self, source_root: Path, *, node_version: str) -> str:
        source_root = source_root.resolve()
        files: list[Path] = []
        for directory in (source_root / "site" / "src", source_root / "site" / "scripts"):
            if directory.is_dir():
                files.extend(
                    path for path in directory.rglob("*")
                    if path.is_file() and "generated" not in path.relative_to(directory).parts
                )
        for directory, names in (
            (source_root / "agents", {"agent.json", "publication.json", "README.md"}),
            (source_root / "workflows", {"workflow.json", "publication.json", "README.md"}),
        ):
            if directory.is_dir():
                files.extend(
                    path for path in directory.rglob("*")
                    if path.is_file()
                    and path.name in names
                    and "versions" not in path.relative_to(directory).parts
                )
        for relative in (
            "site/astro.config.mjs", "site/package.json", "site/package-lock.json",
            "config/agent-authoring-standard.json", "config/agent-presentation-contract.json",
        ):
            path = source_root / relative
            if path.is_file():
                files.append(path)
        material = bytearray()
        for path in sorted(set(files), key=lambda item: item.relative_to(source_root).as_posix()):
            relative = path.relative_to(source_root).as_posix().encode("utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii")
            material.extend(relative + b"|" + digest + b"\n")
        material.extend(f"node|{node_version}\napi|{self._api_url()}\nsite_base|/\n".encode())
        return hashlib.sha256(material).hexdigest()

    def _ensure_node_modules(self, site_root: Path) -> bool:
        target = self.repository_root / "site" / "node_modules"
        link = site_root / "node_modules"
        if link.exists():
            return False
        if not target.is_dir():
            raise SiteReleaseError(
                "Web UI dependencies are unavailable.",
                code="site_dependencies_unavailable",
                phase="building_site",
            )
        if os.name != "nt":
            link.symlink_to(target, target_is_directory=True)
        else:
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                cwd=site_root,
                capture_output=True,
                text=True,
                check=False,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
            if result.returncode != 0 or not link.exists():
                raise SiteReleaseError(
                    "Web UI dependencies could not be attached to the publication worktree.",
                    code="site_dependencies_unavailable",
                    phase="building_site",
                )
        return True

    def _verify_candidate(
        self,
        site_root: Path,
        dist: Path,
        log_root: Path,
        *,
        node: str,
        astro: Path,
        fingerprint: str,
        agent_id: str,
        version: str,
        catalog_module: str,
    ) -> None:
        port = self._free_port()
        stdout = (log_root / "candidate.stdout.log").open("w", encoding="utf-8")
        stderr = (log_root / "candidate.stderr.log").open("w", encoding="utf-8")
        process = subprocess.Popen(
            [node, str(astro), "preview", "--host", "127.0.0.1", "--port", str(port), "--outDir", str(dist)],
            cwd=site_root,
            env={
                **os.environ,
                "PUBLIC_SITE_BASE": "/",
                "PUBLIC_SAPBA_API_URL": self._api_url(),
            },
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        try:
            deadline = time.monotonic() + 15
            paths = ["/zh/", "/en/", "/sapba-build.json"]
            detail_paths = [
                f"/zh/agents/{catalog_module}/{agent_id}/",
                f"/en/agents/{catalog_module}/{agent_id}/",
            ]
            redirect_paths: list[tuple[str, str]] = []
            repository_modules = [
                item.parent.parent.name
                for item in (site_root.parent / "agents").glob(f"*/{agent_id}/agent.json")
            ]
            if repository_modules and repository_modules[0] != catalog_module:
                redirect_paths.extend(
                    [
                        (
                            f"/zh/agents/{repository_modules[0]}/{agent_id}/",
                            f"/zh/agents/{catalog_module}/{agent_id}/",
                        ),
                        (
                            f"/en/agents/{repository_modules[0]}/{agent_id}/",
                            f"/en/agents/{catalog_module}/{agent_id}/",
                        ),
                    ]
                )
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                try:
                    bodies = {path: self._fetch(f"http://127.0.0.1:{port}{path}") for path in paths}
                    marker = json.loads(bodies["/sapba-build.json"])
                    if marker.get("fingerprint") != fingerprint:
                        raise ValueError("fingerprint")
                    for path in detail_paths:
                        body = self._fetch(f"http://127.0.0.1:{port}{path}")
                        if agent_id not in body or version not in body:
                            raise ValueError("agent-version")
                    for path, destination in redirect_paths:
                        body = self._fetch(f"http://127.0.0.1:{port}{path}")
                        if agent_id not in body or destination not in body:
                            raise ValueError("agent-redirect")
                    asset = re.search(r'(?:src|href)="(?P<path>/_astro/[^"]+)"', bodies["/zh/"])
                    if asset:
                        self._fetch(f"http://127.0.0.1:{port}{asset.group('path')}")
                    return
                except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
                    time.sleep(0.25)
            raise SiteReleaseError(
                "The candidate Web UI did not pass its loopback health checks.",
                code="site_candidate_health_failed",
                phase="checking_site",
            )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            stdout.close()
            stderr.close()

    def _remove_old_builds(self, current: str) -> None:
        pointers = {current}
        for name in ("current.json", "previous.json"):
            path = self.build_root / name
            if path.is_file():
                value = self._read_json(path).get("fingerprint")
                if value:
                    pointers.add(str(value))
        builds = sorted(
            (item for item in self.build_root.iterdir() if item.is_dir() and not item.name.startswith(".")),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        keep = set(pointers)
        for item in builds:
            if len(keep) < 3:
                keep.add(item.name)
                continue
            if item.name not in keep:
                shutil.rmtree(self._within_build_root(item), ignore_errors=True)

    def _api_url(self) -> str:
        return os.getenv("SAPBA_INTERNAL_API_URL", "http://127.0.0.1:8765").rstrip("/")

    def _within_build_root(self, path: Path) -> Path:
        resolved = path.resolve()
        if self.build_root not in resolved.parents:
            raise SiteReleaseError(
                "The Web UI cache path escaped its configured root.",
                code="site_build_path_invalid",
                phase="building_site",
            )
        return resolved

    @staticmethod
    def _valid_dist(path: Path) -> bool:
        return (
            (path / "index.html").is_file()
            and (path / "zh" / "index.html").is_file()
            and (path / "en" / "index.html").is_file()
            and (path / "_astro").is_dir()
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _run_capture(command: list[str], cwd: Path, timeout: int) -> str:
        result = subprocess.run(
            command, cwd=cwd, check=True, capture_output=True, text=True, timeout=timeout,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        return result.stdout

    @staticmethod
    def _run_logged(
        command: list[str], cwd: Path, log_path: Path, timeout: int,
        env: dict[str, str], code: str, phase: str,
    ) -> None:
        with log_path.open("w", encoding="utf-8") as log:
            try:
                result = subprocess.run(
                    command, cwd=cwd, check=False, stdout=log, stderr=subprocess.STDOUT,
                    text=True, timeout=timeout, env=env,
                    creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise SiteReleaseError("The Web UI command did not complete.", code=code, phase=phase) from exc
        if result.returncode != 0:
            raise SiteReleaseError("The Web UI command failed.", code=code, phase=phase)

    @staticmethod
    def _fetch(url: str) -> str:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            if response.status != 200:
                raise urllib.error.URLError(str(response.status))
            return response.read().decode("utf-8", errors="replace")

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])
