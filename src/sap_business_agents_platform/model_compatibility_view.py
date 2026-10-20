"""Read-only presentation of provider decisions; never grants admission."""


def projection(*, check, current, authenticated, ready=True, recovery_pending=False,
               stale=False, reason=None):
    if not ready:
        status, reason = "blocked", reason or "runtime_prerequisite_required"
    elif authenticated is not True:
        status, reason = "authentication_required", "runtime_authentication_check_required"
    elif recovery_pending:
        status, reason = "recovery_pending", "authentication_binding_review_required"
    elif current and check.get("compatible") is True:
        status, reason = "reusable", "applicable_check"
    elif current and check:
        status, reason = "failed", "compatibility_check_failed"
    else:
        status, reason = "check_required", reason or (
            "compatibility_binding_changed" if stale else "compatibility_check_missing")
    return {"status": status, "reason": reason,
            "check_contract": check.get("probe_contract", "legacy") if check else None,
            "checked_at": check.get("checked_at") if check else None}


def workbuddy_projection(state, item, *, ready):
    # This is a pending review hint, not a claim that recovery is safe. The
    # explicit offline audit must verify the original job and identity evidence.
    check = item["check"]
    pending = (check.get("status") == "authentication_changed"
               and check.get("authentication_revision") != state.get("authentication_revision")
               and check.get("environment_digest") == state.get("environment_digest")
               and check.get("service_site_digest") == state.get("service_site_digest")
               and any(past.get("compatible") is True
                       and past.get("check_digest") == check.get("check_digest")
                       and past.get("model_id") == item["model_id"]
                       for past in state.get("model_check_history", []) if isinstance(past, dict)))
    return projection(check=check, current=not item["check_stale"],
                      authenticated=state.get("authenticated"), ready=ready,
                      recovery_pending=pending, stale=item["check_stale"])
