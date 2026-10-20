"""SDK-neutral report schemas, frozen transport and value-free repair feedback."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import uuid
from typing import Any

from .models import RunPresentation

VERSION = "sapba.report-contract/1"
HANDOFF_DEFINITION = (
    "For report handoff, 'unchanged presentation' means the successful platform-returned "
    "validated_report including its existing defaults and validation_ref, not the pre-normalization candidate."
)
MAX_CONTRACT_BYTES = 1_048_576
CHECKER_FILES = ("models.py", "acceptance_projection.py", "harness.py",
                "runtime_report_contract.py", "runtime_report_convergence.py")


def checker_binding():
    # Full responsibility files, never AST slices. No credentials or environment state.
    return {name: hashlib.sha256((Path(__file__).parent / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for name in CHECKER_FILES}


class ReportContractError(RuntimeError):
    def __init__(self, code="report_contract_changed"):
        self.code = code
        super().__init__(code)


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def presentation_schema(*, strict: bool = False) -> dict[str, Any]:
    source = RunPresentation.model_json_schema()
    definitions = source.get("$defs", {})

    def expand(node):
        if isinstance(node, list):
            return [expand(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            name = node["$ref"].removeprefix("#/$defs/")
            return expand({**definitions[name], **{k: v for k, v in node.items() if k != "$ref"}})
        result = {k: expand(v) for k, v in node.items() if k not in {"$defs", "title"}}
        # A properties member named 'title' is data, not Schema annotation.
        if "properties" in node:
            result["properties"] = {k: expand(v) for k, v in node["properties"].items()}
        if "const" in result:
            result["enum"] = [result.pop("const")]
        if strict:
            result.pop("default", None)
            if result.get("type") == "object":
                result["required"] = list(result.get("properties", {}))
        return result

    return expand(source)


def tool_schema(spec=None):
    properties = {"report": presentation_schema(),
        "tool_call_id": {"type": "string", "description": "Optional idempotency key."}}
    if spec is not None:
        from .acceptance_projection import AcceptanceProjectionSpec, projection_schema
        properties["acceptance_projection"] = projection_schema(AcceptanceProjectionSpec.model_validate(spec))
    return {"type": "object", "additionalProperties": False, "properties": properties,
        "required": ["report", *(["acceptance_projection"] if spec is not None else [])]}


def instructions(*, acceptance: bool = False) -> str:
    return (
        "Report handoff contract: sap_final_report_validate takes {\"report\": <presentation>}, "
        "not the complete final envelope. report.title is a bilingual {zh,en} object and "
        "report.blocks is an array of typed blocks, never an object. Follow its declared Schema. "
        "A synthetic shape example, not business evidence, is "
        '{"report":{"title":{"zh":"结构示例","en":"Shape example"},"blocks":[]}}. '
        "Do not use this empty example as a business answer. On failure, use validation_issues "
        "and repair_feedback to correct the report without inventing fields or evidence. "
        "A failed validation_ref is never usable. On success, use the returned validated_report "
        "as the unchanged presentation in the full final envelope, including its validation_ref. "
        "Existing defaults in that canonical report are authoritative. Report validation is "
        "not proof that native terminal handoff, cleanup or platform persistence has completed. "
        + HANDOFF_DEFINITION + " "
        + ("Acceptance projection is required only under this run's frozen acceptance contract."
           if acceptance else "Ordinary queries must omit acceptance_projection entirely.")
    )


def freeze(run_id, request_digest, runtime, spec=None):
    from .acceptance_projection import AcceptanceProjectionSpec
    spec = AcceptanceProjectionSpec.model_validate(spec).model_dump(mode="json") if spec is not None else None
    body = {"version": VERSION, "run_id": run_id, "request_digest": request_digest,
        "checker_binding": checker_binding(),
        "runtime_digest": digest(runtime), "acceptance_spec": spec,
        "input_schema": tool_schema(spec), "presentation_schema": presentation_schema(strict=True),
        "instructions": instructions(acceptance=spec is not None)}
    return {**body, "digest": digest(body)}


def verify(contract, *, run_id=None):
    if not isinstance(contract, dict) or contract.get("version") != VERSION:
        raise ReportContractError()
    body = {k: v for k, v in contract.items() if k != "digest"}
    if contract.get("digest") != digest(body) or run_id is not None and contract.get("run_id") != run_id:
        raise ReportContractError()
    if (contract.get("input_schema") != tool_schema(contract.get("acceptance_spec"))
            or contract.get("checker_binding") != checker_binding()
            or contract.get("presentation_schema") != presentation_schema(strict=True)
            or contract.get("instructions") != instructions(acceptance=contract.get("acceptance_spec") is not None)):
        raise ReportContractError()
    return contract


def tools_for_contract(tools, contract):
    verify(contract)
    result = copy.deepcopy(tools)
    for item in result:
        if item.get("name") == "sap_final_report_validate":
            item["inputSchema"] = copy.deepcopy(contract["input_schema"])
            item["description"] = "Validate the declared report structure and run-scoped business evidence. " + contract["instructions"]
    return result


def contract_file(data_root, run_id):
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
        raise ReportContractError("report_contract_binding_invalid")
    return Path(data_root) / "harness" / run_id / "report-contract.json"


def save_contract(data_root, contract):
    verify(contract)
    path = contract_file(data_root, contract["run_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_CONTRACT_BYTES:
        raise ReportContractError("report_contract_size_exceeded")
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(encoded)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(encoded).hexdigest(),
            "digest": contract["digest"]}


def load_contract(path, expected_sha256, *, run_id, root):
    expected = contract_file(root, run_id)
    path = Path(path)
    if path.is_symlink() or path.resolve() != expected.resolve() or not path.resolve().is_relative_to(Path(root).resolve()):
        raise ReportContractError("report_contract_location_invalid")
    try:
        with path.open("rb") as stream:
            encoded = stream.read(MAX_CONTRACT_BYTES + 1)
    except OSError:
        raise ReportContractError("report_contract_missing") from None
    if len(encoded) > MAX_CONTRACT_BYTES or hashlib.sha256(encoded).hexdigest() != expected_sha256:
        raise ReportContractError()
    try:
        return verify(json.loads(encoded), run_id=run_id)
    except (ValueError, TypeError):
        raise ReportContractError() from None


def contract_reference(data_root, run_id):
    path = contract_file(data_root, run_id)
    try:
        with path.open("rb") as stream:
            encoded = stream.read(MAX_CONTRACT_BYTES + 1)
    except OSError:
        raise ReportContractError("report_contract_missing") from None
    if len(encoded) > MAX_CONTRACT_BYTES:
        raise ReportContractError("report_contract_size_exceeded")
    sha = hashlib.sha256(encoded).hexdigest()
    contract = load_contract(path, sha, run_id=run_id, root=data_root)
    return {"path": str(path.resolve()), "sha256": sha, "digest": contract["digest"]}


def _field(schema, part):
    if schema.get("anyOf"):
        schema = next((s for s in schema["anyOf"] if s.get("type") != "null"), {})
    if schema.get("type") == "array":
        return schema.get("items", {}), str(part) if str(part).isdigit() else "<index>"
    fields = schema.get("properties", {})
    return fields.get(str(part), {}), str(part) if str(part) in fields else "<unknown>"


def schema_feedback(report, errors):
    """Only trusted schema metadata and input TYPE, never input VALUE."""
    result = []
    for error in errors[:50]:
        current, raw, path = presentation_schema(), report, ["report"]
        for part in error.get("loc") or []:
            current, safe = _field(current, part)
            path.append(safe)
            if isinstance(raw, dict):
                raw = raw.get(part)
            elif isinstance(raw, list) and isinstance(part, int) and 0 <= part < len(raw):
                raw = raw[part]
            else:
                raw = None
        expected = current.get("type") or [s.get("type", "declared_schema") for s in current.get("anyOf", [])] or "declared_schema"
        actual = "missing" if error.get("type") == "missing" else json_type(raw)
        result.append({"code": "acceptance_report_schema_invalid", "path": "/" + "/".join(path),
            "constraint": str(error.get("type") or "schema"), "expected": expected, "actual_type": actual,
            "message": "The report field does not satisfy the declared presentation schema.",
            "repair_action": "Correct this field to its declared type and constraints, then validate the report again. Do not submit the full final envelope as report."})
    return result


def json_type(value):
    return ("null" if value is None else "object" if isinstance(value, dict) else
            "array" if isinstance(value, list) else "boolean" if isinstance(value, bool) else
            "integer" if isinstance(value, int) else "number" if isinstance(value, float) else "string")


def repair_feedback(issues, *, acceptance=False):
    return {"report_validated": not issues, "issues": copy.deepcopy(issues),
        "instruction": instructions(acceptance=acceptance), "retry_scope": "report_only", "extends_deadline": False}
