from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest

from sap_business_agents_platform.agent_publication import (
    AgentPublicationWorkspace,
    PublicationInfrastructureError,
)
from sap_business_agents_platform.repository_gate import RepositoryReadWriteGate
from sap_business_agents_platform.site_release import SiteReleaseManager


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    agent = root / "agents" / "Common" / "sample-agent"
    agent.mkdir(parents=True)
    (root / ".gitignore").write_text(".local-data/\n", encoding="utf-8")
    (agent / "agent.json").write_text(
        '{"slug":"sample-agent","version":"1.0.0"}\n', encoding="utf-8"
    )
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "Initial")
    return root


def test_publication_prepares_in_worktree_and_fast_forwards_main(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    workspace = AgentPublicationWorkspace(root, root / ".local-data")
    base = workspace.assert_clean_main()
    branch = workspace.branch_name("sample-agent", "1.0.1", "agent_op_1234567890")
    worktree = workspace.create("agent_op_1234567890", branch, base)
    try:
        published = worktree / "agents" / "Common" / "sample-agent" / "versions" / "1.0.1"
        published.mkdir(parents=True)
        (published / "agent.json").write_text(
            '{"slug":"sample-agent","version":"1.0.1"}\n', encoding="utf-8"
        )
        candidate = workspace.commit(
            worktree, "Publish sample-agent v1.0.1", Path("agents/Common/sample-agent")
        )
        assert _git(root, "branch", "--show-current") == "main"
        assert _git(root, "rev-parse", "HEAD") == base
        workspace.fast_forward(candidate, base)
        assert _git(root, "branch", "--show-current") == "main"
        assert _git(root, "rev-parse", "HEAD") == candidate
    finally:
        workspace.cleanup(worktree)
    assert not worktree.exists()
    assert branch in _git(root, "branch", "--list", branch)


def test_publication_rejects_changes_outside_agent_and_documentation(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    workspace = AgentPublicationWorkspace(root, root / ".local-data")
    base = workspace.assert_clean_main()
    branch = workspace.branch_name("sample-agent", "1.0.1", "agent_op_scope")
    worktree = workspace.create("agent_op_scope", branch, base)
    try:
        (worktree / "unexpected.txt").write_text("not publication content", encoding="utf-8")
        with pytest.raises(PublicationInfrastructureError) as failure:
            workspace.commit(
                worktree, "Publish sample-agent v1.0.1", Path("agents/Common/sample-agent")
            )
        assert failure.value.code == "git_publication_scope_invalid"
    finally:
        workspace.cleanup(worktree)


def test_repository_writer_waits_for_complete_reader_snapshot() -> None:
    gate = RepositoryReadWriteGate()
    reader_entered = threading.Event()
    release_reader = threading.Event()
    writer_entered = threading.Event()

    def reader() -> None:
        with gate.read():
            reader_entered.set()
            release_reader.wait(timeout=2)

    def writer() -> None:
        reader_entered.wait(timeout=2)
        with gate.write():
            writer_entered.set()

    first = threading.Thread(target=reader)
    second = threading.Thread(target=writer)
    first.start()
    second.start()
    assert reader_entered.wait(timeout=1)
    time.sleep(0.05)
    assert not writer_entered.is_set()
    release_reader.set()
    assert writer_entered.wait(timeout=1)
    first.join(timeout=1)
    second.join(timeout=1)


def test_site_fingerprint_includes_catalog_lifecycle_and_frontend_sources(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    (root / "site" / "src").mkdir(parents=True)
    (root / "site" / "scripts").mkdir(parents=True)
    (root / "agents" / "Common" / "sample-agent").mkdir(parents=True)
    (root / "agents" / "Common" / "sample-agent" / "agent.json").write_text(
        "{}\n", encoding="utf-8"
    )
    (root / "site" / "src" / "page.ts").write_text(
        "export const page = 1;\n", encoding="utf-8"
    )
    (root / "site" / "package.json").write_text("{}\n", encoding="utf-8")
    publication = root / "agents" / "Common" / "sample-agent" / "publication.json"
    publication.write_text(
        '{"catalog_module":"Common","state":"active"}\n', encoding="utf-8"
    )
    manager = SiteReleaseManager(root, root / ".local-data")
    before = manager.fingerprint(root, node_version="v22.13.0")
    publication.write_text(
        '{"catalog_module":"MM","state":"active"}\n', encoding="utf-8"
    )
    after = manager.fingerprint(root, node_version="v22.13.0")
    assert before != after


def test_manual_restart_builds_before_stopping_services_and_switch_does_not_stop_api() -> None:
    root = Path(__file__).resolve().parents[1]
    startup = (root / "scripts" / "Start-SAPBusinessAgents.ps1").read_text(
        encoding="utf-8-sig"
    )
    switch = (root / "scripts" / "Switch-SAPBusinessAgentsSite.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert startup.index('Name "site_build_before_restart"') < startup.index('Name "port_detection"')
    assert "Stop-ExpectedListener -Name \"SAPBusinessAgents API\"" in startup
    assert "8765" not in switch
    assert "Stop-Site" in switch
    assert '"--ignore-lock"' in switch


def test_candidate_preview_uses_local_base_and_checks_repository_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    site = root / "site"
    dist = tmp_path / "dist"
    logs = tmp_path / "logs"
    (root / "agents" / "Common" / "sample-agent").mkdir(parents=True)
    (root / "agents" / "Common" / "sample-agent" / "agent.json").write_text(
        "{}\n", encoding="utf-8"
    )
    site.mkdir(parents=True)
    dist.mkdir()
    logs.mkdir()
    captured: dict[str, object] = {}
    requested: list[str] = []

    class PreviewProcess:
        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            return None

        def wait(self, timeout: int) -> None:
            return None

    def launch(*args: object, **kwargs: object) -> PreviewProcess:
        captured["command"] = args[0]
        captured.update(kwargs)
        return PreviewProcess()

    manager = SiteReleaseManager(root, root / ".local-data")
    monkeypatch.setattr(subprocess, "Popen", launch)
    monkeypatch.setattr(manager, "_free_port", lambda: 54321)

    def fetch(url: str) -> str:
        path = url.split(":54321", 1)[1]
        requested.append(path)
        if path == "/sapba-build.json":
            return '{"fingerprint":"fingerprint"}'
        if path in {
            "/zh/agents/MM/sample-agent/",
            "/en/agents/MM/sample-agent/",
        }:
            return "sample-agent 1.0.1"
        if path.startswith("/zh/agents/Common/"):
            return "/zh/agents/MM/sample-agent/"
        if path.startswith("/en/agents/Common/"):
            return "/en/agents/MM/sample-agent/"
        return "<html></html>"

    monkeypatch.setattr(manager, "_fetch", fetch)
    manager._verify_candidate(
        site,
        dist,
        logs,
        node="node",
        astro=site / "astro.mjs",
        fingerprint="fingerprint",
        agent_id="sample-agent",
        version="1.0.1",
        catalog_module="MM",
    )

    environment = captured["env"]
    assert "--ignore-lock" in captured["command"]
    assert isinstance(environment, dict)
    assert environment["PUBLIC_SITE_BASE"] == "/"
    assert environment["PUBLIC_SAPBA_API_URL"] == "http://127.0.0.1:8765"
    assert "/zh/agents/Common/sample-agent/" in requested
    assert "/en/agents/Common/sample-agent/" in requested
