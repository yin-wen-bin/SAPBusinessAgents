from __future__ import annotations

import json
import uuid
from pathlib import Path

from .common import atomic_bytes, digest
from .inventory import discover
from .report import report


def render(store, output, plan_id=None, task_id=None):
    if task_id:
        task = store.get("task", task_id)
        payload = {"stage": "status", "task_id": task_id, "plan_digest": task["plan_digest"], "report": report(store, task_id)}
    elif plan_id:
        plan = store.get("plan", plan_id)
        payload = {"stage": "start", "plan_id": plan_id, "plan_digest": plan["digest"],
                   "request_id": "start:" + plan_id + ":" + uuid.uuid4().hex,
                   "selected": plan["selected"], "cases": [{k: c[k] for k in ("id", "feature", "scenario", "mode")} for c in plan["cases"]]}
    else:
        inventory = discover(store.repository)
        payload = {"stage": "select", "inventory_digest": inventory["digest"],
                   "features": [{**{k: f[k] for k in ("id", "title", "default", "operations", "operation_titles")},
                                 "children": [{"id": c["id"], "title": c["title"]} for c in f["children"]]}
                                for f in inventory["features"]]}
    payload["repository"] = str(store.repository)
    serialized = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = (Path(__file__).parent / "assets/card.html").read_text(encoding="utf-8")
    markup = template.replace("__ROOT_ID__", "sapba-test-" + digest(payload)[:16]).replace("__PAYLOAD__", serialized)
    atomic_bytes(Path(output), markup.encode("utf-8"))
    return {"path": str(Path(output).resolve()), "stage": payload["stage"], "payload": payload}
