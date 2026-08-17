from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError


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
            try:
                for schema_name in ("input_schema", "output_schema"):
                    schema_path_name = f"{schema_name}_path"
                    if record.get(schema_path_name):
                        record[schema_name] = _load_trusted_schema(
                            self.skillhub_root, str(record[schema_path_name]), schema_name
                        )
            except SkillError:
                record["available"] = False
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
        _validate_json_contract(input_payload, skill.get("input_schema"), "input")
        if skill_id == "sap-adt-table-export":
            _validate_adt_input(input_payload)
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
                output_bytes = output_path.read_bytes()
                result = json.loads(output_bytes.decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SkillError(f"Skill {skill_id} did not write one JSON object to --output.") from exc
            if not isinstance(result, dict):
                raise SkillError(f"Skill {skill_id} returned an invalid result.")
            _validate_json_contract(result, skill.get("output_schema"), "output")
            if skill_id == "sap-adt-table-export":
                _validate_adt_output(result)
                manifest_hash = _validate_adt_manifest(output_path, output_bytes, result)
                result["artifacts"].append(
                    {"type": "output_manifest", "sha256": manifest_hash, "verified": True}
                )
        return result


def _load_trusted_schema(root: Path, relative_path: str, label: str) -> dict[str, Any]:
    path = (root / relative_path).resolve()
    if root != path and root not in path.parents:
        raise SkillError(f"Skill {label} path escapes SAPSkillhub root.")
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillError(f"Skill {label} could not be loaded from its trusted schema path.") from exc
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise SkillError(f"Skill {label} must be an object JSON Schema.")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise SkillError(f"Skill {label} JSON Schema is invalid.") from exc
    return schema


def _validate_json_contract(value: dict[str, Any], schema: Any, label: str) -> None:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise SkillError(f"Skill {label}_schema must be an object JSON Schema.")
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    except (SchemaError, ValidationError) as exc:
        path = ".".join(str(item) for item in getattr(exc, "absolute_path", []))
        suffix = f" at {path}" if path else ""
        raise SkillError(f"Skill {label} does not match its JSON Schema{suffix}.") from exc


_ADT_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,59}$")
_ADT_OBJECT = re.compile(r"^[A-Za-z][A-Za-z0-9_/]{0,59}$")
_ADT_PROFILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_ADT_FILTER_KEYS = {"field", "sign", "option", "operator", "value", "low", "high", "values"}
_ADT_OPERATORS = {"eq", "ne", "gt", "ge", "lt", "le", "bt", "in"}
_SENSITIVE_KEYS = {
    "url",
    "endpoint",
    "host",
    "client",
    "username",
    "user",
    "password",
    "credential",
    "credentials",
    "ca_path",
    "certificate",
    "verify_ssl",
    "sql",
}


