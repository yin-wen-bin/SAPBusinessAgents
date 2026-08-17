from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from .manifests import ManifestError, validate_execution


ALLOWED_TRANSFORMS = {
    "identity",
    "to_string",
    "to_integer",
    "format_date",
    "first",
    "join",
}


class WorkflowError(ValueError):
    def __init__(self, message: str, *, code: str = "workflow_invalid", detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class WorkflowRepository:
    def __init__(self, root: Path, agents: Any) -> None:
        self.root = root
        self.agents = agents

    def list(self) -> list[dict[str, Any]]:
        return [self._load(path) for path in sorted(self.root.glob("*/*/workflow.json"))]

    def get(self, workflow_id: str) -> dict[str, Any]:
        for path in self.root.glob("*/*/workflow.json"):
            payload = self._load(path)
            if payload.get("id") == workflow_id or path.parent.name == workflow_id:
                return payload
        raise KeyError(workflow_id)

    def _load(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowError(f"Cannot load {path}: {exc}") from exc
        validate_workflow(payload, self.agents, source=str(path), require_pins=True)
        return payload


def agent_digest(agent: dict[str, Any]) -> str:
    selected = {
        "schemaVersion": agent.get("schemaVersion"),
        "slug": agent.get("slug"),
        "version": agent.get("version"),
        "execution": agent.get("execution"),
    }
    canonical = json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def workflow_digest(workflow: dict[str, Any]) -> str:
    canonical = json.dumps(workflow, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_workflow(workflow: dict[str, Any], agents: Any) -> dict[str, Any]:
    normalized = json.loads(json.dumps(workflow))
    for node in normalized.get("nodes") or []:
        if not isinstance(node, dict) or not node.get("agentId"):
            continue
        agent = agents.get(str(node["agentId"]))
        node.setdefault("agentVersion", str(agent.get("version") or "0.0.0"))
        node.setdefault("agentDigest", agent_digest(agent))
    normalized.setdefault("schemaVersion", 1)
    normalized.setdefault("version", "0.1.0")
    normalized.setdefault("mode", "deterministic")
    normalized.setdefault("readOnly", True)
    normalized.setdefault("inputSchema", {"type": "object", "properties": {}, "additionalProperties": False})
    normalized.setdefault("outputSchema", {"type": "object", "properties": {}, "additionalProperties": False})
    normalized.setdefault("connections", [])
    normalized.setdefault("outputs", [])
    normalized.setdefault("policies", {"onInconclusive": "continue_if_required_outputs_present"})
    return normalized


def validate_workflow(
    workflow: dict[str, Any],
    agents: Any,
    *,
    source: str = "workflow.json",
    require_pins: bool = True,
) -> list[str]:
    if workflow.get("schemaVersion") != 1:
        raise WorkflowError(f"{source}.schemaVersion must be 1")
    workflow_id = str(workflow.get("id") or "")
    if not re.fullmatch(r"[a-z][a-z0-9-]*", workflow_id):
        raise WorkflowError(f"{source}.id is invalid")
    if workflow.get("mode") != "deterministic" or workflow.get("readOnly") is not True:
        raise WorkflowError(f"{source} must be deterministic and readOnly=true")
    _validate_object_schema(workflow.get("inputSchema"), f"{source}.inputSchema")
    _validate_object_schema(workflow.get("outputSchema"), f"{source}.outputSchema")
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise WorkflowError(f"{source}.nodes must be a non-empty array")
    node_by_id: dict[str, dict[str, Any]] = {}
    agent_by_node: dict[str, dict[str, Any]] = {}
    drift: list[str] = []
    for index, node in enumerate(nodes):
        location = f"{source}.nodes[{index}]"
        if not isinstance(node, dict):
            raise WorkflowError(f"{location} must be an object")
        node_id = str(node.get("id") or "")
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", node_id) or node_id in node_by_id:
            raise WorkflowError(f"{location}.id is invalid or duplicated")
        agent_id = str(node.get("agentId") or "")
        try:
            agent = agents.get(agent_id)
        except (KeyError, ManifestError) as exc:
            raise WorkflowError(f"{location}.agentId is unavailable: {agent_id}") from exc
        validate_execution(agent, f"agent:{agent_id}")
        execution = agent["execution"]
        if not isinstance(execution.get("outputSchema"), dict):
            raise WorkflowError(
                f"Agent {agent_id} has no composable execution.outputSchema",
                code="agent_not_composable",
            )
        expected_version = str(agent.get("version") or "0.0.0")
        expected_digest = agent_digest(agent)
        if require_pins and (
            node.get("agentVersion") != expected_version or node.get("agentDigest") != expected_digest
        ):
            drift.append(node_id)
        node_by_id[node_id] = node
        agent_by_node[node_id] = agent
    if drift:
        raise WorkflowError(
            "One or more Agent versions changed after workflow validation.",
            code="agent_version_mismatch",
            detail={"nodes": drift},
        )

    connections = workflow.get("connections")
    if not isinstance(connections, list):
        raise WorkflowError(f"{source}.connections must be an array")
    target_ports: set[tuple[str, str]] = set()
    dependencies: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    for index, connection in enumerate(connections):
        location = f"{source}.connections[{index}]"
        if not isinstance(connection, dict):
            raise WorkflowError(f"{location} must be an object")
        source_spec = connection.get("from")
        target_spec = connection.get("to")
        if not isinstance(source_spec, dict) or not isinstance(target_spec, dict):
            raise WorkflowError(f"{location} must contain from/to objects")
        target_node = str(target_spec.get("nodeId") or "")
        target_port = str(target_spec.get("port") or "")
        if target_node not in node_by_id:
            raise WorkflowError(f"{location}.to.nodeId is unavailable")
        target_schema = agent_by_node[target_node]["execution"]["inputSchema"]
        target_type = _property_schema(target_schema, target_port, f"{location}.to.port")
        target_key = (target_node, target_port)
        if target_key in target_ports:
            raise WorkflowError(f"{location} maps the same target input more than once")
        target_ports.add(target_key)
        source_type = _source_type(
            source_spec, workflow, node_by_id, agent_by_node, dependencies, target_node, location
        )
        transform = connection.get("transform") or {"type": "identity"}
        transformed_type = _validate_transform(source_type, transform, location)
        if not _types_compatible(transformed_type, str(target_type.get("type") or "")):
            raise WorkflowError(
                f"{location} maps incompatible types {transformed_type!r} -> "
                f"{target_type.get('type')!r}",
                code="workflow_port_type_mismatch",
            )
    for node_id, agent in agent_by_node.items():
        required = agent["execution"]["inputSchema"].get("required") or []
        missing = [port for port in required if (node_id, str(port)) not in target_ports]
        if missing:
            raise WorkflowError(
                f"Node {node_id} is missing required inputs: {', '.join(missing)}",
                code="workflow_required_input_unmapped",
            )
    topological_order(workflow)
    _validate_outputs(workflow, node_by_id, agent_by_node)
    return drift


def topological_order(workflow: dict[str, Any]) -> list[str]:
    node_ids = [str(item["id"]) for item in workflow.get("nodes") or []]
    dependencies: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for connection in workflow.get("connections") or []:
        source = connection.get("from") or {}
        target = connection.get("to") or {}
        if source.get("scope") == "node_output":
            dependencies[str(target.get("nodeId"))].add(str(source.get("nodeId")))
    remaining = {node: set(values) for node, values in dependencies.items()}
    order: list[str] = []
    while remaining:
        ready = [node for node in node_ids if node in remaining and not remaining[node]]
        if not ready:
            raise WorkflowError("Workflow graph contains a cycle.", code="workflow_cycle_detected")
        for node in ready:
            order.append(node)
            remaining.pop(node)
            for values in remaining.values():
                values.discard(node)
    return order


def apply_transform(value: Any, transform: dict[str, Any] | None) -> Any:
    transform = transform or {"type": "identity"}
    kind = str(transform.get("type") or "identity")
    if kind == "identity":
        return value
    if kind == "to_string":
        return str(value)
    if kind == "to_integer":
        if isinstance(value, bool):
            raise WorkflowError("Boolean values cannot be converted to integer.", code="mapping_failed")
        return int(value)
    if kind == "format_date":
        text = str(value)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ].*)?", text):
            raise WorkflowError("Date transform requires an ISO date value.", code="mapping_failed")
        return text[:10]
    if kind == "first":
        if not isinstance(value, list) or not value:
            raise WorkflowError("first transform requires a non-empty list.", code="mapping_failed")
        return value[0]
    if kind == "join":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise WorkflowError("join transform requires a string list.", code="mapping_failed")
        return str(transform.get("separator") or ",").join(value)
    raise WorkflowError(f"Unsupported workflow transform: {kind}", code="mapping_failed")


def validate_value(value: dict[str, Any], schema: dict[str, Any], *, label: str) -> None:
    if not isinstance(value, dict):
        raise WorkflowError(f"{label} must be an object", code="workflow_contract_violation")
    properties = schema.get("properties") or {}
    required = [str(item) for item in schema.get("required") or []]
    missing = [name for name in required if name not in value or value[name] in (None, "")]
    if missing:
        raise WorkflowError(
            f"{label} is missing required fields: {', '.join(missing)}",
            code="workflow_output_unavailable",
        )
    unknown = sorted(set(value).difference(properties))
    if schema.get("additionalProperties") is False and unknown:
        raise WorkflowError(
            f"{label} contains undeclared fields: {', '.join(unknown)}",
            code="workflow_contract_violation",
        )
    for name, item in value.items():
        if name not in properties:
            continue
        expected = str((properties[name] or {}).get("type") or "")
        if not _value_matches_type(item, expected):
            raise WorkflowError(
                f"{label}.{name} does not match type {expected}",
                code="workflow_contract_violation",
            )


def _validate_object_schema(value: Any, location: str) -> None:
    if not isinstance(value, dict) or value.get("type") != "object" or not isinstance(
        value.get("properties"), dict
    ):
        raise WorkflowError(f"{location} must be an object JSON Schema")


def _property_schema(schema: dict[str, Any], name: str, location: str) -> dict[str, Any]:
    properties = schema.get("properties") or {}
    value = properties.get(name)
    if not isinstance(value, dict):
        raise WorkflowError(f"{location} references an undeclared port: {name}")
    return value


def _source_type(
    source: dict[str, Any],
    workflow: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    agents: dict[str, dict[str, Any]],
    dependencies: dict[str, set[str]],
    target_node: str,
    location: str,
) -> str:
    scope = source.get("scope")
    if scope == "workflow_input":
        return str(_property_schema(workflow["inputSchema"], str(source.get("port") or ""), location).get("type") or "")
    if scope == "node_output":
        source_node = str(source.get("nodeId") or "")
        if source_node not in nodes or source_node == target_node:
            raise WorkflowError(f"{location}.from.nodeId is unavailable")
        dependencies[target_node].add(source_node)
        output_schema = agents[source_node]["execution"]["outputSchema"]
        return str(_property_schema(output_schema, str(source.get("port") or ""), location).get("type") or "")
    if scope == "constant":
        return _json_type(source.get("value"))
    raise WorkflowError(f"{location}.from.scope is unsupported")


def _validate_transform(source_type: str, transform: Any, location: str) -> str:
    if not isinstance(transform, dict):
        raise WorkflowError(f"{location}.transform must be an object")
    kind = str(transform.get("type") or "identity")
    if kind not in ALLOWED_TRANSFORMS:
        raise WorkflowError(f"{location}.transform.type is not allowed")
    if kind == "identity":
        return source_type
    if kind == "to_string":
        return "string"
    if kind == "to_integer":
        if source_type not in {"string", "integer", "number"}:
            raise WorkflowError(f"{location} cannot convert {source_type} to integer")
        return "integer"
    if kind == "format_date":
        if source_type != "string":
            raise WorkflowError(f"{location} date formatting requires a string")
        return "string"
    if kind in {"first", "join"}:
        if source_type != "array":
            raise WorkflowError(f"{location}.{kind} requires an array source")
        return "string" if kind == "join" else str(transform.get("resultType") or "string")
    return source_type


def _validate_outputs(
    workflow: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    agents: dict[str, dict[str, Any]],
) -> None:
    output_defs = workflow.get("outputs")
    if not isinstance(output_defs, list):
        raise WorkflowError("workflow.outputs must be an array")
    declared = workflow["outputSchema"].get("properties") or {}
    seen: set[str] = set()
    dummy_dependencies = {node: set() for node in nodes}
    dummy_dependencies[""] = set()
    for index, output in enumerate(output_defs):
        if not isinstance(output, dict):
            raise WorkflowError(f"workflow.outputs[{index}] must be an object")
        name = str(output.get("name") or "")
        if name not in declared or name in seen:
            raise WorkflowError(f"workflow.outputs[{index}].name is undeclared or duplicated")
        seen.add(name)
        source_type = _source_type(
            output.get("source") or {}, workflow, nodes, agents, dummy_dependencies, "", f"workflow.outputs[{index}]"
        )
        transformed = _validate_transform(source_type, output.get("transform") or {"type": "identity"}, f"workflow.outputs[{index}]")
        if not _types_compatible(transformed, str(declared[name].get("type") or "")):
            raise WorkflowError(f"workflow.outputs[{index}] has an incompatible type")
    missing = sorted(set(workflow["outputSchema"].get("required") or []).difference(seen))
    if missing:
        raise WorkflowError("Required workflow outputs are not mapped: " + ", ".join(missing))


def _types_compatible(source: str, target: str) -> bool:
    return source == target or (source == "integer" and target == "number")


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "null"


def _value_matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def iter_node_connections(workflow: dict[str, Any], node_id: str) -> Iterable[dict[str, Any]]:
    for connection in workflow.get("connections") or []:
        if str((connection.get("to") or {}).get("nodeId") or "") == node_id:
            yield connection
