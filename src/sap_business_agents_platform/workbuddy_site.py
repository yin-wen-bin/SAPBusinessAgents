"""SDK-free, versioned official service routing. Never an installation digest."""
from __future__ import annotations

import os
from .workbuddy_environment import WorkBuddyError, digest

FIELDS = ("service_site", "service_site_revision", "service_site_digest")
EXTERNAL_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CODEBUDDY_API_KEY",
                 "CODEBUDDY_AUTH_TOKEN", "CODEBUDDY_BASE_URL")
SITES = {
    "china": {"sdk_environment": "internal", "cli_env": {"CODEBUDDY_INTERNET_ENVIRONMENT": "internal"},
              "auth_hosts": ["copilot.tencent.com"], "account_url": "https://copilot.tencent.com/",
              "configuration_url": "https://www.codebuddy.cn/docs/cli/iam"},
    "international": {"sdk_environment": "external", "cli_env": {},
              "auth_hosts": ["codebuddy.ai"], "account_url": "https://www.codebuddy.ai/",
              "configuration_url": "https://www.codebuddy.ai/docs/cli/iam"},
}


def external_configuration() -> bool:
    return any(os.environ.get(key) for key in EXTERNAL_KEYS)


def make_configuration(site: str, revision: int) -> dict:
    if not isinstance(site, str) or site not in SITES or not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise WorkBuddyError("workbuddy_service_site_invalid")
    value = {"service_site": site, "service_site_revision": revision}
    value["service_site_digest"] = digest({"contract": "sapba.workbuddy.site/1", **value, **SITES[site]})
    return value


def configuration(state: dict, *, required: bool = False) -> dict:
    missing = not all(field in state for field in FIELDS)
    if missing:
        if any(field in state for field in FIELDS):
            raise WorkBuddyError("workbuddy_service_site_invalid")
        if required:
            raise WorkBuddyError("workbuddy_service_site_migration_required")
        return {**make_configuration("china", 0), "service_site_migration_required": True}
    value = make_configuration(state["service_site"], state["service_site_revision"])
    if value["service_site_digest"] != state["service_site_digest"]:
        raise WorkBuddyError("workbuddy_service_site_invalid")
    return value


def binding(value: dict) -> dict:
    return {field: value[field] for field in FIELDS}


def matches(value: dict, current: dict) -> bool:
    return not current.get("service_site_migration_required") and all(value.get(k) == current[k] for k in FIELDS)


def model_matches(check: dict, state: dict, environment=None) -> bool:
    if not matches(check, configuration(state)):
        return False
    if check.get('authentication_revision',0) == state.get('authentication_revision',0):
        return True
    if environment is not None:
        from .workbuddy_auth_recovery import model_check_matches
        return model_check_matches(environment,state,check)
    return False


def assert_binding(state: dict, snapshot: dict) -> dict:
    current = configuration(state, required=True)
    if not matches(snapshot, current):
        raise WorkBuddyError("workbuddy_service_site_changed")
    return current


def routing(current: dict) -> dict:
    expected = make_configuration(current.get("service_site"), current.get("service_site_revision"))
    if expected != binding(current):
        raise WorkBuddyError("workbuddy_service_site_invalid")
    return {**expected, **SITES[current["service_site"]]}