def _validate_adt_input(value: dict[str, Any]) -> None:
    """Fail closed before invoking ADT with a strictly bounded declarative task."""

    if value.get("schema_version") != 1:
        raise SkillError("ADT input schema_version must be 1.")
    profile = value.get("connection_profile")
    if not isinstance(profile, str) or not _ADT_PROFILE.fullmatch(profile):
        raise SkillError("ADT connection_profile must be one fixed non-sensitive profile name.")
    if value.get("source_type") not in {"table", "cds"}:
        raise SkillError("ADT source_type must be table or cds.")
    object_name = value.get("object")
    if not isinstance(object_name, str) or not _ADT_OBJECT.fullmatch(object_name):
        raise SkillError("ADT object must be one static approved identifier.")
    fields = value.get("fields")
    if (
        not isinstance(fields, list)
        or not fields
        or len(fields) > 100
        or len(set(fields)) != len(fields)
        or any(not isinstance(field, str) or not _ADT_IDENTIFIER.fullmatch(field) for field in fields)
    ):
        raise SkillError("ADT fields must be a non-empty unique list of static identifiers.")
    filters = value.get("filters")
    if not isinstance(filters, list) or not filters or len(filters) > 20:
        raise SkillError("ADT filters must be non-empty and bounded.")
    for index, item in enumerate(filters):
        if not isinstance(item, dict) or set(item).difference(_ADT_FILTER_KEYS):
            raise SkillError(f"ADT filter {index} contains unsupported keys.")
        field = item.get("field")
        operator = str(item.get("operator") or item.get("option") or "").lower()
        if not isinstance(field, str) or not _ADT_IDENTIFIER.fullmatch(field):
            raise SkillError(f"ADT filter {index} field is invalid.")
        if operator not in _ADT_OPERATORS:
            raise SkillError(f"ADT filter {index} operator is not approved.")
        if ("operator" in item) == ("option" in item):
            raise SkillError(f"ADT filter {index} requires exactly one operator key.")
        if item.get("sign", "I") not in {"I", "E"}:
            raise SkillError(f"ADT filter {index} sign is invalid.")
        if "values" in item and (
            not isinstance(item["values"], list) or not item["values"] or len(item["values"]) > 100
        ):
            raise SkillError(f"ADT filter {index} values must be a non-empty bounded list.")
        if operator == "bt" and ("low" not in item or "high" not in item):
            raise SkillError(f"ADT filter {index} between requires low and high.")
        if operator not in {"bt", "in"} and not any(key in item for key in ("value", "low", "values")):
            raise SkillError(f"ADT filter {index} requires a typed value.")
    order_by = value.get("order_by", [])
    if not isinstance(order_by, list) or len(order_by) > 10:
        raise SkillError("ADT order_by must be a bounded array.")
    for index, item in enumerate(order_by):
        if isinstance(item, str):
            field, direction = item, "asc"
        elif isinstance(item, dict) and set(item).issubset({"field", "direction"}):
            field, direction = item.get("field"), str(item.get("direction") or "asc").lower()
        else:
            raise SkillError(f"ADT order_by {index} is invalid.")
        if not isinstance(field, str) or not _ADT_IDENTIFIER.fullmatch(field) or direction != "asc":
            raise SkillError("ADT order_by must use static ascending stable keys only.")
    max_rows = value.get("max_rows")
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or not 1 <= max_rows <= 10_000:
        raise SkillError("ADT max_rows must be between 1 and 10000.")
    _reject_sensitive_adt_keys(value)


def _reject_sensitive_adt_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                raise SkillError(f"ADT input contains prohibited key: {key}")
            _reject_sensitive_adt_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_sensitive_adt_keys(child)


def _validate_adt_output(value: dict[str, Any]) -> None:
    if value.get("schema_version") != 1 or value.get("skill_id") != "sap-adt-table-export":
        raise SkillError("ADT output identity is invalid.")
    if value.get("read_only") is not True:
        raise SkillError("ADT output did not attest read_only=true.")
    status = value.get("status")
    if status not in {"complete", "partial", "failed"}:
        raise SkillError("ADT output status is invalid.")
    completeness = value.get("completeness")
    issues = value.get("validation_issues")
    if not isinstance(completeness, dict) or not isinstance(issues, list):
        raise SkillError("ADT output completeness contract is invalid.")
    if status == "complete" and not (
        value.get("validated") is True
        and completeness.get("source_complete") is True
        and completeness.get("paging_complete") is True
        and not issues
    ):
        raise SkillError("ADT complete result lacks required validation or paging evidence.")
    rows = value.get("rows")
    if not isinstance(rows, list) or value.get("row_count") != len(rows):
        raise SkillError("ADT output row_count does not match returned rows.")


def _validate_adt_manifest(output_path: Path, output_bytes: bytes, result: dict[str, Any]) -> str:
    manifest_path = output_path.with_name(output_path.name + ".manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillError("ADT output is missing its adjacent SHA-256 manifest.") from exc
    expected = hashlib.sha256(output_bytes).hexdigest()
    if not isinstance(manifest, dict) or manifest.get("output_sha256") != expected:
        raise SkillError("ADT output SHA-256 manifest verification failed.")
    for key in ("skill_id", "run_id", "read_only", "status"):
        if manifest.get(key) != result.get(key):
            raise SkillError(f"ADT output manifest {key} does not match the result.")
    return expected
