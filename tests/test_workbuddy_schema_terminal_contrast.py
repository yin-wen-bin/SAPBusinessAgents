"""Offline guards for an explicitly authorized, once-only synthetic contrast."""
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
    spec = importlib.util.spec_from_file_location("terminal_contrast_test", root / "scripts/verify-workbuddy-schema-terminal-contrast.py")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_prompt_and_contract_unchanged_only_wire_schema_representation(module):
    from sap_business_agents_platform.workbuddy_harness import native_terminal_instructions
    from jsonschema import Draft202012Validator
    canonical, wire, prompts = module.payloads()
    original, example = module.baseline.probe_contract()
    assert canonical == original and wire != canonical
    assert wire["required"] == canonical["required"]
    assert prompts["prompt"].endswith(json.dumps(example, ensure_ascii=False))
    assert prompts["system_prompt"].endswith(native_terminal_instructions(canonical))
    for checker in (Draft202012Validator(canonical), Draft202012Validator(wire)):
        checker.validate(example)
        assert not checker.is_valid({**example, "input_kind": "null"})
        assert not checker.is_valid({name: example[name] for name in list(example)[:3]})


def manager_for_case(module, tmp_path, monkeypatch, *, patch=None, failure=None):
    canonical, wire, prompts = module.payloads()
    before = {"binding": {"provider_id": "workbuddy", "model": "hy4-preview", "service_site": "china",
        "environment_digest": module.EXPECTED_RELEASE}, "original_case": {"budget_seconds": 90}}
    request = {"canonical_schema": canonical, "wire_schema": wire, **prompts}
    monkeypatch.setattr(module, "check_frozen", lambda *_: (before, copy.deepcopy(request)))
    monkeypatch.setattr(module.baseline.runner, "require_idle", lambda *_: None)
    calls = []
    async def run(**kwargs):
        calls.append(kwargs)
        assert kwargs["seconds"] == 90 and kwargs["probe"] and kwargs["mode"] == "bounded"
        assert kwargs["operation"] == "model_check" and kwargs["tool_handler"] is None
        assert kwargs["payload"]["output_schema"] == wire
        assert kwargs["payload"]["prompt"] == prompts["prompt"] and kwargs["payload"]["system_prompt"] == prompts["system_prompt"]
        assert "tools" not in kwargs["payload"]
        kwargs["emit"]("agent_runtime_query_submitted", {"provider_id": "workbuddy"})
        if failure:
            raise WorkBuddyError(failure)
        atomic_json(tmp_path / "jobs" / (digest(kwargs["task_id"]) + ".json"),
                    {"status": "completed", "cleanup_complete": True, "validation_job": False})
        _, value = module.baseline.probe_contract()
        value.update(patch or {})
        return {"text": json.dumps(value), "output_format": "native_json_schema", "actual_model": "hy4-preview",
                "model_identity_source": "assistant_message"}
    async def close():
        pass
    owner = SimpleNamespace(environment=SimpleNamespace(root=tmp_path),
        supervisor=SimpleNamespace(run=run, close=close, pending=lambda: []))
    return owner, calls


def test_success_uses_original_checker_without_any_registration(module, tmp_path, monkeypatch):
    owner, calls = manager_for_case(module, tmp_path, monkeypatch)
    result = asyncio.run(module.terminal(owner, tmp_path / "batch"))
    assert result["phase"] == "passed" and result["checks"]["canonical_schema_validated"]
    assert len(calls) == 1 and not result["cleanup_pending"]
    report = json.loads((tmp_path / "batch/terminal-report.json").read_text())
    assert report["model_calls"] == 1 and report["sap_calls"] == report["authentication_calls"] == 0
    assert not report["operation_qualification_granted"] and not report["model_compatibility_registered"]


@pytest.mark.parametrize("patch", [{"input_kind": "null"}, {"input_field": ""}, {"source_complete": True},
    {"evidence_refs": ["invented"]}, {"presentation": {}}])
def test_returned_terminal_does_not_bypass_canonical_or_synthetic_check(module, tmp_path, monkeypatch, patch):
    owner, calls = manager_for_case(module, tmp_path, monkeypatch, patch=patch)
    result = asyncio.run(module.terminal(owner, tmp_path / "batch"))
    assert result["phase"] == "failed" and len(calls) == 1 and not result["checks"]
    report = json.loads((tmp_path / "batch/terminal-report.json").read_text())
    assert not report["operation_qualification_granted"] and not report["model_compatibility_registered"]


def test_failed_probe_cannot_be_redispatched(module, tmp_path, monkeypatch):
    owner, calls = manager_for_case(module, tmp_path, monkeypatch, failure="workbuddy_execution_failed")
    batch = tmp_path / "batch"
    result = asyncio.run(module.terminal(owner, batch))
    assert result["failure_code"] == "workbuddy_execution_failed" and len(calls) == 1
    with pytest.raises(AssertionError, match="already_attempted"):
        asyncio.run(module.terminal(owner, batch))
    assert len(calls) == 1


def test_changed_binding_stops_before_dispatch(module, tmp_path, monkeypatch):
    owner, calls = manager_for_case(module, tmp_path, monkeypatch)
    def changed(*_):
        raise AssertionError("verification_original_binding_changed")
    monkeypatch.setattr(module, "check_frozen", changed)
    with pytest.raises(AssertionError, match="binding_changed"):
        asyncio.run(module.terminal(owner, tmp_path / "batch"))
    assert calls == [] and not (tmp_path / "batch").exists()


def test_dry_run_creates_no_manager_or_model_task(module, monkeypatch, capsys):
    monkeypatch.setattr(module, "WorkBuddyManager", lambda *_: pytest.fail("No dry-run task"))
    monkeypatch.setattr(module.baseline.runner, "batch_path", lambda value: Path(value))
    monkeypatch.setattr(module.baseline.sys, "argv", ["contrast", "terminal", "--batch", "example"])
    assert module.main() == 0
    assert json.loads(capsys.readouterr().out)["model_calls"] == 0
