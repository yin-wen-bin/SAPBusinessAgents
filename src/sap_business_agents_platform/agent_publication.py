from __future__ import annotations

import json
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class PublicationInfrastructureError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class AgentPublicationWorkspace:
    """Prepare a single publication commit without switching the main checkout."""

    def __init__(self, repository_root: Path, data_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.data_root = data_root.resolve()
        self.worktree_root = (self.data_root / "publication-worktrees").resolve()
        self.worktree_root.mkdir(parents=True, exist_ok=True)

    def assert_clean_main(self, *, expected_sha: str | None = None) -> str:
        branch = self._git(["branch", "--show-current"]).strip()
        if branch != "main":
            raise PublicationInfrastructureError(
                "Agent publication requires the official checkout to remain on main.",
                code="git_main_required",
            )
        if self._git(["status", "--porcelain"]).strip():
            raise PublicationInfrastructureError(
                "Agent publication requires a clean Git worktree.",
                code="git_worktree_dirty",
            )
        sha = self._git(["rev-parse", "HEAD"]).strip()
        if expected_sha and sha != expected_sha:
            raise PublicationInfrastructureError(
                "The main branch changed while publication was being prepared.",
                code="git_publication_base_changed",
            )
        return sha

    def create(self, operation_id: str, branch: str, base_sha: str) -> Path:
        path = (self.worktree_root / operation_id).resolve()
        if self.worktree_root not in path.parents:
            raise PublicationInfrastructureError(
                "Publication worktree escaped its configured root.",
                code="git_publication_worktree_invalid",
            )
        if path.exists():
            raise PublicationInfrastructureError(
                "Publication worktree already exists.",
                code="git_publication_worktree_exists",
            )
        result = subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(path), base_sha],
            cwd=self.repository_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise PublicationInfrastructureError(
                "The isolated publication worktree could not be created.",
                code="git_publication_worktree_failed",
            )
        return path

    def commit(self, worktree: Path, message: str, allowed_agent_path: Path) -> str:
        changed = self._git_at(worktree, ["git", "status", "--porcelain"]).splitlines()
        if not changed:
            raise PublicationInfrastructureError(
                "Agent publication produced no repository change.",
                code="agent_management_no_change",
            )
        allowed_agent = allowed_agent_path.as_posix().rstrip("/") + "/"
        invalid: list[str] = []
        for line in changed:
            relative = line[3:].strip().strip('"').replace("\\", "/")
            if " -> " in relative:
                relative = relative.split(" -> ", 1)[1]
            if relative == "README.md" or relative.startswith(allowed_agent):
                continue
            if relative.startswith(("agents/", "workflows/")) and relative.endswith("README.md"):
                continue
            invalid.append(relative)
        if invalid:
            raise PublicationInfrastructureError(
                "Publication preparation changed files outside the approved Agent and documentation scope.",
                code="git_publication_scope_invalid",
            )
        # Scope validation above has already rejected unrelated files. Staging
        # the whole isolated worktree also works in small test repositories
        # where optional documentation roots are absent.
        self._run_at(worktree, ["git", "add", "-A", "--", "."])
        self._run_at(worktree, ["git", "commit", "-m", message])
        return self._git_at(worktree, ["git", "rev-parse", "HEAD"]).strip()

    def fast_forward(self, candidate_sha: str, base_sha: str) -> str:
        self.assert_clean_main(expected_sha=base_sha)
        self._run(["merge", "--ff-only", candidate_sha])
        merged = self._git(["rev-parse", "HEAD"]).strip()
        if merged != candidate_sha:
            raise PublicationInfrastructureError(
                "The main branch did not reach the prepared publication commit.",
                code="git_publication_merge_unconfirmed",
            )
        if self._git(["branch", "--show-current"]).strip() != "main":
            raise PublicationInfrastructureError(
                "The official checkout left main during publication.",
                code="git_main_required",
            )
        return merged

    def is_merged(self, candidate_sha: str) -> bool:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", candidate_sha, "main"],
            cwd=self.repository_root,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def cleanup(self, worktree: Path | None) -> None:
        if worktree is None or not worktree.exists():
            return
        resolved = worktree.resolve()
        if self.worktree_root not in resolved.parents:
            return
        modules = resolved / "site" / "node_modules"
        if modules.exists() and self._is_link_like(modules):
            try:
                modules.rmdir()
            except OSError:
                pass
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(resolved)],
            cwd=self.repository_root,
            capture_output=True,
            text=True,
            check=False,
        )

    def branch_name(self, agent_id: str, version: str, operation_id: str) -> str:
        safe = "".join(char if char.isalnum() or char in "._-" else "-" for char in version)
        return f"codex/agent-{agent_id}-publish-v{safe}-{operation_id[-8:]}"

    def _git(self, args: list[str]) -> str:
        return self._git_at(self.repository_root, ["git", *args])

    @staticmethod
    def _git_at(cwd: Path, command: list[str]) -> str:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise PublicationInfrastructureError(
                "A Git publication command failed.", code="git_publication_failed"
            )
        return result.stdout

    def _run(self, args: list[str]) -> None:
        self._run_at(self.repository_root, ["git", *args])

    @staticmethod
    def _run_at(cwd: Path, command: list[str]) -> None:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise PublicationInfrastructureError(
                "A Git publication command failed.", code="git_publication_failed"
            )

    @staticmethod
    def _is_link_like(path: Path) -> bool:
        attributes = getattr(os.stat(path, follow_symlinks=False), "st_file_attributes", 0)
        return path.is_symlink() or bool(attributes & 0x400)


@contextmanager
def deployment_file_lock(data_root: Path, *, timeout_seconds: float = 60) -> Iterator[None]:
    """Coordinate Git publication with manual startup across processes."""

    lock_path = data_root.resolve() / "deployment.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    deadline = time.monotonic() + timeout_seconds
    locked = False
    try:
        while not locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise PublicationInfrastructureError(
                        "Another startup or deployment operation is still active.",
                        code="deployment_operation_active",
                    )
                time.sleep(0.25)
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"pid": os.getpid(), "acquired_at": time.time()}).encode())
        handle.flush()
        handle.seek(0)
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()
