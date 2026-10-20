"""Continuation preparation/guards only; never a live qualification."""
import asyncio
import copy
import importlib.util
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("sap_continuation_test", ROOT / "scripts/verify-workbuddy-sap-continuation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def context(module, tmp_path, monkeypatch):
    from sap_business_agents_platform.workbuddy_environment import atomic_json
    parent, child = tmp_path / "parent", tmp_path / "child"
    snapshot = {"service_site": "china", "authentication_revision": 4, "model": "hy4-preview", "actual_model": "hy4-preview"}
    freeze = {"scope": "china_non_sap", "fixture_digests": {}, "controls": {"codex": "same"}, "allowed_operations": sorted(module.runner.NON_SAP)}
    catalog = {"items": []}
    for name, value in {"runtime-snapshot.json": snapshot, "freeze.json": freeze,
                        "catalog-snapshot.json": catalog, "result-summary.json": {"phase": "passed", "qualification": {"validated": 11}}}.items():
        atomic_json(parent / name, value)
    caps = {op: {"status": "validated", "permission_modes": ["bounded", "trusted_local"]} for op in module.runner.NON_SAP}
    manager = SimpleNamespace(snapshot=lambda: {"operation_capabilities": caps}, environment=SimpleNamespace(maintenance=nullcontext))
    monkeypatch.setattr(module, "ROOT", tmp_path)
    # Only simulate hashes; a production guard still checks all real files.
    monkeypatch.setattr(module, "file_hash", lambda path: Path(path).name)
    monkeypatch.setattr(module.runner, "require_idle", lambda *_: None)
    monkeypatch.setattr(module.runner, "check_frozen", lambda *_: freeze)
    monkeypatch.setattr(module.runner, "controls", lambda *_: {"codex": "same"})
    monkeypatch.setattr(module.runner, "batch_path", lambda _: parent)
    monkeypatch.setattr(module.live_guard, "current_binding", lambda *_: copy.deepcopy(snapshot))
    monkeypatch.setattr(module.live_guard, "check_batch", lambda *_: copy.deepcopy(snapshot))
    identity = {"agent_id": "mm-po-gr-status", "execution_digest": "same"}
    monkeypatch.setattr(module, "source_identity", lambda: copy.deepcopy(identity))
    monkeypatch.setattr(module, "verified_account", lambda *_: {"revision": 4, "status": "authenticated"})
    return SimpleNamespace(parent=parent, child=child, manager=manager, snapshot=snapshot, caps=caps,
                           identity=identity, save=atomic_json)


def test_prepare_reuses_exact_parent_binding_and_never_changes_parent(module, context):
    c = context
    before = {p.name: p.read_bytes() for p in c.parent.iterdir()}
    result = module.prepare(c.manager, c.child, c.parent)
    assert result["model_calls"] == result["sap_calls"] == 0
    assert json.loads((c.child / "runtime-snapshot.json").read_text(encoding="utf-8")) == c.snapshot
    assert {p.name: p.read_bytes() for p in c.parent.iterdir()} == before
    frozen = json.loads((c.child / "freeze.json").read_text(encoding="utf-8"))
    assert frozen["scope"] == "china_sap_readonly" and set(frozen["allowed_operations"]) == module.OPERATIONS
    assert frozen["case_parameters"]["acceptance"]["case_count"] == 1
    assert frozen["case_parameters"]["sample"]["purchase_order"] == "4500001466"
    assert "top=" not in frozen["case_parameters"]["free_query"]["limited_sample"]["query"]
    question = frozen["case_parameters"]["free_query"]["limited_sample"]["query"]
    assert "从SAP最多读取一条" in question and "读取请求必须显式限制为一条" in question
    assert "不要先完整读取再显示一条" in question
    assert "workbuddy_validation_diagnostics.py" in frozen["fixture_digests"]
    with pytest.raises(AssertionError, match="new_batch_required"):
        module.prepare(c.manager, c.child, c.parent)


def test_deepseek_parent_summary_and_identity_are_explicit(module, context, monkeypatch):
    c = context
    c.snapshot.update(model="deepseek-v4.1-flash", actual_model="deepseek-v4.1-flash", environment_digest="same-release")
    c.save(c.parent / "runtime-snapshot.json", c.snapshot)
    c.save(c.parent / "result-summary.json", {"phase": "passed", "valid_operations": 11,
        "model": c.snapshot["model"], "environment_digest": c.snapshot["environment_digest"]})
    calls = []
    def current(_, model):
        calls.append(model)
        assert model == c.snapshot["model"]
        return copy.deepcopy(c.snapshot)
    monkeypatch.setattr(module.live_guard, "current_binding", current)
    accounts = []
    def account(_, model):
        accounts.append(model)
        return {"revision": 4, "status": "authenticated"}
    monkeypatch.setattr(module, "verified_account", account)
    assert module.prepare(c.manager, c.child, c.parent)["model_calls"] == 0
    module.check_batch(c.manager, c.child)
    assert calls and accounts and set(calls + accounts) == {"deepseek-v4.1-flash"}


def test_summary_cannot_rebind_new_count_to_another_model(module, context):
    c = context
    c.save(c.parent / "result-summary.json", {"phase": "passed", "valid_operations": 11,
        "model": "different-model", "environment_digest": "different-release"})
    with pytest.raises(AssertionError, match="parent_summary_binding_changed"):
        module.prepare(c.manager, c.child, c.parent)
    assert not c.child.exists()


def test_prepare_rejects_changed_binding_or_incomplete_modes(module, context, monkeypatch):
    c = context
    monkeypatch.setattr(module.live_guard, "current_binding", lambda *_: {**c.snapshot, "service_site": "international"})
    with pytest.raises(AssertionError, match="parent_binding_changed"):
        module.prepare(c.manager, c.child, c.parent)
    assert not c.child.exists()
    monkeypatch.setattr(module.live_guard, "current_binding", lambda *_: copy.deepcopy(c.snapshot))
    c.caps["review_agent_feedback"]["permission_modes"] = ["bounded"]
    with pytest.raises(AssertionError, match="prerequisites_not_ready"):
        module.prepare(c.manager, c.child, c.parent)
    assert not c.child.exists()


def test_source_changes_and_parent_record_changes_block_continuation(module, context, monkeypatch):
    c = context
    module.prepare(c.manager, c.child, c.parent)
    module.check_batch(c.manager, c.child)
    c.identity["execution_digest"] = "changed"
    with pytest.raises(AssertionError, match="source_agent_changed"):
        module.check_batch(c.manager, c.child)
    c.identity["execution_digest"] = "same"
    monkeypatch.setattr(module, "file_hash", lambda _: "changed")
    with pytest.raises(AssertionError, match="parent_record_changed"):
        module.check_batch(c.manager, c.child)


def test_a_failed_attempt_cannot_be_redispatched_and_sample_cannot_skip_free_query(module, context):
    c = context
    module.prepare(c.manager, c.child, c.parent)
    c.save(c.child / "limited_sample-summary.json", {"phase": "failed", "cleanup_pending": False})
    with pytest.raises(AssertionError, match="already_attempted"):
        asyncio.run(module.dispatch(c.manager, "limited_sample", c.child))
    with pytest.raises(AssertionError, match="prerequisites_not_ready"):
        asyncio.run(module.dispatch(c.manager, "sample", c.child))
    assert not (c.child / "sample-summary.json").exists()


def test_cli_dry_run_never_instantiates_manager(module, monkeypatch, capsys):
    monkeypatch.setattr(module, "WorkBuddyManager", lambda *_: pytest.fail("dry run must not create a Runtime"))
    monkeypatch.setattr(module.runner, "batch_path", lambda value: Path(value))
    monkeypatch.setattr(module.sys, "argv", ["verify", "prepare", "--batch", "unused"])
    assert module.main() == 0
    assert json.loads(capsys.readouterr().out)["model_calls"] == 0


def test_source_identity_uses_repository_manifest_slug(module, monkeypatch):
    manifest = {"slug": "mm-po-gr-status", "version": "0.1.1"}
    monkeypatch.setattr(module, "AgentRepository", lambda *_: SimpleNamespace(
        get=lambda _: manifest, package=lambda _: {"rules_source": "unchanged fixture rules"}))
    monkeypatch.setattr(module, "agent_digest", lambda _: "agent-digest")
    monkeypatch.setattr(module, "agent_execution_digest", lambda m, r: "execution-digest")
    assert module.source_identity() == {"agent_id": "mm-po-gr-status", "version": "0.1.1",
        "agent_digest": "agent-digest", "execution_digest": "execution-digest"}
