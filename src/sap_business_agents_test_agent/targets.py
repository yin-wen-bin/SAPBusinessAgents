from __future__ import annotations

import secrets
from urllib.parse import urlsplit

import httpx

from .common import TestAgentError, now, write_json, read_json, digest


MARKER = "__sapba_auto_test_target.json"


def marker(store, task_id):
    from .workspace import prepare_target
    target = prepare_target(store, task_id)
    with store.transaction() as db:
        task = store.get("task", task_id, db)
        token = task.setdefault("target_token", secrets.token_hex(24))
        body = {"task_id": task_id, "token": token}
        write_json(target / "site/public" / MARKER, body)
        store.put("task", task_id, task, db)
        return body


def loopback(value):
    url = urlsplit(value)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"} or url.username or url.password:
        raise TestAgentError("target_must_be_loopback")
    if url.path not in {"", "/"} or url.query or url.fragment or not url.port or url.port < 1024:
        raise TestAgentError("target_origin_invalid")
    return value.rstrip("/")


def bind(store, task_id, site_url, api_url, client=None, native_identity=False):
    site_url, api_url = loopback(site_url), loopback(api_url)
    task = store.get("task", task_id)
    if not task.get("target_token"):
        raise TestAgentError("target_marker_required")
    owned = client is None
    client = client or httpx.Client(timeout=10, follow_redirects=False, trust_env=False)
    try:
        try:
            site = client.get(site_url + "/" + MARKER)
            api = client.get(api_url + ("/" + MARKER if native_identity else "/api/config/status"))
            site.raise_for_status()
            api.raise_for_status()
            proof, configuration = site.json(), api.json()
        except (httpx.HTTPError, ValueError):
            raise TestAgentError("isolated_target_unavailable") from None
        expected_data = (store.task_dir(task_id) / "targets" / str(task["round"]) / "source/.local-data").resolve()
        from pathlib import Path
        if proof != {"task_id": task_id, "token": task["target_token"]}:
            raise TestAgentError("site_isolation_not_proven")
        if Path(configuration.get("data_root", "")).resolve() != expected_data:
            raise TestAgentError("api_isolation_not_proven")
        if native_identity and (configuration.get("task_id") != task_id or configuration.get("token") != task["target_token"]
                                or configuration.get("source_digest") != task["round_source_digest"]):
            raise TestAgentError("api_source_identity_not_proven")
    finally:
        if owned:
            client.close()
    target = {"site_url": site_url, "api_url": api_url, "round": task["round"],
              "source_digest": task["round_source_digest"], "isolation_verified_at": now()}
    if native_identity:
        target["native_identity"] = True
    with store.transaction() as db:
        task = store.get("task", task_id, db)
        task["target"] = target
        store.put("task", task_id, task, db)
    return target


def recheck(store, task_id):
    task = store.get("task", task_id)
    target = task.get("target")
    if not target:
        raise TestAgentError("isolated_target_not_bound")
    if target.get("round") != task["round"] or target.get("source_digest") != task["round_source_digest"]:
        raise TestAgentError("isolated_target_stale")
    if target.get("profile") == "existing-platform-free-query":
        return bind_platform(store, task_id, target["site_url"], frozen_ui=bool(target.get("frozen_ui_root")))
    return bind(store, task_id, target["site_url"], target["api_url"], native_identity=target.get("native_identity", False))


def bind_platform(store, task_id, site_url, client=None, frozen_ui=False):
    """Reuse the configured platform for query-only scenarios, retaining owned UI.

    Lifecycle suites continue to require a disposable API/data root. The caller
    cannot supply a platform URL, credentials or a substitute SAP destination.
    """
    from sap_business_agents_platform.config import Settings
    from .state import task_plan
    task = store.get("task", task_id)
    plan = task_plan(store, task)
    if plan["selected"] != ["free-query"] or any(c["feature"] != "free-query" for c in plan["cases"]):
        raise TestAgentError("platform_reuse_requires_query_only_scope")
    if not task.get("target_token"):
        raise TestAgentError("target_marker_required")
    settings = Settings.from_env(store.repository)
    api_url, site_url = loopback(settings.internal_api_url), loopback(site_url)
    ui_root = None
    if frozen_ui:
        from pathlib import Path
        if site_url not in settings.local_ui_origins:
            raise TestAgentError("platform_ui_origin_not_approved")
        ui_root = store.task_dir(task_id) / "targets" / str(task["round"]) / "source/site/dist"
        proof = read_json(ui_root / MARKER)
        ui_digest = digest({p.relative_to(ui_root).as_posix(): digest(p.read_bytes()) for p in ui_root.rglob("*") if p.is_file()})
    owned = client is None
    client = client or httpx.Client(timeout=20, trust_env=False, follow_redirects=False)
    try:
        api = client.get(api_url + "/api/health")
        api.raise_for_status()
        health = api.json()
        if not frozen_ui:
            site = client.get(site_url + "/" + MARKER)
            site.raise_for_status()
            proof = site.json()
    except (httpx.HTTPError, ValueError):
        if owned:
            client.close()
        raise TestAgentError("existing_platform_unavailable") from None
    except Exception:
        if owned:
            client.close()
        raise
    try:
        if proof != {"task_id": task_id, "token": task["target_token"]}:
            raise TestAgentError("site_isolation_not_proven")
        if health.get("service") != "sap-business-agents-local" or health.get("loopback_only") is not True:
            raise TestAgentError("platform_identity_not_proven")
        startup = health.get("startup_binding", {})
        if startup.get("verified") is not True or not startup.get("boot_id"):
            raise TestAgentError("platform_startup_not_proven")
        previous = task.get("target", {})
        if previous.get("profile") == "existing-platform-free-query" and previous.get("boot_id") != startup["boot_id"]:
            raise TestAgentError("platform_binding_changed_reconcile_owned_runs")
    finally:
        if owned:
            client.close()
    if ui_root and previous.get("frozen_ui_digest") not in {None, ui_digest}:
        raise TestAgentError("platform_ui_build_changed")
    target = {"profile": "existing-platform-free-query", "site_url": site_url, "api_url": api_url,
              "round": task["round"], "source_digest": task["round_source_digest"],
              "boot_id": startup["boot_id"], "platform_source_digest": startup.get("source_digest"),
              "isolation_verified_at": now(), "query_only": True}
    if ui_root:
        target.update(frozen_ui_root=str(ui_root), frozen_ui_digest=ui_digest)
    with store.transaction() as db:
        task = store.get("task", task_id, db)
        task["target"] = target
        store.put("task", task_id, task, db)
    return target
