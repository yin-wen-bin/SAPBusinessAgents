"""Shared compiler proposal contract; never alters fixed bindings."""
import json

from .runtime_diagnostics import checked_output
from .runtime_contract import RuntimeContractError as WorkBuddyError

VERSION = "1.0"
TEXT = {"type": "object", "properties": {"zh": {"type": "string"}, "en": {"type": "string"}},
        "required": ["zh", "en"], "additionalProperties": False}
STRINGS = {"type": "array", "items": {"type": "string"}}
BINDING = {"type": "object", "properties": {key: {"type": "string", "minLength": 1}
           for key in ("input_port", "source_stage_id", "source_output_port")},
           "required": ["input_port", "source_stage_id", "source_output_port"], "additionalProperties": False}
STAGE = {"type": "object", "required": ["id", "agent_id", "confidence", "capability", "bindings", "requested_outputs"],
         "additionalProperties": False, "properties": {
             "id": {"type": "string", "minLength": 1}, "agent_id": {"type": ["string", "null"]},
             "confidence": {"enum": ["high", "medium", "low"]}, "capability": TEXT, "reason": TEXT,
             "bindings": {"type": "array", "items": BINDING}, "requested_outputs": STRINGS,
             "gap_title": TEXT, "gap_description": TEXT, "required_inputs": {"type": "array", "items": {"type": "object"}},
             "required_outputs": {"type": "array", "items": {"type": "object"}},
             "guardrails": {"type": "object"}, "acceptance": TEXT}}
PROPOSAL_SCHEMA = {"type": "object", "required": ["title", "stages"], "additionalProperties": False,
                   "properties": {"title": TEXT, "description": TEXT, "intent": TEXT,
                       "stages": {"type": "array", "minItems": 1, "items": STAGE},
                       "validation_defaults": {"type": "object"},
                       **{key: {"type": "array", "items": {"type": "object"}}
                          for key in ("integration_inputs", "output_actions", "integration_gaps")}}}
EXPECTATIONS_SCHEMA = {"type": "array", "items": {"type": "object", "required": ["output", "operator"],
    "properties": {"output": {"type": "string"}, "operator": {"enum": ["exists", "non_empty", "equals", "one_of", "decimal_within"]},
        "expected": {}, "tolerance": {"type": ["string", "number"]}}, "additionalProperties": False}}


def instructions(*, feedback=False):
    example = {"title": {"zh": "业务检查", "en": "Business check"}, "description": {"zh": "只读检查", "en": "Read-only check"},
        "stages": [{"id": "check", "agent_id": "<exact supplied Agent ID>", "confidence": "high",
            "capability": {"zh": "检查", "en": "Check"}, "reason": {"zh": "已核对能力", "en": "Capability verified"},
            "bindings": [], "requested_outputs": ["<declared business output port>"]}]}
    return ("Compiler proposal contract " + VERSION + ": proposal_json encodes the COMPLETE compiler proposal, not a "
        "workflow graph or JSON Patch. Each stage must explicitly declare confidence: high only for an exact "
        "catalog capability match; otherwise medium/low with a genuine gap. Do not invent confidence to force selection. "
        "The platform compiler alone pins versions/digests/schemas from the supplied catalog. Do not emit those fields. "
        "Empty bindings expose declared Agent inputs as workflow inputs. Explicit bindings contain input_port, "
        "source_stage_id and source_output_port, using compatible declared ports from an earlier stage. "
        "Integration fields use the existing supplied integration catalog and binding_id; never invent native tools. "
        "For clarification, proposal_json may encode {} instead of an executable proposal.\n" +
        json.dumps({"proposal_schema": PROPOSAL_SCHEMA, "proposal_example_shape": example}, ensure_ascii=False) + "\n" +
        ("For revise_workflow return the complete proposal preserving all unaffected stages, not {patch:[...]}. "
         "For other feedback actions proposal_json is null. validation_input_patch_json is a flat JSON object of "
         "declared public workflow input names and replacement values, not JSON Patch; use {} if unchanged. "
         "candidate_expectations_json is a JSON ARRAY of output/operator/expected/tolerance checks, not assertion text "
         "or a must_pass object; use [] if unchanged. These are candidates, never permission to execute SAP.\n" +
         json.dumps({"candidate_expectations_schema": EXPECTATIONS_SCHEMA}, ensure_ascii=False) + "\n" if feedback else ""))


def decode(text, schema, *, operation, path):
    try:
        return checked_output(text, schema, operation=operation, output_format="embedded_json")
    except WorkBuddyError as exc:
        for issue in exc.detail["validation_issues"]:
            issue["path"] = path + (issue["path"] if issue["path"] != "/" else "")
        raise


def input_patch_schema(workflow):
    schema = workflow.get("inputSchema") or {}
    return {"type": "object", "properties": schema.get("properties", {}),
            "$defs": schema.get("$defs", {}), "additionalProperties": False}
