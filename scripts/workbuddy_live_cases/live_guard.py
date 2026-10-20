"""Task-local monitor for the current batch, not a qualification policy."""
from __future__ import annotations

import json
from pathlib import Path

from sap_business_agents_platform.runtime_policy import orchestration_digest
from sap_business_agents_platform.workbuddy_diagnostics import safe_progress
from sap_business_agents_platform.workbuddy_environment import digest, file_hash
from workbuddy_validation_cases import frozen_runtime_binding

_batch = None
_operations = frozenset()
_modes = frozenset()


def binding_identity(value):
    changing = {"selected_at", "operation_capabilities", "runtime_configuration_revision", "configuration_digest"}
    return {key: item for key, item in value.items() if key not in changing}


def current_binding(manager, model_id="hy4-preview"):
    """Read existing platform checks only; never authenticate or probe a model."""
    from runtime_validation_cases import active_adapter
    adapter = active_adapter()
    if adapter:
        adapter.check_binding(adapter.manifest["runtime"])
        value = dict(adapter.manifest["runtime"])
        value["authentication_binding_revision"] = value["authentication_revision"]
        return value
    from sap_business_agents_platform.config import Settings
    from sap_business_agents_platform.workbuddy_site import matches
    root = manager.environment.root.parents[2]
    settings = Settings.from_env(root)
    account_state = json.loads((settings.data_root / "sdk-runtimes/authentication.json").read_text(encoding="utf-8"))
    account = account_state.get("accounts", {}).get("workbuddy", {})
    assert account.get("status") == "authenticated" and account.get("authenticated") is True, "verification_authentication_required"
    assert not account.get("invalidation_pending"), "verification_authentication_pending"
    assert not any(o.get("provider_id") == "workbuddy" and o.get("status") in {
        "queued", "running", "waiting_authentication", "cancelling", "cleanup_pending"
    } for o in account_state.get("operations", {}).values()), "verification_authentication_active"
    state = manager.environment.state()
    assert matches(account, manager.site_configuration(required=True)), "verification_authentication_site_changed"
    assert account.get("revision") == state.get("authentication_revision"), "verification_authentication_revision_changed"
    assert state.get("default_model_id") == "hy4-preview", "verification_default_model_changed"
    value = frozen_runtime_binding(manager.runtime_snapshot(model_id, require_enabled=False, formal=True))
    assert value["service_site"] == "china", "verification_china_site_required"
    assert value["model"] == value["actual_model"] == model_id, "verification_model_identity_changed"
    assert value["version"] == "0.3.247" and value["cli_version"] == "2.141.0", "verification_installation_changed"
    manager.environment.assert_verification_binding(value, formal=True)
    value["authentication_binding_revision"] = account["revision"]
    return value


def catalog_snapshot(root):
    from types import SimpleNamespace
    from sap_business_agents_platform.manifests import AgentRepository
    from sap_business_agents_platform.role_matching import RoleMatchingService
    # _catalog reads public definitions only. No database, runtime or SAP calls.
    return RoleMatchingService._catalog(SimpleNamespace(agents=AgentRepository(Path(root) / "agents")))


def check_batch(manager, batch):
    from runtime_validation_cases import active_adapter
    adapter = active_adapter()
    if adapter:
        if Path(batch).resolve() != adapter.batch.resolve():
            raise ValueError("validation_batch_binding_changed")
        adapter.check_binding(adapter.manifest["runtime"])
        return adapter.manifest["runtime"]
    batch = Path(batch)
    frozen = json.loads((batch / "freeze.json").read_text(encoding="utf-8"))
    bound = json.loads((batch / "runtime-snapshot.json").read_text(encoding="utf-8"))
    model_id = frozen.get("validation_model_id", "hy4-preview")
    assert bound["model"] == model_id, "verification_frozen_model_changed"
    assert binding_identity(current_binding(manager, model_id)) == binding_identity(bound), "verification_runtime_identity_changed"
    root = manager.environment.root.parents[2]
    assert all(file_hash(root / name) == value for name, value in frozen["controls"].get("files", {}).items()), "verification_codex_or_environment_changed"
    assert all(file_hash(root / "scripts" / name) == value for name, value in frozen["fixture_digests"].items()), "verification_fixture_changed"
    if "catalog_sha256" in frozen:
        assert digest(catalog_snapshot(root)) == frozen["catalog_sha256"], "verification_catalog_changed"
        saved = json.loads((batch / "catalog-snapshot.json").read_text(encoding="utf-8"))
        assert digest(saved) == frozen["catalog_sha256"], "verification_saved_catalog_changed"
    assert all(orchestration_digest(op) == value for op, value in frozen["operation_digests"].items()), "verification_orchestration_changed"
    assert not manager.snapshot()["enabled"], "verification_runtime_enabled"
    return bound


def configure(batch, operations, modes):
    global _batch, _operations, _modes
    _batch = Path(batch).resolve()
    _operations, _modes = frozenset(operations), frozenset(modes)


def attach_validation_monitor(manager, snapshot, destination):
    from runtime_validation_cases import active_adapter
    adapter = active_adapter()
    if adapter:
        return adapter.attach(manager, snapshot)
    if _batch is None:
        raise ValueError("verification_batch_not_configured")
    frozen = json.loads((_batch / "freeze.json").read_text(encoding="utf-8"))
    bound = json.loads((_batch / "runtime-snapshot.json").read_text(encoding="utf-8"))
    assert snapshot == bound and not manager.snapshot()["enabled"]
    assert not manager.supervisor.pending()
    assert all(orchestration_digest(op) == value for op, value in frozen["operation_digests"].items())
    assert check_batch(manager, _batch) == bound
    assert _operations.issubset(frozen.get("allowed_operations", _operations)), "verification_operation_out_of_scope"
    path = Path(destination).resolve()
    assert path.is_relative_to(_batch)
    path.parent.mkdir(parents=True, exist_ok=True)
    native = manager.supervisor.run
    register = manager.record_operation_validation

    def checked_registration(value, operation, **kwargs):
        assert value == bound and operation in _operations, "verification_qualification_scope_changed"
        if frozen.get("scope") == "china_non_sap":
            assert kwargs.get("evidence", {}).get("sap_calls") == 0, "verification_sap_access_forbidden"
        with manager.environment.maintenance():
            check_batch(manager, _batch)
            return register(value, operation, **kwargs)

    manager.record_operation_validation = checked_registration

    async def monitored(**kwargs):
        assert kwargs["snapshot"] == bound and kwargs["operation"] in _operations
        assert kwargs.get("mode", "bounded") in _modes
        check_batch(manager, _batch)
        if frozen.get("scope") == "china_non_sap":
            assert not kwargs.get("tool_handler") and not kwargs["payload"].get("tools"), "verification_sap_access_forbidden"
        downstream = kwargs.get("emit")

        def emit(kind, data):
            safe = safe_progress(kind, data)
            if safe:
                item = {"event": kind, "operation": kwargs["operation"], **safe}
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(item, ensure_ascii=True) + "\n")
                if kind == "workbuddy_native_failure":
                    print(json.dumps(item), flush=True)
            if downstream:
                downstream(kind, data)

        kwargs["emit"] = emit
        result = await native(**kwargs)
        check_batch(manager, _batch)
        assert result.get("actual_model") == bound["actual_model"]
        assert result.get("model_identity_source") == "assistant_message"
        return result

    manager.supervisor.run = monitored
