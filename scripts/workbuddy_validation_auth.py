"""Reuse verified, credential-free authentication metadata in isolated fixtures.

No login/check call, no fabricated status, and no mutation of the real account.
The normal RuntimeAuthenticationService still performs all binding/lease checks.
"""
import copy
import json
from pathlib import Path

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.workbuddy_environment import atomic_json, digest
from workbuddy_live_cases import live_guard

ACCOUNT_FIELDS = {"status", "authenticated", "identity", "method", "source", "checked_at",
    "last_successful_check", "revision", "failure_code", "confirmed_binding", "invalidation_pending",
    "service_site", "service_site_revision", "service_site_digest"}


def verified_account(manager, model_id="hy4-preview"):
    snapshot = live_guard.current_binding(manager, model_id)
    root = manager.environment.root.parents[2]
    source = Settings.from_env(root).data_root / "sdk-runtimes/authentication.json"
    account = json.loads(source.read_text(encoding="utf-8"))["accounts"]["workbuddy"]
    assert set(account).issubset(ACCOUNT_FIELDS), "verification_authentication_metadata_unknown"
    assert account["authenticated"] is True and account["status"] == "authenticated", "verification_authentication_required"
    assert account["revision"] == snapshot["authentication_binding_revision"], "verification_authentication_revision_changed"
    return copy.deepcopy(account)


def seed_isolated_authentication(settings, batch):
    from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
    batch = Path(batch).resolve()
    destination = settings.data_root / "sdk-runtimes/authentication.json"
    assert destination.resolve().is_relative_to(batch) and not destination.exists(), "verification_authentication_destination_invalid"
    manager = WorkBuddyManager(settings.repository_root)
    bound = live_guard.check_batch(manager, batch)
    from runtime_validation_cases import active_adapter
    scoped = active_adapter()
    frozen = scoped.manifest if scoped else json.loads((batch / "freeze.json").read_text(encoding="utf-8"))
    account = verified_account(manager, bound.get("model", "hy4-preview"))
    assert digest(account) == frozen["authentication_account_digest"], "verification_authentication_proof_changed"
    assert account["revision"] == bound.get("authentication_binding_revision", bound.get("authentication_revision")), "verification_authentication_revision_changed"
    # RuntimeAuthenticationService's persisted format stores no credentials or
    # URLs. Do not copy operation history, transient login URLs or other accounts.
    atomic_json(destination, {"version": 1, "accounts": {"workbuddy": account}, "operations": {}})
    return {"authentication_revision": account["revision"], "source_digest": digest(account),
            "existing_verified_proof_reused": True, "authentication_calls": 0}
