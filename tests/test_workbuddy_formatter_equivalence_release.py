"""Offline once-only and original-validator gates for new release diagnostics."""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.workbuddy_environment import atomic_json, digest, WorkBuddyError


@pytest.fixture
def module(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    spec = importlib.util.spec_from_file_location("equivalence_release_test", root / "scripts/verify-workbuddy-formatter-equivalence-release.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def setup_terminal(module, tmp_path, monkeypatch, *, patch=None, failure=None):
    canonical, wire, prompts = module.contrast.payloads()
    binding = {"provider_id": "workbuddy", "model": "hy4-preview", "environment_digest": "new", "service_site": "china"}
    request = {"canonical_schema": canonical, "wire_schema": wire, **prompts}
    monkeypatch.setattr(module, "check_new", lambda *_: ({"budget_seconds": 90}, copy.deepcopy(request), binding))
    monkeypatch.setattr(module.baseline.runner, "require_idle", lambda *_: None)
    calls = []
    async def run(**kwargs):
        calls.append(kwargs)
        assert kwargs["snapshot"] == binding and kwargs["seconds"] == 90
        assert kwargs["probe"] and kwargs["mode"] == "bounded" and kwargs["tool_handler"] is None
        assert kwargs["payload"]["output_schema"] == wire
        assert kwargs["payload"]["system_prompt"] == prompts["system_prompt"]
        assert kwargs["payload"]["prompt"] == prompts["prompt"] and "tools" not in kwargs["payload"]
        kwargs["emit"]("agent_runtime_query_submitted", {"provider_id": "workbuddy"})
        if failure: raise WorkBuddyError(failure)
        atomic_json(tmp_path / "jobs" / (digest(kwargs["task_id"]) + ".json"),
                    {"status": "completed", "cleanup_complete": True, "validation_job": False})
        _, value = module.baseline.probe_contract()
        value.update(patch or {})
        return {"text": json.dumps(value), "output_format": "native_json_schema", "actual_model": "hy4-preview",
                "model_identity_source": "assistant_message"}
    async def close(): pass
    return SimpleNamespace(environment=SimpleNamespace(root=tmp_path),
        supervisor=SimpleNamespace(run=run, close=close, pending=lambda: [])), calls


def test_success_requires_canonical_schema_and_does_not_grant_qualification(module, tmp_path, monkeypatch):
    manager, calls = setup_terminal(module, tmp_path, monkeypatch)
    report = asyncio.run(module.terminal(manager, tmp_path / "batch"))
    assert report["phase"] == "passed" and report["checks"]["canonical_schema_validated"] and len(calls) == 1
    saved = json.loads((tmp_path / "batch/terminal-report.json").read_text())
    assert saved["model_calls"] == 1 and saved["sap_calls"] == 0
    assert not saved["operation_qualification_granted"] and not saved["model_compatibility_registered"]


@pytest.mark.parametrize("patch", [{"input_kind": "null"}, {"input_field": ""}, {"source_complete": True}, {"evidence_refs": ["invented"]}])
def test_original_validator_and_synthetic_scope_cannot_be_bypassed(module, tmp_path, monkeypatch, patch):
    manager, calls = setup_terminal(module, tmp_path, monkeypatch, patch=patch)
    report = asyncio.run(module.terminal(manager, tmp_path / "batch"))
    assert report["phase"] == "failed" and len(calls) == 1 and report["checks"] is None


def test_failure_has_no_retry_and_uses_primary_safe_code(module, tmp_path, monkeypatch):
    manager, calls = setup_terminal(module, tmp_path, monkeypatch, failure="workbuddy_deadline_exceeded")
    batch = tmp_path / "batch"
    report = asyncio.run(module.terminal(manager, batch))
    assert report["failure_code"] == "workbuddy_deadline_exceeded" and len(calls) == 1
    with pytest.raises(AssertionError, match="already_attempted"):
        asyncio.run(module.terminal(manager, batch))
    assert len(calls) == 1


def test_unconfirmed_binding_blocks_before_dispatch(module, tmp_path, monkeypatch):
    manager, calls = setup_terminal(module, tmp_path, monkeypatch)
    def fail(*_): raise AssertionError("verification_new_authentication_required")
    monkeypatch.setattr(module, "check_new", fail)
    with pytest.raises(AssertionError, match="authentication_required"):
        asyncio.run(module.terminal(manager, tmp_path / "batch"))
    assert calls == []


def test_failed_offline_xml_blocks_freeze(module, tmp_path, monkeypatch):
    monkeypatch.setattr(module.baseline.runner, "require_idle", lambda *_: None)
    monkeypatch.setattr(module, "before_check", lambda *_: ({"controls": {"files": {module.WORKER: "old"}}}, {}))
    monkeypatch.setattr(module, "file_hash", lambda *_: "new")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    xml = tmp_path / ".local-data/report.xml"
    xml.parent.mkdir()
    xml.write_text('<testsuites><testsuite tests="1" failures="1" errors="0"/></testsuites>')
    with pytest.raises(AssertionError, match="offline_tests_failed"):
        module.freeze(None, tmp_path / "batch", xml)
    assert not (tmp_path / "batch/freeze.json").exists()
