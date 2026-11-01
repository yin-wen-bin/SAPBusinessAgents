"""SDK-neutral report schemas, frozen transport and value-free repair feedback."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import uuid
from functools import lru_cache
from typing import Any

from .models import RunPresentation, RunPresentationV2

VERSION = "sapba.report-contract/2"
HANDOFF_DEFINITION = (
    "For report handoff, 'unchanged presentation' means the successful platform-returned "
    "validated_report including its existing defaults and validation_ref, not the pre-normalization candidate."
)
MAX_CONTRACT_BYTES = 1_048_576
CHECKER_FILES = ("models.py", "acceptance_projection.py", "harness.py",
                "runtime_report_contract.py", "runtime_report_convergence.py",
                "runtime_query_contract.py", "runtime_money.py", "source_semantics.py", "source_reassessment.py", "runtime_evidence_bindings.py",
                "runtime_harness_contract.py", "runtime_harness_result.py", "runtime_terminal_contract.py", "mcp_server.py")
CHECKER_FILES += ("report_verification.py", "report_scope.py", "runtime_relations.py")
CHECKER_FILES += ("runtime_strict_schema.py",)
CHECKER_FILES += ("runtime_query_clock.py", "financial_classification.py")


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


def presentation_schema(*, strict: bool = False, version=1) -> dict[str, Any]:
    # Pure code-defined schemas only, not credentials, evidence or admission.
    # Callers get their own copy so SDKs cannot mutate another request's schema.
    return copy.deepcopy(_presentation_schema(strict=strict, version=version))


@lru_cache(maxsize=4)
def _presentation_schema(*, strict: bool = False, version=1) -> dict[str, Any]:
    source = (RunPresentationV2 if version == 2 else RunPresentation).model_json_schema()
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


def supported_presentation_schema(*, strict=False):
    return {"anyOf": [presentation_schema(strict=strict), presentation_schema(strict=strict, version=2)]}


def tool_schema(spec=None):
    properties = {"report": supported_presentation_schema(),
        "tool_call_id": {"type": "string", "description": "Optional idempotency key."}}
    if spec is not None:
        from .acceptance_projection import AcceptanceProjectionSpec, projection_schema
        properties["acceptance_projection"] = projection_schema(AcceptanceProjectionSpec.model_validate(spec))
    return {"type": "object", "additionalProperties": False, "properties": properties,
        "required": ["report", *(["acceptance_projection"] if spec is not None else [])]}


def instructions(*, acceptance: bool = False) -> str:
    return (
        'For new reports use schema_version="2.0". Bind table cells using row.value_bindings keyed by column key. '
        'A source binding has kind=source, evidence_ref, the complete source_key, source_field and the exact currency_field for an amount. '
        'A calculation binding has kind=calculation, calculation_ref, calculation_item, calculation_field and currency. '
        'For a complete financial review, a reliable minimal report is a table of ALL original rows plus bound metrics; '
        'business identity fields belong in bound table columns. Keep every requested raw field in the evidence. '
        'sap_money_aggregate returns row_sets alongside metrics. For a full-source table set row_set_binding to '
        '{"kind":"calculation","calculation_ref":"<returned calculation_ref>",'
        '"calculation_item":"<row_sets item id>","calculation_field":"count","currency":null}. '
        'For a subset table use an existing calculation metric whose row_keys_digest proves EXACTLY those rows; '
        'never bind a full set to a subset or invent a mode=all/source row-set binding. '
        'Table cell strings must be the exact owned canonical values in both locales: preserve source date strings '
        'and use an empty string for null. Do not reformat dates or replace empty values with prose. '
        'Only include a factual text/notice when its declared fact_rule proves the ENTIRE statement. '
        'For open_items_exist the exact bilingual text_template is '
        '{"zh":"存在未清项目，数量为 {{count}}。","en":"Open items exist; count: {{count}}."}; '
        'bind count to an owned open_at_cutoff calculation count. Other narrative, including identity sentences '
        'and method notices, remains unproven even with value slots; use bound table cells and metric labels instead. '
        'Do not relabel an unproven statement to evade verification. '
        'The financial-classification evidence_ref is a configuration proof handle, not a business-row table; '
        'recompute money on its source_evidence_ref. In a continuation create current-run reads and proofs; '
        'prior-run references are history, never current evidence. '

        'For text/notice use text_template with {{name}} slots in BOTH locales and value_bindings keyed by name; '
        'supply text equal to that template, or copy the canonical rendered text on handoff. Never invent prose amounts. '
        'verification is host-owned: omit it on a candidate, then copy the entire validated_report unchanged. '
        'Unstructured narrative remains a business-proof limitation; changing claim_scope cannot verify it. '
        'Question scope is frozen: an extra filter cannot silently narrow the answer. Request a user clarification for an answer_scope change. '
        'Cross-service identities require reviewed mappings or sap_relation_resolve with owned complete evidence and authoritative metadata; never infer Supplier=InvoicingParty. '
        "Report handoff contract: sap_final_report_validate takes {\"report\": <presentation>}, "
        "not the complete final envelope. report.title is a bilingual {zh,en} object and "
        "report.blocks is an array of typed blocks, never an object. Follow its declared Schema. "
        "Use only declared properties; an optional report property need not be emitted when it "
        "does not apply. Do not invent additional attributes for a block type. The successful "
        "validated_report supplies the existing defaults required by the strict final envelope. "
        "A synthetic shape example, not business evidence, is "
        '{"report":{"title":{"zh":"结构示例","en":"Shape example"},"blocks":[]}}. '
        "Do not use this empty example as a business answer. On failure, use validation_issues "
        "and repair_feedback to correct the report without inventing fields or evidence. "
        "A failed validation_ref is never usable. On success, use the returned validated_report "
        "with ALL block fields (even empty arrays and null defaults); never regenerate a shorter copy. "
        "as the unchanged presentation in the full final envelope, including its validation_ref. "
        "Existing defaults in that canonical report are authoritative. Report validation is "
        "not proof that native terminal handoff, cleanup or platform persistence has completed. "
        + HANDOFF_DEFINITION + " "
        + "Financial metrics must use sap_money_aggregate and bind calculation_ref, calculation_item, "
          "calculation_field and currency to its exact canonical values in both locales. "
        "A count has currency=null. Put only the exact numeric string in value.zh/value.en, "
        "without thousands separators, currency suffixes or units. Do not normalize guessed values. "
        "Omit financial_verification on new candidates (default not_requested); the platform derives it. "
        "Business prose/tables are not arithmetic proofs even when all metric bindings pass. "
        "Use financial_verification_details to understand the scope; never claim the full report is verified. "
          "Do not recompute money in prose or present unverified amounts as verified. "
        + ("Acceptance projection is required only under this run's frozen acceptance contract."
           if acceptance else "Ordinary queries must omit acceptance_projection entirely.")
    )


def freeze(run_id, request_digest, runtime, spec=None):
    from .acceptance_projection import AcceptanceProjectionSpec
    from .runtime_query_contract import frozen_contract
    spec = AcceptanceProjectionSpec.model_validate(spec).model_dump(mode="json") if spec is not None else None
    body = {"version": VERSION, "run_id": run_id, "request_digest": request_digest,
        "checker_binding": checker_binding(),
        "runtime_digest": digest(runtime), "acceptance_spec": spec,
        "input_schema": tool_schema(spec), "presentation_schema": supported_presentation_schema(strict=True),
        "instructions": instructions(acceptance=spec is not None),
        "query_contract": frozen_contract()}
    return {**body, "digest": digest(body)}


def verify(contract, *, run_id=None):
    if not isinstance(contract, dict) or contract.get("version") != VERSION:
        raise ReportContractError()
    body = {k: v for k, v in contract.items() if k != "digest"}
    if contract.get("digest") != digest(body) or run_id is not None and contract.get("run_id") != run_id:
        raise ReportContractError()
    from .runtime_legacy_protocol import supported
    if (contract.get("input_schema") != tool_schema(contract.get("acceptance_spec"))
            or (contract.get("checker_binding") != checker_binding() and not supported(contract.get("checker_binding")))
            or contract.get("presentation_schema") != supported_presentation_schema(strict=True)
            or contract.get("instructions") != instructions(acceptance=contract.get("acceptance_spec") is not None)):
        raise ReportContractError()
    from .runtime_query_contract import frozen_contract
    if contract.get("query_contract") != frozen_contract():
        raise ReportContractError("query_contract_changed")
    return contract


def tools_for_contract(tools, contract):
    verify(contract)
    result = copy.deepcopy(tools)
    for item in result:
        if item.get("name") in {"sap_query_validate", "sap_query_execute"}:
            from .runtime_query_contract import tool_schema as query_schema
            item["inputSchema"] = query_schema()
            item["description"] += " " + contract["query_contract"]["instructions"]
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
        current, raw, path = presentation_schema(version=2 if isinstance(report, dict) and report.get('schema_version') == '2.0' else 1), report, ["report"]
        parent, parent_path = current, list(path)
        for part in error.get("loc") or []:
            parent, parent_path = current, list(path)
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
        issue = {"code": "acceptance_report_schema_invalid", "path": "/" + "/".join(path),
            "constraint": str(error.get("type") or "schema"), "expected": expected, "actual_type": actual,
            "message": "The report field does not satisfy the declared presentation schema.",
            "repair_action": "Correct this field to its declared type and constraints, then validate the report again. Do not submit the full final envelope as report."}
        if error.get("type") == "extra_forbidden":
            if parent.get("anyOf"):
                parent = next((s for s in parent["anyOf"] if s.get("type") == "object"), {})
            issue.update(expected="absent", object_location="/" + "/".join(parent_path),
                allowed_properties=list(parent.get("properties", {})),
                repair_action="Remove undeclared properties from the object at object_location; "
                    "allowed_properties lists its complete trusted field names. Keep declared content "
                    "and evidence unchanged, then validate again. Do not infer a replacement property "
                    "or change business conclusions. No input key or value is reproduced in this feedback.")
        elif current.get("enum") is not None:
            issue["allowed_values"] = copy.deepcopy(current["enum"])
        elif error.get("type") == "value_error":
            # Only fixed checker-owned shape messages are safe. Never echo an
            # arbitrary exception message, context, candidate key or value.
            messages = {"Value error, presentation table rows must match the declared column count",
                "Value error, presentation table total_rows cannot be smaller than displayed rows"}
            messages.update("Value error, presentation block " + kind + " has no display content"
                for kind in presentation_schema()["properties"]["blocks"]["items"]["properties"]["type"]["enum"])
            if error.get("msg") in messages:
                issue["message"] = error["msg"].removeprefix("Value error, ")
                issue["repair_action"] = "Correct the stated block-content or table-shape constraint using verified evidence only. " \
                    "Do not invent facts, change conclusions, omit required records or repeat external reads. Then validate again."
        result.append(issue)
    return result


def json_type(value):
    return ("null" if value is None else "object" if isinstance(value, dict) else
            "array" if isinstance(value, list) else "boolean" if isinstance(value, bool) else
            "integer" if isinstance(value, int) else "number" if isinstance(value, float) else "string")


def repair_feedback(issues, *, acceptance=False):
    if not issues:
        return {"report_validated": True, "issues": [],
            "instruction": "Report validation passed. Hand off the full final object now using the exact "
                "validated_report as presentation, including its validation_ref and ALL existing defaults. "
                "Do not reword or revalidate a passed report merely to remove a financial warning. "
                "An unverified financial status is a retained limitation, not a report-structure rejection; "
                "do not claim business completeness. Report validation alone is not a complete terminal. "
                "Do not repeat external reads. Revalidation does not restart the handoff window.",
            "retry_scope": "terminal_only", "extends_deadline": False}
    return {"report_validated": not issues, "issues": copy.deepcopy(issues),
        "instruction": instructions(acceptance=acceptance), "retry_scope": "report_only", "extends_deadline": False}
