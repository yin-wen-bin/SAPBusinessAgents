from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ALLOWED_EXECUTORS = {"sap_read", "skill", "rule"}
ALLOWED_SAP_READ_OPERATIONS = {"execute_plan", "execute_get"}
ALLOWED_RULE_OPERATIONS = {
    "assess_api_evidence",
    "assess_adt_preflight",
    "assess_o2c_document_flow",
    "classify_control_object",
    "evidence_summary",
    "extract_bounded_values",
    "evaluate_business_agent",
    "evaluate_p2p_status",
    "evaluate_o2c_status",
}
ALLOWED_FAILURE_POLICIES = {"fail_run", "record_gap"}


class ManifestError(ValueError):
    pass


class AgentRepository:
    def __init__(self, agents_root: Path) -> None:
        self.agents_root = agents_root

    def list(self) -> list[dict[str, Any]]:
        manifests: list[dict[str, Any]] = []
        for path in sorted(self.agents_root.glob("*/*/agent.json")):
            manifests.append(self._load_path(path))
        return manifests

    def get(self, agent_id: str) -> dict[str, Any]:
        for path in self.agents_root.glob("*/*/agent.json"):
            if path.parent.name == agent_id:
                return self._load_path(path)
        raise KeyError(agent_id)

    def executable(self) -> list[dict[str, Any]]:
        return [item for item in self.list() if item.get("execution")]

    def _load_path(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestError(f"Cannot load {path}: {exc}") from exc
        if payload.get("schemaVersion") == 2:
            validate_execution(payload, str(path))
        return payload


def validate_execution(agent: dict[str, Any], source: str = "agent.json") -> None:
    execution = agent.get("execution")
    if not isinstance(execution, dict):
        raise ManifestError(f"{source}.execution must be an object")
    if execution.get("mode") != "deterministic":
        raise ManifestError(f"{source}.execution.mode must be deterministic")
    inputs = execution.get("inputSchema")
    if not isinstance(inputs, dict) or inputs.get("type") != "object":
        raise ManifestError(f"{source}.execution.inputSchema must be an object JSON Schema")
    outputs = execution.get("outputSchema")
    output_mapping = execution.get("outputMapping")
    if outputs is not None:
        if not isinstance(outputs, dict) or outputs.get("type") != "object":
            raise ManifestError(f"{source}.execution.outputSchema must be an object JSON Schema")
        if not isinstance(outputs.get("properties"), dict):
            raise ManifestError(f"{source}.execution.outputSchema.properties must be an object")
        if not isinstance(output_mapping, dict):
            raise ManifestError(f"{source}.execution.outputMapping must be an object")
        unknown_outputs = sorted(set(output_mapping).difference(outputs["properties"]))
        if unknown_outputs:
            raise ManifestError(
                f"{source}.execution.outputMapping contains undeclared outputs: "
                + ", ".join(unknown_outputs)
            )
    steps = execution.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ManifestError(f"{source}.execution.steps must be non-empty")
    seen: set[str] = set()
    for index, step in enumerate(steps):
        location = f"{source}.execution.steps[{index}]"
        if not isinstance(step, dict):
            raise ManifestError(f"{location} must be an object")
        step_id = str(step.get("id") or "")
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", step_id):
            raise ManifestError(f"{location}.id is invalid")
        if step_id in seen:
            raise ManifestError(f"{source}: duplicate execution step {step_id}")
        seen.add(step_id)
        when = step.get("when")
        if when is not None:
            _validate_when(when, seen - {step_id}, location)
        executor = step.get("executor")
        if executor not in ALLOWED_EXECUTORS:
            raise ManifestError(f"{location}.executor is not allowed")
        failure_policy = step.get("failurePolicy", "fail_run")
        if failure_policy not in ALLOWED_FAILURE_POLICIES:
            raise ManifestError(f"{location}.failurePolicy is not allowed")
        if failure_policy == "record_gap" and executor not in {"sap_read", "skill"}:
            raise ManifestError(
                f"{location}.failurePolicy=record_gap is only allowed for read-only evidence steps"
            )
        if executor == "sap_read":
            if step.get("operation") not in ALLOWED_SAP_READ_OPERATIONS:
                raise ManifestError(f"{location}.operation is not allowed")
            if step.get("readOnly") is not True:
                raise ManifestError(f"{location} must declare readOnly=true")
            request = step.get("request")
            if not isinstance(request, dict):
                raise ManifestError(f"{location}.request must be an object")
            _reject_write_methods(request, location)
            _require_odata_versions(request, location)
        if executor == "skill":
            if step.get("readOnly") is not True:
                raise ManifestError(f"{location} skill must declare readOnly=true")
            if not str(step.get("skillId") or ""):
                raise ManifestError(f"{location}.skillId is required")
            if not isinstance(step.get("request") or step.get("inputMapping") or {}, dict):
                raise ManifestError(f"{location} skill input must be an object")
        if executor == "rule" and step.get("operation") not in ALLOWED_RULE_OPERATIONS:
            raise ManifestError(f"{location}.operation is not an approved local rule")


def _validate_when(value: Any, prior_step_ids: set[str], location: str) -> None:
    if not isinstance(value, dict) or set(value) != {"source", "equals"}:
        raise ManifestError(f"{location}.when must contain only source and equals")
    source = value.get("source")
    expected = value.get("equals")
    if not isinstance(source, str) or not isinstance(expected, bool):
        raise ManifestError(f"{location}.when requires a template source and boolean equals")
    match = re.fullmatch(
        r"\{\{\s*steps\.([a-z][a-z0-9_-]*)\.output(?:\.[A-Za-z0-9_-]+)+\s*\}\}",
        source,
    )
    if match is None:
        raise ManifestError(f"{location}.when.source must be one prior step output template")
    if match.group(1) not in prior_step_ids:
        raise ManifestError(f"{location}.when.source must reference a prior step")


def _reject_write_methods(value: Any, location: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"http_method", "httpMethod"} and str(child).upper() != "GET":
                raise ManifestError(f"{location} contains a non-GET SAP operation")
            _reject_write_methods(child, location)
    elif isinstance(value, list):
        for child in value:
            _reject_write_methods(child, location)


def _require_odata_versions(value: Any, location: str) -> None:
    if isinstance(value, dict):
        if "service_name" in value and "entity_set" in value:
            if value.get("odata_version") not in {"2.0", "4.0"}:
                raise ManifestError(
                    f"{location} service references must declare odata_version 2.0 or 4.0"
                )
        forbidden = {
            "url",
            "resource_path",
            "service_root_path",
            "metadata_path",
            "headers",
            "authorization",
            "sap_client",
        }
        if forbidden.intersection(value):
            raise ManifestError(f"{location} contains forbidden transport fields")
        for child in value.values():
            _require_odata_versions(child, location)
    elif isinstance(value, list):
        for child in value:
            _require_odata_versions(child, location)
