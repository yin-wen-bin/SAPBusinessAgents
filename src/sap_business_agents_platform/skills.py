from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


class SkillError(RuntimeError):
    pass


class SkillRegistry:
    """Allowlisted machine-callable SAPSkillhub contracts."""

    def __init__(self, skillhub_root: Path, allowlist_path: Path) -> None:
        self.skillhub_root = skillhub_root
        self.allowlist_path = allowlist_path

    def list(self) -> list[dict[str, Any]]:
        if not self.allowlist_path.exists():
            return []
        payload = json.loads(self.allowlist_path.read_text(encoding="utf-8"))
        records: list[dict[str, Any]] = []
        for raw in payload.get("skills", []):
            record = dict(raw)
            entrypoint = (self.skillhub_root / str(record.get("entrypoint") or "")).resolve()
            within_root = self.skillhub_root == entrypoint or self.skillhub_root in entrypoint.parents
            record["available"] = bool(within_root and entrypoint.is_file())
            record["entrypoint"] = str(entrypoint)
            has_contract = all(
                isinstance(record.get(name), dict) and record[name].get("type") == "object"
                for name in ("input_schema", "output_schema")
            )
            if (
                record.get("read_only") is True
                and record.get("validated") is True
                and has_contract
            ):
                records.append(record)
        return records

    def get(self, skill_id: str) -> dict[str, Any]:
        for item in self.list():
            if item.get("skill_id") == skill_id:
                return item
        raise KeyError(skill_id)

    async def execute(self, skill_id: str, input_payload: dict[str, Any]) -> dict[str, Any]:
        skill = self.get(skill_id)
        if not skill.get("available"):
            raise SkillError(f"Skill {skill_id} is allowlisted but its entrypoint is unavailable.")
        if skill.get("read_only") is not True or skill.get("validated") is not True:
            raise SkillError(f"Skill {skill_id} is not approved for read-only automation.")
        _validate_object_contract(input_payload, skill.get("input_schema"), "input")
        timeout = max(1, int(skill.get("timeout") or 300))
        with tempfile.TemporaryDirectory(prefix="sapba-skill-") as temporary:
            temporary_root = Path(temporary)
            input_path = temporary_root / "input.json"
            output_path = temporary_root / "output.json"
            input_path.write_text(
                json.dumps(input_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(skill["entrypoint"]),
                "--input",
                str(input_path),
                "--output",
                str(output_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                raise SkillError(f"Skill {skill_id} timed out.") from exc
            if process.returncode != 0:
                raise SkillError(
                    f"Skill {skill_id} failed with exit code {process.returncode}. "
                    "Its stderr was intentionally not persisted because it may contain sensitive data."
                )
            try:
                result = json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SkillError(f"Skill {skill_id} did not write one JSON object to --output.") from exc
        if not isinstance(result, dict):
            raise SkillError(f"Skill {skill_id} returned an invalid result.")
        _validate_object_contract(result, skill.get("output_schema"), "output")
        return result


def _validate_object_contract(
    value: dict[str, Any], schema: Any, label: str
) -> None:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise SkillError(f"Skill {label}_schema must be an object JSON Schema.")
    missing = [name for name in schema.get("required", []) if name not in value]
    if missing:
        raise SkillError(f"Skill {label} is missing required fields: {', '.join(missing)}")
    properties = schema.get("properties") or {}
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(value).difference(properties))
        if unknown:
            raise SkillError(f"Skill {label} contains unknown fields: {', '.join(unknown)}")
