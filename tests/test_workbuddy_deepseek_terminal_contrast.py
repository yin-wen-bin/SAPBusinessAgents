"""Offline model-contrast guards: same Schema, real terminal, no qualification."""
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
    spec = importlib.util.spec_from_file_location("deepseek_terminal_test", root / "scripts/verify-workbuddy-deepseek-terminal-contrast.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def setup_terminal(module, tmp_path, monkeypatch, *, patch=None, identity=None, failure=None, cleanup=True):
    canonical, wire, prompts = module.followup.previous.contrast.payloads()
    binding = {"provider_id": "workbuddy", "model": module.MODEL, "environment_digest": "current", "service_site": "china"}
    request = {"canonical_schema": canonical, "wire_schema": wire, **prompts}
    monkeypatch.setattr(module, "check", lambda *_: ({"budget_seconds": 90, "reference_task_id": "old"}, copy.deepcopy(request), binding))
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
                    {"status": "completed", "cleanup_complete": cleanup, "validation_job": False})
        _, value = module.baseline.probe_contract()
        value.update(patch or {})
        return {"text": json.dumps(value), "output_format": "native_json_schema", "actual_model": identity or module.MODEL,
                "model_identity_source": "assistant_message"}
    async def close(): pass
    return SimpleNamespace(environment=SimpleNamespace(root=tmp_path),
        supervisor=SimpleNamespace(run=run, close=close, pending=lambda: [])), calls


def test_same_full_contract_and_no_qualification(module, tmp_path, monkeypatch):
    manager, calls = setup_terminal(module, tmp_path, monkeypatch)
    report = asyncio.run(module.terminal(manager, tmp_path / "batch"))
    assert report["phase"] == "passed" and report["checks"]["canonical_schema_validated"] and len(calls) == 1
    assert report["checks"]["actual_model"] == module.MODEL
    saved = json.loads((tmp_path / "batch/terminal-report.json").read_text())
    assert saved["model_calls"] == 1 and saved["sap_calls"] == 0
    assert not saved["operation_qualification_granted"] and not saved["model_compatibility_registered"]


@pytest.mark.parametrize("patch", [{"input_kind": "null"}, {"input_field": ""}, {"source_complete": True},
    {"evidence_refs": ["invented"]}, {"input_kind": "secure_business_reference", "input_field": "receipt_reference"},
    {"unexpected": "extra"}])
def test_no_enum_repair_or_contract_weakening(module, tmp_path, monkeypatch, patch):
    manager, calls = setup_terminal(module, tmp_path, monkeypatch, patch=patch)
    report = asyncio.run(module.terminal(manager, tmp_path / "batch"))
    assert report["phase"] == "failed" and len(calls) == 1 and report["checks"] is None


def test_timeout_never_retries(module, tmp_path, monkeypatch):
    manager, calls = setup_terminal(module, tmp_path, monkeypatch, failure="workbuddy_deadline_exceeded")
    batch = tmp_path / "batch"
    report = asyncio.run(module.terminal(manager, batch))
    assert report["failure_code"] == "workbuddy_deadline_exceeded" and len(calls) == 1
    with pytest.raises(AssertionError, match="already_attempted"):
        asyncio.run(module.terminal(manager, batch))
    assert len(calls) == 1


@pytest.mark.parametrize("options", [{"identity": "hy4-preview"}, {"cleanup": False}])
def test_model_identity_and_cleanup_are_required(module, tmp_path, monkeypatch, options):
    manager, calls = setup_terminal(module, tmp_path, monkeypatch, **options)
    report = asyncio.run(module.terminal(manager, tmp_path / "batch"))
    assert report["phase"] == "failed" and len(calls) == 1


def test_invalid_binding_blocks_before_model_call(module, tmp_path, monkeypatch):
    manager, calls = setup_terminal(module, tmp_path, monkeypatch)
    def fail(*_): raise AssertionError("verification_model_records_changed")
    monkeypatch.setattr(module, "check", fail)
    with pytest.raises(AssertionError, match="model_records_changed"):
        asyncio.run(module.terminal(manager, tmp_path / "batch"))
    assert calls == []
