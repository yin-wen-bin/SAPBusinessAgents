"""Value-free input availability for design review, before SAP sample discovery."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

INPUT_METADATA_CONTRACT = "sapba.workflow-validation-input-metadata/1"

INPUT_METADATA_INSTRUCTIONS = """
Validation input availability is metadata, not executable input or example values.
For sapba.workflow-validation-input-metadata/1, fields marked pending_auto_discovery
have no concrete value yet. After a passing design review the platform attempts bounded,
read-only discovery, then validates the concrete input against the workflow schema before
execution. Discovery can fail; pending input proves neither a sample nor completeness.
Do not treat a metadata object or availability state as a string/array runtime input, or
reject its literal type/cardinality. Review its declared schema reference and graph mapping.
Provided fields expose only redacted JSON shape; reject known incompatible supplied shapes,
but do not infer hidden values or claim their formats/business facts are validated.
Fields marked missing remain missing with discovery disabled. All genuine graph, mapping,
safety, required-output and completeness blockers must still block. Never call SAP/tools here.
""".strip()


def missing_input(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _json_shape(value: Any, depth: int = 0) -> dict[str, Any]:
    if value is None:
        return {"json_type": "null"}
    if isinstance(value, bool):
        return {"json_type": "boolean"}
    if isinstance(value, int):
        return {"json_type": "integer"}
    if isinstance(value, float):
        return {"json_type": "number"}
    if isinstance(value, str):
        return {"json_type": "string", "length": len(value)}
    if isinstance(value, list):
        result: dict[str, Any] = {"json_type": "array", "length": len(value)}
        if depth < 3:
            result["items"] = [_json_shape(item, depth + 1) for item in value[:8]]
            if len(value) > 8:
                result["omitted_items"] = len(value) - 8
        return result
    if isinstance(value, dict):
        result = {"json_type": "object", "property_count": len(value)}
        # Business identifiers may be object keys too. Do not expose nested keys.
        if depth < 3:
            result["value_types"] = sorted({_json_shape(item, 3)["json_type"] for item in value.values()})
        return result
    return {"json_type": "non_json"}


def validation_input_metadata(
    input_schema: dict[str, Any], supplied_input: dict[str, Any],
    required_inputs: list[str], *, auto_discover: bool,
) -> dict[str, Any]:
    required = set(required_inputs)
    properties = input_schema.get("properties") or {}
    fields = {}
    for name in sorted(required.union(supplied_input)):
        missing = name in required and missing_input(supplied_input.get(name))
        field: dict[str, Any] = {
            "availability": "pending_auto_discovery" if missing and auto_discover else "missing" if missing else "provided",
            "required": name in required,
            "declared_schema_pointer": "/inputSchema/properties/" + name.replace("~", "~0").replace("/", "~1") if name in properties else None,
        }
        if not missing:
            field["supplied_shape"] = _json_shape(supplied_input[name])
        fields[name] = field
    return {"availability_semantics": INPUT_METADATA_INSTRUCTIONS,
        "contract": INPUT_METADATA_CONTRACT, "contains_business_values": False,
        "discovery_after_passing_review": bool(auto_discover),
        "concrete_input_schema_validation": "after_discovery_before_execution", "fields": deepcopy(fields)}
