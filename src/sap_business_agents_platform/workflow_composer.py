from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from .workflows import normalize_workflow, validate_workflow


ALLOWED_PORT_TYPES = {"string", "integer", "number", "boolean", "object", "array"}


class WorkflowCompositionError(ValueError):
    pass


def compact_agent_catalog(agents: Any) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for agent in sorted(agents.executable(), key=lambda item: str(item.get("slug") or "")):
        execution = agent.get("execution") or {}
        if not isinstance(execution.get("outputSchema"), dict):
            continue
        items.append(
            {
                "agent_id": str(agent.get("slug") or ""),
                "version": str(agent.get("version") or "0.0.0"),
                "module": str(agent.get("module") or "Common"),
                "title": deepcopy(agent.get("title") or {}),
                "summary": deepcopy(agent.get("summary") or {}),
                "tags": [str(item) for item in agent.get("tags") or []],
                "guardrails": deepcopy(agent.get("guardrails") or {}),
                "input_schema": deepcopy(execution.get("inputSchema") or {}),
                "output_schema": deepcopy(execution.get("outputSchema") or {}),
            }
        )
    canonical = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "digest": "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "items": items,
    }


def compile_workflow_proposal(
    *,
    workflow_id: str,
    requirement: str,
    locale: str,
    proposal: dict[str, Any],
    catalog: dict[str, Any],
    agents: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_stages = proposal.get("stages")
    if not isinstance(raw_stages, list) or not raw_stages:
        raise WorkflowCompositionError("Codex composition must contain at least one stage.")

    eligible = {str(item["agent_id"]): item for item in catalog.get("items") or []}
    stages: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    selected_by_stage: dict[str, dict[str, Any]] = {}

    for index, raw in enumerate(raw_stages, start=1):
        if not isinstance(raw, dict):
            raise WorkflowCompositionError(f"Composition stage {index} must be an object.")
        stage_id = _unique_stage_id(str(raw.get("id") or f"stage_{index}"), used_ids)
        agent_id = str(raw.get("agent_id") or "")
        confidence = str(raw.get("confidence") or "low").lower()
        selected = agent_id in eligible and confidence == "high"
        stage = {
            "id": stage_id,
            "capability": _localized(raw.get("capability"), fallback=f"Stage {index}"),
            "agent_id": agent_id if selected else None,
            "confidence": confidence if confidence in {"high", "medium", "low"} else "low",
            "reason": _localized(raw.get("reason"), fallback=""),
            "bindings": deepcopy(raw.get("bindings") or []),
            "requested_outputs": [str(item) for item in raw.get("requested_outputs") or []],
        }
        stages.append(stage)
        if selected:
            selected_by_stage[stage_id] = agents.get(agent_id)
            continue
        gap = {
            "gap_id": f"gap-{stage_id.replace('_', '-')}",
            "stage_id": stage_id,
            "title": _localized(raw.get("gap_title") or raw.get("capability"), fallback=f"Missing Agent {index}"),
            "description": _localized(raw.get("gap_description") or raw.get("capability"), fallback=""),
            "required_inputs": _sanitize_port_specs(raw.get("required_inputs")),
            "required_outputs": _sanitize_port_specs(raw.get("required_outputs")),
            "guardrails": _localized_list(raw.get("guardrails")),
            "acceptance": _localized(raw.get("acceptance"), fallback="Must pass repository review and three-stage live acceptance."),
            "status": "open",
            "agent_draft_id": None,
        }
        gaps.append(gap)

    nodes: list[dict[str, Any]] = []
    for index, stage in enumerate(stages):
        agent_id = stage.get("agent_id")
        if not agent_id:
            continue
        nodes.append(
            {
                "id": stage["id"],
                "agentId": agent_id,
                "position": {"x": 100 + index * 360, "y": 120},
            }
        )

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    connections: list[dict[str, Any]] = []
    accepted_sources: set[str] = set()
    processed_stage_ids: set[str] = set()

    for stage in stages:
        agent = selected_by_stage.get(stage["id"])
        if agent is None:
            continue
        agent_input = agent["execution"]["inputSchema"]
        properties = agent_input.get("properties") or {}
        required = {str(item) for item in agent_input.get("required") or []}
        bindings = {
            str(item.get("input_port") or ""): item
            for item in stage.get("bindings") or []
            if isinstance(item, dict)
        }
        for port, port_schema in properties.items():
            binding = bindings.get(str(port))
            source = _validated_binding_source(
                binding,
                target_port=str(port),
                target_schema=port_schema,
                selected_by_stage=selected_by_stage,
                allowed_source_ids=processed_stage_ids,
            )
            if source is None:
                source = _unique_compatible_source(
                    target_port=str(port),
                    target_schema=port_schema,
                    selected_by_stage=selected_by_stage,
                    allowed_source_ids=processed_stage_ids,
                )
            if source is not None:
                accepted_sources.add(str(source["nodeId"]))
                connections.append(
                    {
                        "from": source,
                        "to": {"nodeId": stage["id"], "port": str(port)},
                        "transform": {"type": "identity"},
                    }
                )
                continue
            public_name = _public_input_name(
                input_schema["properties"], str(port), stage["id"], port_schema
            )
            input_schema["properties"].setdefault(public_name, deepcopy(port_schema))
            if str(port) in required and public_name not in input_schema["required"]:
                input_schema["required"].append(public_name)
            connections.append(
                {
                    "from": {"scope": "workflow_input", "port": public_name},
                    "to": {"nodeId": stage["id"], "port": str(port)},
                    "transform": {"type": "identity"},
                }
            )
        processed_stage_ids.add(stage["id"])

    terminal_ids = [
        stage["id"]
        for stage in stages
        if stage["id"] in selected_by_stage and stage["id"] not in accepted_sources
    ]
    if not terminal_ids and selected_by_stage:
        terminal_ids = [next(reversed(selected_by_stage))]

    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    outputs: list[dict[str, Any]] = []
    for stage in stages:
        agent = selected_by_stage.get(stage["id"])
        if agent is None:
            continue
        available = agent["execution"]["outputSchema"].get("properties") or {}
        requested = list(stage.get("requested_outputs") or [])
        if stage["id"] in terminal_ids:
            requested = [*requested, "business_status", "business_report"]
        for port in _deduplicate(requested):
            if port not in available:
                continue
            name = f"{stage['id']}_{port}"
            output_schema["properties"][name] = deepcopy(available[port])
            output_schema["required"].append(name)
            outputs.append(
                {
                    "name": name,
                    "source": {"scope": "node_output", "nodeId": stage["id"], "port": port},
                    "transform": {"type": "identity"},
                }
            )

    title = _localized(proposal.get("title"), fallback=requirement[:80])
    description = _localized(proposal.get("description"), fallback=requirement)
    workflow = {
        "schemaVersion": 1,
        "id": workflow_id,
        "version": "0.1.0",
        "title": title,
        "description": description,
        "mode": "deterministic",
        "readOnly": True,
        "inputSchema": input_schema,
        "outputSchema": output_schema,
        "nodes": nodes,
        "connections": connections,
        "outputs": outputs,
        "policies": {"onInconclusive": "continue_if_required_outputs_present"},
    }
    workflow = normalize_workflow(workflow, agents)
    if nodes:
        validate_workflow(workflow, agents, source="generated-workflow", require_pins=True)
    defaults = proposal.get("validation_defaults")
    safe_defaults = {
        str(key): value
        for key, value in (defaults.items() if isinstance(defaults, dict) else [])
        if str(key) in input_schema["properties"]
    }
    composition = {
        "requirement": requirement,
        "locale": locale if locale in {"zh", "en"} else "zh",
        "intent": _localized(proposal.get("intent"), fallback=requirement),
        "catalog_digest": str(catalog.get("digest") or ""),
        "stages": stages,
        "gaps": gaps,
        "validation_defaults": safe_defaults,
        "clarification_question": "",
        "error": None,
    }
    return workflow, composition


def gap_free_query_prompt(gap: dict[str, Any], *, locale: str) -> str:
    title = _localized(gap.get("title"), fallback="Missing Agent")
    description = _localized(gap.get("description"), fallback="")
    guardrails = _localized_list(gap.get("guardrails"))
    acceptance = _localized(gap.get("acceptance"), fallback="")
    language = "zh" if locale == "zh" else "en"
    if language == "zh":
        return (
            f"为缺口 Agent 调研并执行一次严格只读的 SAP 自由查询：{title['zh']}。\n"
            f"功能要求：{description['zh']}\n"
            f"期望输入端口：{json.dumps(gap.get('required_inputs') or [], ensure_ascii=False)}\n"
            f"期望输出端口：{json.dumps(gap.get('required_outputs') or [], ensure_ascii=False)}\n"
            f"安全边界：{'；'.join(guardrails['zh']) or '仅允许 GET 和已批准的只读 Skill'}。\n"
            f"验收要求：{acceptance['zh']}。不得创建或修改 SAP 业务数据。"
        )
    return (
        f"Research and execute one strictly read-only SAP free query for this missing Agent: {title['en']}.\n"
        f"Required capability: {description['en']}\n"
        f"Expected input ports: {json.dumps(gap.get('required_inputs') or [])}\n"
        f"Expected output ports: {json.dumps(gap.get('required_outputs') or [])}\n"
        f"Guardrails: {'; '.join(guardrails['en']) or 'GET and approved read-only Skills only'}.\n"
        f"Acceptance: {acceptance['en']}. Never create or modify SAP business data."
    )


def _validated_binding_source(
    binding: Any,
    *,
    target_port: str,
    target_schema: dict[str, Any],
    selected_by_stage: dict[str, dict[str, Any]],
    allowed_source_ids: set[str],
) -> dict[str, Any] | None:
    if not isinstance(binding, dict):
        return None
    source_stage = str(binding.get("source_stage_id") or "")
    source_port = str(binding.get("source_output_port") or "")
    if (
        source_port != target_port
        or source_stage not in selected_by_stage
        or source_stage not in allowed_source_ids
    ):
        return None
    output_schema = selected_by_stage[source_stage]["execution"]["outputSchema"]
    source_schema = (output_schema.get("properties") or {}).get(source_port)
    if not isinstance(source_schema, dict):
        return None
    source_type = str(source_schema.get("type") or "")
    target_type = str(target_schema.get("type") or "")
    if source_type != target_type and not (source_type == "integer" and target_type == "number"):
        return None
    return {"scope": "node_output", "nodeId": source_stage, "port": source_port}


def _unique_compatible_source(
    *,
    target_port: str,
    target_schema: dict[str, Any],
    selected_by_stage: dict[str, dict[str, Any]],
    allowed_source_ids: set[str],
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for stage_id, agent in selected_by_stage.items():
        if stage_id not in allowed_source_ids:
            continue
        output_schema = agent["execution"]["outputSchema"]
        source_schema = (output_schema.get("properties") or {}).get(target_port)
        if not isinstance(source_schema, dict):
            continue
        source_type = str(source_schema.get("type") or "")
        target_type = str(target_schema.get("type") or "")
        if source_type == target_type or (source_type == "integer" and target_type == "number"):
            candidates.append(
                {"scope": "node_output", "nodeId": stage_id, "port": target_port}
            )
    return candidates[0] if len(candidates) == 1 else None


def _public_input_name(
    properties: dict[str, Any], port: str, stage_id: str, schema: dict[str, Any]
) -> str:
    if port not in properties:
        return port
    if _schema_signature(properties[port]) == _schema_signature(schema):
        return port
    candidate = f"{stage_id}_{port}"
    index = 1
    while candidate in properties:
        index += 1
        candidate = f"{stage_id}_{port}_{index}"
    return candidate


def _schema_signature(schema: Any) -> str:
    if not isinstance(schema, dict):
        return ""
    relevant = {
        key: schema[key]
        for key in ("type", "format", "pattern", "enum", "minLength", "maxLength")
        if key in schema
    }
    return json.dumps(relevant, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _unique_stage_id(value: str, used: set[str]) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_") or "stage"
    if not normalized[0].isalpha():
        normalized = f"stage_{normalized}"
    candidate = normalized
    index = 1
    while candidate in used:
        index += 1
        candidate = f"{normalized}_{index}"
    used.add(candidate)
    return candidate


def _localized(value: Any, *, fallback: str) -> dict[str, str]:
    if isinstance(value, dict):
        zh = str(value.get("zh") or value.get("en") or fallback)
        en = str(value.get("en") or value.get("zh") or fallback)
        return {"zh": zh, "en": en}
    text = str(value or fallback)
    return {"zh": text, "en": text}


def _localized_list(value: Any) -> dict[str, list[str]]:
    if isinstance(value, dict):
        zh = [str(item) for item in value.get("zh") or value.get("en") or []]
        en = [str(item) for item in value.get("en") or value.get("zh") or []]
        return {"zh": zh, "en": en}
    if isinstance(value, list):
        items = [str(item) for item in value]
        return {"zh": items, "en": items}
    return {"zh": [], "en": []}


def _sanitize_port_specs(value: Any) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return specs
    for item in value:
        if not isinstance(item, dict):
            continue
        name = re.sub(r"[^a-z0-9_]+", "_", str(item.get("name") or "").lower()).strip("_")
        kind = str(item.get("type") or "string")
        if not name or kind not in ALLOWED_PORT_TYPES:
            continue
        specs.append(
            {
                "name": name,
                "type": kind,
                "description": _localized(item.get("description"), fallback=name),
                "required": bool(item.get("required", True)),
            }
        )
    return specs


def _deduplicate(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
