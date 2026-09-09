"""Opt-in, tool-free Codex smoke test on an in-memory copy of a managed draft.

Reads the source SQLite database in mode=ro. Does not save/modify the source,
Runtime settings or Git. Only hashes/statuses enter the optional local report.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import tempfile
import time
import uuid
from importlib.metadata import version
from pathlib import Path

from sap_business_agents_platform.agent_authoring import apply_package_edits
from sap_business_agents_platform.codex_planner import CodexPlanner, _agent_authoring_codex
from sap_business_agents_platform.sdk_manager import _normalize_model


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def verify(args):
    started = time.monotonic()
    if args.synthetic:
        metadata = {}
        original = {"manifest": {"slug": "isolated-test-agent", "module": "Common", "version": "0.1.0",
                     "title": {"zh": "合成测试", "en": "Synthetic test"}, "validation": {"verdict": "NOT_TESTED", "executable": False}},
                    "readme": "Synthetic fixture; no business data or SAP access.", "rules": None, "files": {}}
    else:
        if not args.database or not args.agent_id:
            raise ValueError("smoke_source_required")
        with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT d.metadata_json,r.package_json FROM agent_authoring_drafts d "
                "JOIN agent_authoring_revisions r ON r.draft_id=d.draft_id AND r.revision=d.revision "
                "WHERE d.agent_id=? AND d.status NOT IN ('published','cancelled')", (args.agent_id,),
            ).fetchall()
        if len(rows) != 1:
            raise ValueError("smoke_source_not_unique")
        metadata, original = json.loads(rows[0][0]), json.loads(rows[0][1])
    package = json.loads(json.dumps(original))
    package.pop("binary_files", None)
    private = set(metadata.get("private_source_files") or [])
    package["files"] = {key: value for key, value in package.get("files", {}).items() if key not in private}
    source_digest = digest(original)
    with tempfile.TemporaryDirectory(prefix="sapba-feedback-smoke-") as folder:
        async with _agent_authoring_codex(Path(folder)) as client:
            directory = await asyncio.wait_for(client.models(include_hidden=False), timeout=20)
        if directory.next_cursor:
            raise ValueError("model_catalog_incomplete")
        models = [normalized for item in directory.data if (normalized := _normalize_model(item))]
        model = next(item for item in models if item["model_id"] == "gpt-5.6-sol")
        effort = model["default_reasoning_effort"]
        if not effort or effort not in model["supported_reasoning_efforts"]:
            raise ValueError("runtime_reasoning_effort_invalid")
        planner = CodexPlanner(Path(folder), model="gpt-5.6-sol", reasoning_effort=effort)
        operation = "smoke-" + uuid.uuid4().hex
        task = asyncio.create_task(planner.review_agent_feedback(
            feedback="Only change manifest.title.zh to '隔离修改验证示例' and manifest.title.en to 'Isolated modification verification'. Preserve every other field, especially slug, validation and execution. Return revise_agent with two targeted edits. Do not use tools or access SAP.",
            locale="zh", package=package, operation_id=operation,
        ))
        done, _ = await asyncio.wait({task}, timeout=120)
        if not done:
            task.cancel()
            await asyncio.wait({task}, timeout=7)
            shutdown = asyncio.create_task(planner.abort_agent_feedback(operation))
            await asyncio.wait({shutdown}, timeout=3)
            raise ValueError("smoke_runtime_timeout")
        result = task.result()
        if result.get("action") != "revise_agent":
            raise ValueError("smoke_response_invalid")
        revised = apply_package_edits(package, result["edits"]) if "edits" in result else result["package"]
        expected = json.loads(json.dumps(package))
        expected["manifest"]["title"] = {"zh": "隔离修改验证示例", "en": "Isolated modification verification"}
        if revised != expected:
            raise ValueError("smoke_unexpected_changes")
        if not await planner.abort_agent_feedback(operation):
            raise ValueError("smoke_shutdown_unconfirmed")
    return {"verdict": "PASS", "source_kind": "synthetic" if args.synthetic else "authorized_local_draft_copy", "sdk_version": version("openai-codex"), "model": "gpt-5.6-sol",
            "reasoning_effort": effort, "source_digest": source_digest,
            "catalog_digest": digest(models), "response_digest": digest(revised),
            "source_unchanged": digest(original) == source_digest, "sap_calls": 0,
            "elapsed_seconds": round(time.monotonic() - started, 2)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic", action="store_true", help="Use only a literal synthetic fixture; never read local drafts")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--agent-id")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        report = asyncio.run(verify(args))
    except Exception as error:
        # Never print SDK messages, source package content or raw stderr.
        report = {"verdict": "FAIL", "error_code": getattr(error, "code", type(error).__name__)}
    if args.report:
        root = Path(__file__).resolve().parents[1] / ".local-data"
        if root not in args.report.resolve().parents:
            raise ValueError("smoke_report_path_invalid")
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report["verdict"] == "PASS" else 1)
