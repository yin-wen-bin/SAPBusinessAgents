"""SDK-free, allowlisted native progress projection; no command/content logging."""
import re
import hashlib
import json


def output_diagnostic(text, *, operation, output_format=None):
    """Keep enough to identify a terminal failure without saving business rows."""
    value = text.encode("utf-8") if isinstance(text, str) else None
    return {"operation": operation, "phase": "terminal_output", "output_format": output_format,
            "output_length": len(value) if value is not None else None,
            "output_sha256": hashlib.sha256(value).hexdigest() if value is not None else None}


def schema_issues(raw, schema):
    from jsonschema import Draft202012Validator
    issues = []
    for error in Draft202012Validator(schema).iter_errors(raw):
        parts, node = [], schema
        for part in error.absolute_path:
            if "$ref" in node and node["$ref"].startswith("#/$defs/"):
                node = schema.get("$defs", {}).get(node["$ref"].split("/")[-1], {})
            if isinstance(part, int):
                node = node.get("items", {})
            elif part in node.get("properties", {}):
                node = node["properties"][part]
            else:
                break
            parts.append(str(part).replace("~", "~0").replace("/", "~1"))
        path = "/" + "/".join(parts)
        missing = [name for name in error.schema.get("required", []) if name in error.schema.get("properties", {})
                   and isinstance(error.instance, dict) and name not in error.instance]
        if error.validator == "required" and missing:
            issues.extend({"code": "runtime_report_schema_invalid", "path": path.rstrip("/") + "/" + name,
                           "constraint": "required"} for name in missing)
        else:
            issues.append({"code": "runtime_report_schema_invalid", "path": path, "constraint": str(error.validator)})
        if len(issues) >= 50:
            break
    return issues[:50]


def checked_output(text, schema, *, operation, output_format=None):
    from .runtime_contract import RuntimeContractError
    detail = output_diagnostic(text, operation=operation, output_format=output_format)
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        issues = [{"code": "runtime_report_json_invalid", "path": "/", "constraint": "json"}]
    else:
        issues = schema_issues(raw, schema)
    if issues:
        error = RuntimeContractError("runtime_report_validation_failed")
        error.detail = {**detail, "validation_issues": issues}
        raise error
    return raw

