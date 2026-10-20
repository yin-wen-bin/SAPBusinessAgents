"""Private no-SAP probe guards only; offline tests never qualify operations."""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.workbuddy_environment import atomic_json
from sap_business_agents_platform.workbuddy_site import make_configuration


@pytest.fixture
def module(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    spec = importlib.util.spec_from_file_location("formatter_terminal_test", root / "scripts/verify-workbuddy-formatter-terminal.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_full_probe_contract_preserves_shared_nullable_schema(module):
    from sap_business_agents_platform.runtime_harness_contract import _HARNESS_OUTPUT_SCHEMA
    from sap_business_agents_platform.acceptance_projection import output_schema
    before = copy.deepcopy(_HARNESS_OUTPUT_SCHEMA)
    schema, value = module.probe_contract()
    assert schema == output_schema(before, None) and before == _HARNESS_OUTPUT_SCHEMA
    result = {"text": json.dumps(value), "output_format": "native_json_schema",
              "actual_model": "hy4-preview", "model_identity_source": "assistant_message"}
    assert module.verify_terminal(result, schema)["literal_null_fields"] == ["input_kind", "input_field"]
    assert value["executed_plans"] == value["evidence_refs"] == [] and value["presentation"] is None


@pytest.mark.parametrize("patch", [{"input_kind": "null"}, {"input_field": ""}, {"source_complete": True},
    {"evidence_refs": ["invented-evidence"]}, {"input_kind": "secure_business_reference", "input_field": "receipt_reference"}])
def test_full_probe_rejects_enum_errors_and_business_evidence(module, patch):
    schema, value = module.probe_contract()
    with pytest.raises((ValueError, AssertionError)):
        module.verify_terminal({"text": json.dumps({**value, **patch}), "output_format": "native_json_schema",
            "actual_model": "hy4-preview", "model_identity_source": "assistant_message"}, schema)


def test_missing_terminal_or_unknown_identity_cannot_pass(module):
    schema, value = module.probe_contract()
    result = {"text": json.dumps(value), "output_format": "native_json_schema",
              "actual_model": "hy4-preview", "model_identity_source": "assistant_message"}
    for patch in ({"text": ""}, {"output_format": "text"}, {"model_identity_source": None}, {"actual_model": "alias"}):
        with pytest.raises((ValueError, AssertionError)):
            module.verify_terminal({**result, **patch}, schema)


def test_existing_attempt_blocks_before_worker_or_sap_dispatch(module, tmp_path, monkeypatch):
    atomic_json(tmp_path / "terminal-report.json", {"phase": "failed"})
    monkeypatch.setattr(module.runner, "require_idle", lambda *_: pytest.fail("Do not retry the attempt"))
    with pytest.raises(AssertionError, match="already_attempted"):
        asyncio.run(module.terminal(None, tmp_path))


def test_prepare_is_zero_calls_and_rejects_wrong_site_or_model(module, tmp_path, monkeypatch):
    monkeypatch.setattr(module.runner, "require_idle", lambda *_: None)
    monkeypatch.setattr(module, "controls", lambda: {"codex": "unchanged"})
    monkeypatch.setattr(module, "old_release_identity", lambda *_: {"sdk": "0.3.247", "cli": "2.141.0", "environment_digest": "old"})
    state = {**make_configuration("china", 1), "default_model_id": "hy4-preview", "capabilities": {}}
    owner = SimpleNamespace(environment=SimpleNamespace(state=lambda: state))
    result = module.prepare(owner, tmp_path / "batch")
    assert result["model_calls"] == result["sap_calls"] == 0
    frozen = json.loads((tmp_path / "batch/before.json").read_text())
    assert frozen["scope"] == "no_sap_formatter_probe" and not frozen["automatic_retry"]
    assert "controls" in frozen
    with pytest.raises(AssertionError, match="new_batch_required"):
        module.prepare(owner, tmp_path / "batch")
    state["default_model_id"] = "another-model"
    with pytest.raises(AssertionError, match="site_or_model_changed"):
        module.prepare(owner, tmp_path / "other")
    assert not (tmp_path / "other").exists()


def test_dry_run_never_instantiates_a_manager(module, monkeypatch, capsys):
    monkeypatch.setattr(module, "WorkBuddyManager", lambda *_: pytest.fail("No dry-run process or SDK"))
    monkeypatch.setattr(module.sys, "argv", ["probe", "terminal", "--batch", ".local-data/workbuddy-validation/dry"])
    assert module.main() == 0
    assert json.loads(capsys.readouterr().out)["model_calls"] == 0
