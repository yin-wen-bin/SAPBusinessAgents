from __future__ import annotations

import secrets
from urllib.parse import urlsplit

import httpx

from .common import TestAgentError, now, write_json


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


def bind(store, task_id, site_url, api_url, client=None):
    site_url, api_url = loopback(site_url), loopback(api_url)
    task = store.get("task", task_id)
    if not task.get("target_token"):
        raise TestAgentError("target_marker_required")
    owned = client is None
    client = client or httpx.Client(timeout=10, follow_redirects=False, trust_env=False)
    try:
        site = client.get(site_url + "/" + MARKER)
        api = client.get(api_url + "/api/config/status")
        site.raise_for_status()
        api.raise_for_status()
        proof, configuration = site.json(), api.json()
        expected_data = (store.task_dir(task_id) / "targets" / str(task["round"]) / "source/.local-data").resolve()
        from pathlib import Path
        if proof != {"task_id": task_id, "token": task["target_token"]}:
            raise TestAgentError("site_isolation_not_proven")
        if Path(configuration.get("data_root", "")).resolve() != expected_data:
            raise TestAgentError("api_isolation_not_proven")
    finally:
        if owned:
            client.close()
    target = {"site_url": site_url, "api_url": api_url, "round": task["round"],
              "source_digest": task["round_source_digest"], "isolation_verified_at": now()}
    with store.transaction() as db:
        task = store.get("task", task_id, db)
        task["target"] = target
        store.put("task", task_id, task, db)
    return target
