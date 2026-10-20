"""Pure, explicit historical review. No manager, SDK, writes or admission changes."""
import copy
from .workbuddy_auth_recovery import CONTRACT, _document, verified_model_check_origin
from .workbuddy_environment import digest, WorkBuddyError
from .workbuddy_site import configuration, matches


def propose(environment, state, authentication, *, original_identity, original_check):
    """Return a proposal and the safe current-identity artifact it requires.

    The caller archives this artifact in an ISOLATED evidence root and uses the
    normal receipt validator there. This function cannot apply a receipt.
    """
    try:
        check = _document(environment, original_check)
        original = _document(environment, original_identity)
        account = authentication["accounts"]["workbuddy"]
        binding = account.get("confirmed_binding") or {}
        site = configuration(state)
        operations = [op for op in authentication["operations"].values()
                      if op.get("provider_id") == "workbuddy"]
        latest = max(operations, key=lambda op: op.get("created_at", ""), default={})
        identity = state.get("authentication_identity_digest")
        if (state.get("authenticated") is not True or account.get("authenticated") is not True
                or account.get("status") != "authenticated" or account.get("failure_code")
                or account.get("invalidation_pending")
                or not identity or binding.get("identity_digest") != identity
                or binding.get("authenticated") is not True
                or binding.get("source") != "official_cli"
                or type(account.get("revision")) is not int
                or account.get("revision") != state.get("authentication_revision")
                or state.get("authentication_environment") != state.get("environment_digest")
                or not matches(binding, site)
                or original.get("authenticated") is not True or original.get("identity_digest") != identity):
            return {"decision": "blocked", "reason": "authentication_evidence_mismatch"}
        if (not latest or latest.get("action") != "login" or latest.get("status") != "completed"
                or latest.get("cleanup_complete") is not True or latest.get("failure_code")
                or not matches(latest.get("service_context", {}), site)
                or not latest.get("completed_at") or not account.get("checked_at")
                or latest["completed_at"] > account["checked_at"]
                or any(op.get("status") in {"queued", "running", "waiting_authentication", "cancelling", "cleanup_pending"}
                       for op in operations)):
            return {"decision": "blocked", "reason": "authentication_operation_unconfirmed"}
        if (check.get("compatible") is not True or not matches(check, site)
                or check.get("environment_digest") != state.get("environment_digest")
                or not isinstance(check.get("authentication_revision"), int)
                or isinstance(check.get("authentication_revision"), bool)
                or check["authentication_revision"] >= account["revision"]
                or not verified_model_check_origin(environment, check, check.get("model_id"))):
            return {"decision": "blocked", "reason": "original_probe_evidence_invalid"}
        observation = account.get("authentication_observation") or {}
        if not observation.get("context_digest") or not observation.get("source_digest"):
            return {"decision": "blocked", "reason": "authentication_observation_missing"}
        current = {
            "authenticated": True, "identity_digest": identity, "cleanup_complete": True,
            "authentication_revision": account["revision"], "environment_digest": state["environment_digest"],
            **site, "checked_at": account["checked_at"], "operation_id": latest["operation_id"],
            "authentication_snapshot_digest": digest(authentication),
            "workbuddy_snapshot_digest": digest(state),
            "observation_digest": digest(observation)}
        receipt = {"contract": CONTRACT, "source_revision": check["authentication_revision"],
                   "target_revision": account["revision"], **site,
                   "environment_digest": state["environment_digest"], "identity_digest": identity,
                   "original_identity": copy.deepcopy(original_identity),
                   "model_checks": {check["model_id"]: copy.deepcopy(original_check)}}
        return {"decision": "proposal_only", "reason": "same_identity_original_probe_verified",
                "receipt_fields": receipt, "current_identity_artifact": current,
                "requirements": ["fresh_binding_review", "maintenance_authorization",
                                 "no_active_or_cleanup_pending_jobs", "target_infrastructure_certification"]}
    except (KeyError, OSError, ValueError, TypeError, WorkBuddyError):
        return {"decision": "blocked", "reason": "evidence_missing_or_modified"}
