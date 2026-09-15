"""Shared, side-effect-free Agent documentation projection and contract checks."""
from __future__ import annotations

import copy
from functools import lru_cache
import json
from pathlib import Path
import subprocess
from typing import Any


def _contract_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "agent-presentation-contract.json"


def _contract_version() -> str:
    return str(json.loads(_contract_path().read_text(encoding="utf-8")).get("version") or "unknown")


@lru_cache(maxsize=1)
def authoring_presentation_guidance() -> str:
    """Render the English machine-contract summary injected into every SDK authoring turn."""
    value = json.loads(_contract_path().read_text(encoding="utf-8"))
    requirements = ((value.get("authoring_requirements") or {}).get("en") or [])
    return "Agent presentation contract " + str(value.get("version") or "unknown") + ":\n" + "\n".join(
        f"- {item}" for item in requirements if isinstance(item, str) and item.strip()
    )


@lru_cache(maxsize=256)
def _inspect_cached(contract_version: str, serialized_manifest: str) -> dict[str, Any]:
    script = Path(__file__).resolve().parents[2] / "site" / "scripts" / "inspect-agent-presentation.mjs"
    try:
        result = subprocess.run(
            ["node", str(script)],
            input=json.dumps({"manifest": json.loads(serialized_manifest)}, ensure_ascii=False),
            encoding="utf-8",
            capture_output=True,
            timeout=20,
            check=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is None or result.returncode:
        return {
            "contract_version": "unavailable",
            "package_digest": None,
            "status": "invalid",
            "issues": [{
                "code": "presentation_contract_unavailable",
                "path": "",
                "severity": "error",
                "blocking": True,
                "message": {
                    "zh": "当前无法生成Agent展示资料，请稍后重试。",
                    "en": "The Agent presentation projection is currently unavailable. Retry later.",
                },
            }],
            "workflow": [],
            "odata_objects": [],
            "tables": [],
            "unknown_skill_objects": [],
            "tools": [],
            "scope_mode": "unavailable",
        }
    try:
        value = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        return {
            "contract_version": "unavailable",
            "package_digest": None,
            "status": "invalid",
            "issues": [{
                "code": "presentation_contract_unavailable",
                "path": "",
                "severity": "error",
                "blocking": True,
                "message": {
                    "zh": "Agent展示资料生成器返回了无效结果。",
                    "en": "The Agent presentation generator returned an invalid result.",
                },
            }],
            "workflow": [],
            "odata_objects": [],
            "tables": [],
            "unknown_skill_objects": [],
            "tools": [],
            "scope_mode": "unavailable",
        }
    if not isinstance(value, dict):
        raise TypeError("Agent presentation projection must be an object")
    return value


def inspect_agent_presentation(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical presentation projection for one immutable manifest value."""
    serialized = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return copy.deepcopy(_inspect_cached(_contract_version(), serialized))


def presentation_ready(manifest: dict[str, Any]) -> bool:
    return inspect_agent_presentation(manifest).get("status") == "ready"
