from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sap_business_agents_platform.acceptance import (
    CanonicalTestCase,
    canonical_hash,
    validate_direct_baseline,
)


JsonObject = dict[str, Any]


def _load(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write(path: Path, value: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _validate_campaign(value: JsonObject) -> list[JsonObject]:
    if value.get("schema_version") != "1.0":
        raise ValueError("campaign schema_version must be 1.0")
    entries = value.get("agents")
    if not isinstance(entries, list) or not entries:
        raise ValueError("campaign agents must be a non-empty array")
    seen: set[str] = set()
    result: list[JsonObject] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"campaign agents[{index}] must be an object")
        required = {"module", "agent_id", "case", "baseline", "output"}
        allowed = {*required, "fixed_result"}
        if not required.issubset(entry) or not set(entry).issubset(allowed):
            raise ValueError(f"campaign agents[{index}] has unexpected or missing fields")
        agent_id = str(entry.get("agent_id") or "")
        if not agent_id or agent_id in seen:
            raise ValueError(f"campaign agents[{index}].agent_id is missing or duplicated")
        seen.add(agent_id)
        result.append(dict(entry))
    return result


def _state_entry(state: JsonObject, agent_id: str) -> JsonObject:
    agents = state.setdefault("agents", {})
    value = agents.setdefault(
        agent_id,
        {
            "phase": "pending",
            "attempts": 0,
            "baseline_hash": None,
            "free_run_id": None,
            "acceptance_path": None,
            "verdict": None,
            "last_error": None,
        },
    )
    return value


def _matched_free_run_id(artifact: JsonObject) -> str | None:
    free_result = artifact.get("free_query") if isinstance(artifact.get("free_query"), dict) else {}
    comparison = (
        free_result.get("comparison")
        if isinstance(free_result.get("comparison"), dict)
        else {}
    )
    run_id = str(free_result.get("run_id") or "")
    return run_id if run_id and comparison.get("verdict") == "MATCH" else None


def _terminal_acceptance(artifact: JsonObject, expected_hash: str) -> tuple[str, str] | None:
    verdict = str(artifact.get("verdict") or "")
    free = artifact.get("free_query") if isinstance(artifact.get("free_query"), dict) else {}
    fixed = artifact.get("fixed_agent") if isinstance(artifact.get("fixed_agent"), dict) else {}
    free_comparison = free.get("comparison") if isinstance(free.get("comparison"), dict) else {}
    fixed_comparison = fixed.get("comparison") if isinstance(fixed.get("comparison"), dict) else {}
    run_id = str(free.get("run_id") or "")
    if (
        verdict not in {"PASS", "BLOCKED"}
        or free_comparison.get("verdict") != "MATCH"
        or fixed_comparison.get("verdict") != "MATCH"
        or free_comparison.get("expected_hash") != expected_hash
        or fixed_comparison.get("expected_hash") != expected_hash
        or not run_id
        or (verdict == "BLOCKED" and not artifact.get("blocking_limitations"))
    ):
        return None
    return verdict, run_id


def run(args: argparse.Namespace) -> int:
    root = Path(args.repository).resolve()
    campaign_path = Path(args.campaign).resolve()
    campaign = _load(campaign_path)
    entries = _validate_campaign(campaign)
    state_path = Path(args.state).resolve()
    state = _load(state_path) if state_path.exists() else {
        "schema_version": "1.0",
        "campaign_hash": canonical_hash(campaign),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": None,
        "agents": {},
    }
    if state.get("campaign_hash") != canonical_hash(campaign):
        raise ValueError("campaign changed after state was created; use a new state file")

    failures = 0
    for entry in entries:
        if args.module and entry["module"] != args.module:
            continue
        agent_id = str(entry["agent_id"])
        if args.agent and agent_id != args.agent:
            continue
        item = _state_entry(state, agent_id)
        if args.reuse_only and not item.get("free_run_id"):
            continue
        case_path = (campaign_path.parent / str(entry["case"])).resolve()
        baseline_path = (campaign_path.parent / str(entry["baseline"])).resolve()
        output_path = (campaign_path.parent / str(entry["output"])).resolve()
        case = CanonicalTestCase.from_dict(_load(case_path))
        if case.agent_id != agent_id:
            raise ValueError(f"campaign entry {agent_id} does not match its case")
        baseline_payload = _load(baseline_path)
        baseline = validate_direct_baseline(baseline_payload, case)
        baseline_hash = canonical_hash(baseline_payload)
        if item.get("baseline_hash") not in {None, baseline_hash}:
            raise ValueError(f"frozen baseline changed for {agent_id}")
        item["baseline_hash"] = baseline_hash
        existing_acceptance = output_path / "acceptance.json"
        if existing_acceptance.exists():
            existing_artifact = _load(existing_acceptance)
            terminal = _terminal_acceptance(existing_artifact, canonical_hash(baseline))
            if terminal:
                item["verdict"], item["free_run_id"] = terminal
                item["acceptance_path"] = str(existing_acceptance)
                item["phase"] = "accepted" if item["verdict"] == "PASS" else "blocked"
                item["last_error"] = None
                continue
        if args.dry_run:
            continue

        item["phase"] = "running"
        item["attempts"] = int(item.get("attempts") or 0) + 1
        item["last_error"] = None
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write(state_path, state)
        command = [
            sys.executable,
            str(root / "scripts" / "run_three_stage_acceptance.py"),
            "--repository",
            str(root),
            "--module",
            str(entry["module"]),
            "--case",
            str(case_path),
            "--baseline",
            str(baseline_path),
            "--output",
            str(output_path),
            "--api-url",
            args.api_url,
            "--timeout",
            str(args.timeout),
        ]
        if item.get("free_run_id"):
            command.extend(["--free-run-id", str(item["free_run_id"])])
        if entry.get("fixed_result"):
            fixed_result_path = (campaign_path.parent / str(entry["fixed_result"])).resolve()
            command.extend(["--fixed-result", str(fixed_result_path)])
        completed = subprocess.run(
            command,
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        acceptance_path = output_path / "acceptance.json"
        item["acceptance_path"] = str(acceptance_path)
        terminal_blocked = False
        if acceptance_path.exists():
            artifact = _load(acceptance_path)
            item["verdict"] = artifact.get("verdict")
            # Reuse an expensive live free-query only after its semantic result
            # already MATCHes the frozen baseline. A mismatching query must be
            # rerun after platform repair rather than pinning stale evidence.
            item["free_run_id"] = _matched_free_run_id(artifact)
            terminal_blocked = bool(
                artifact.get("verdict") == "BLOCKED"
                and ((artifact.get("free_query") or {}).get("comparison") or {}).get("verdict") == "MATCH"
                and ((artifact.get("fixed_agent") or {}).get("comparison") or {}).get("verdict") == "MATCH"
                and artifact.get("blocking_limitations")
            )
            item["phase"] = (
                "accepted"
                if artifact.get("verdict") == "PASS"
                else "blocked"
                if terminal_blocked
                else "needs_adjudication"
            )
        else:
            item["verdict"] = "BLOCKED"
            item["phase"] = "blocked"
        effective_failure = completed.returncode != 0 and not terminal_blocked
        if effective_failure:
            failures += 1
            item["last_error"] = {
                "return_code": completed.returncode,
                "stdout_tail": completed.stdout[-2000:],
                "stderr_tail": completed.stderr[-2000:],
            }
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write(state_path, state)
        if effective_failure and not args.continue_on_failure:
            break

    if not args.dry_run:
        _write(state_path, state)
    print(
        json.dumps(
            {
                "campaign": str(campaign_path),
                "state": str(state_path),
                "agents": len(entries),
                "failures": failures,
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
        )
    )
    return 0 if failures == 0 else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a resumable three-stage live acceptance campaign.")
    parser.add_argument("--repository", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--module")
    parser.add_argument("--agent", help="Run one Agent from the campaign while preserving the same resumable gates.")
    parser.add_argument("--api-url", default="http://127.0.0.1:8765")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--continue-on-failure", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--reuse-only",
        action="store_true",
        help="Run only entries whose state already contains a semantically matched free-query run.",
    )
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
