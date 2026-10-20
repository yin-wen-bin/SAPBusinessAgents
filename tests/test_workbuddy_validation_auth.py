"""Real authentication guards over synthetic metadata; no SDK/model/SAP I/O."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import workbuddy_validation_auth as module
    from sap_business_agents_platform.workbuddy_site import make_configuration
    from sap_business_agents_platform.workbuddy_environment import atomic_json, digest
    from sap_business_agents_platform.runtime_authentication import RuntimeAuthenticationService
    site = make_configuration("china", 1)
    account = {"revision": 4, "status": "authenticated", "authenticated": True, **site}
    source = tmp_path / ".local-data/sdk-runtimes/authentication.json"
    atomic_json(source, {"version": 1, "accounts": {"workbuddy": account}, "operations": {}})
    binding = {"authentication_binding_revision": 4, **site}
    batch = tmp_path / ".local-data/validation/batch"
    atomic_json(batch / "freeze.json", {"authentication_account_digest": digest(account)})
    manager = SimpleNamespace(environment=SimpleNamespace(root=tmp_path / ".local-data/runtimes/workbuddy"))
    monkeypatch.setattr(module.Settings, "from_env", lambda *_: SimpleNamespace(data_root=tmp_path / ".local-data"))
    monkeypatch.setattr(module.live_guard, "current_binding", lambda *_: copy.deepcopy(binding))
    monkeypatch.setattr(module.live_guard, "check_batch", lambda *_: copy.deepcopy(binding))
    from sap_business_agents_platform import workbuddy_manager
    monkeypatch.setattr(workbuddy_manager, "WorkBuddyManager", lambda *_: manager)
    settings = SimpleNamespace(data_root=batch / "data", repository_root=tmp_path)
    return SimpleNamespace(module=module, account=account, source=source, batch=batch, binding=binding,
        settings=settings, save=atomic_json, Authentication=RuntimeAuthenticationService)


def test_isolated_authentication_reuses_actual_record_and_keeps_real_checks(context):
    c = context
    before = c.source.read_bytes()
    proof = c.module.seed_isolated_authentication(c.settings, c.batch)
    assert proof["authentication_calls"] == 0 and proof["authentication_revision"] == 4
    assert c.source.read_bytes() == before
    account = json.loads((c.settings.data_root / "sdk-runtimes/authentication.json").read_text(encoding="utf-8"))
    assert account["accounts"]["workbuddy"] == c.account and account["operations"] == {}
    adapter = SimpleNamespace(context=lambda: {k: c.binding[k] for k in (
        "service_site", "service_site_revision", "service_site_digest")})
    auth = c.Authentication(c.settings.data_root / "sdk-runtimes", {"workbuddy": adapter})
    async def use():
        async with auth.use("workbuddy", c.binding):
            assert auth.leases["workbuddy"] == 1
    asyncio.run(use())
    assert auth.leases["workbuddy"] == 0
    with pytest.raises(Exception, match="Authentication operation"):
        auth.assert_binding("workbuddy", {**c.binding, "authentication_binding_revision": 5})


@pytest.mark.parametrize("patch", [{"authenticated": False}, {"revision": 5}, {"token": "must-not-copy"}])
def test_unverified_changed_or_credential_metadata_never_seeds_an_account(context, patch):
    c = context
    c.save(c.source, {"version": 1, "accounts": {"workbuddy": {**c.account, **patch}}, "operations": {}})
    with pytest.raises(AssertionError):
        c.module.seed_isolated_authentication(c.settings, c.batch)
    assert not (c.settings.data_root / "sdk-runtimes/authentication.json").exists()


def test_seed_cannot_overwrite_proof_or_write_outside_batch(context):
    c = context
    c.module.seed_isolated_authentication(c.settings, c.batch)
    with pytest.raises(AssertionError, match="destination_invalid"):
        c.module.seed_isolated_authentication(c.settings, c.batch)
    c.settings.data_root = c.source.parent.parent
    with pytest.raises(AssertionError, match="destination_invalid"):
        c.module.seed_isolated_authentication(c.settings, c.batch)


def test_isolated_authentication_checks_the_frozen_explicit_model(context, monkeypatch):
    c = context
    c.binding["model"] = "deepseek-v4.1-flash"
    models = []
    def current(_, model):
        models.append(model)
        return copy.deepcopy(c.binding)
    monkeypatch.setattr(c.module.live_guard, "current_binding", current)
    c.module.seed_isolated_authentication(c.settings, c.batch)
    assert models == ["deepseek-v4.1-flash"]
