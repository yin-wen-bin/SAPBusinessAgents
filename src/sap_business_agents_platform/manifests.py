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
    "resolve_inventory_health_window",
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
        return [item for item in self.list() if is_agent_executable(item)]

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
    if agent.get("schemaVersion") == 2:
        _validate_page_contract(agent, inputs, source)
        _validate_workflow_mapping(agent, seen, source)
        _validate_acceptance(execution.get("acceptance"), source)
        _validate_live_acceptance(agent.get("validation"), source)


def is_agent_executable(agent: dict[str, Any]) -> bool:
    validation = agent.get("validation")
    return bool(
        agent.get("execution")
        and isinstance(validation, dict)
        and validation.get("verdict") == "PASS"
        and validation.get("executable") is True
        and validation.get("freeQueryComparison") == "MATCH"
        and validation.get("fixedAgentComparison") == "MATCH"
    )


def _localized_titles(properties: Any, source: str) -> dict[str, list[str]]:
    if not isinstance(properties, dict):
        raise ManifestError(f"{source}.properties must be an object")
    values = {"zh": [], "en": []}
    for name, schema in properties.items():
        if not isinstance(schema, dict):
            raise ManifestError(f"{source}.properties.{name} must be an object")
        title = schema.get("title")
        if not isinstance(title, dict) or not all(str(title.get(locale) or "").strip() for locale in values):
            raise ManifestError(f"{source}.properties.{name}.title must be bilingual")
        for locale in values:
            values[locale].append(str(title[locale]))
    return values


def _validate_page_contract(agent: dict[str, Any], inputs: dict[str, Any], source: str) -> None:
    if "SAP ECC" in (agent.get("systems") or []):
        raise ManifestError(f"{source}.systems must not advertise SAP ECC")
    expected_inputs = _localized_titles(inputs.get("properties"), f"{source}.execution.inputSchema")
    if agent.get("inputs") != expected_inputs:
        raise ManifestError(f"{source}.inputs must mirror execution.inputSchema titles")
    outputs = (agent.get("execution") or {}).get("outputSchema")
    if not isinstance(outputs, dict):
        raise ManifestError(f"{source}.execution.outputSchema is required")
    expected_outputs = _localized_titles(
        outputs.get("properties"), f"{source}.execution.outputSchema"
    )
    if agent.get("outputs") != expected_outputs:
        raise ManifestError(f"{source}.outputs must mirror execution.outputSchema titles")


def _validate_workflow_mapping(agent: dict[str, Any], step_ids: set[str], source: str) -> None:
    mapped: list[str] = []
    workflow = agent.get("workflow")
    if not isinstance(workflow, list) or not workflow:
        raise ManifestError(f"{source}.workflow must be non-empty")
    for index, step in enumerate(workflow):
        execution_ids = step.get("executionStepIds") if isinstance(step, dict) else None
        if not isinstance(execution_ids, list) or not execution_ids:
            raise ManifestError(f"{source}.workflow[{index}].executionStepIds is required")
        for step_id in execution_ids:
            if str(step_id) not in step_ids:
                raise ManifestError(
                    f"{source}.workflow[{index}] references unknown execution step {step_id}"
                )
            mapped.append(str(step_id))
    if len(mapped) != len(set(mapped)):
        raise ManifestError(f"{source}.workflow maps an execution step more than once")
    if set(mapped) != step_ids:
        raise ManifestError(f"{source}.workflow must map every execution step exactly once")


def _validate_acceptance(value: Any, source: str) -> None:
    if not isinstance(value, dict):
        raise ManifestError(f"{source}.execution.acceptance is required")
    if value.get("comparisonMode") != "business_semantic":
        raise ManifestError(f"{source}.execution.acceptance.comparisonMode is invalid")
    keys = value.get("businessKeys")
    if not isinstance(keys, list) or not keys or any(not str(item).strip() for item in keys):
        raise ManifestError(f"{source}.execution.acceptance.businessKeys is required")
    for field in ("facts", "metrics", "requiredLimitations"):
        if not isinstance(value.get(field), list):
            raise ManifestError(f"{source}.execution.acceptance.{field} must be an array")
    if value.get("schemaVersion") == "2.0":
        for field in (
            "decimalFields",
            "decimalMetricIds",
            "currencyFields",
            "unitFields",
            "dateFields",
        ):
            if not isinstance(value.get(field), list):
                raise ManifestError(
                    f"{source}.execution.acceptance.{field} must be an array"
                )
        for field in (
            "inputDefaults",
            "constantDefaults",
            "fieldAliases",
            "fieldExtractors",
            "currencyFromDecimal",
            "valueMappings",
            "limitationKeywords",
            "zeroFactWhenMetricZero",
        ):
            if not isinstance(value.get(field), dict):
                raise ManifestError(
                    f"{source}.execution.acceptance.{field} must be an object"
                )
        if not isinstance(value.get("summaryRecord"), bool):
            raise ManifestError(
                f"{source}.execution.acceptance.summaryRecord must be boolean"
            )
        if not isinstance(value.get("recordScope", ""), str):
            raise ManifestError(
                f"{source}.execution.acceptance.recordScope must be text"
            )
        if not isinstance(value.get("metricDefinitions", {}), dict):
            raise ManifestError(
                f"{source}.execution.acceptance.metricDefinitions must be an object"
            )
        if not isinstance(value.get("businessStatusDefinition", ""), str):
            raise ManifestError(
                f"{source}.execution.acceptance.businessStatusDefinition must be text"
            )
        if not isinstance(value.get("businessStatusFromAnyPositiveMetric", {}), dict):
            raise ManifestError(
                f"{source}.execution.acceptance.businessStatusFromAnyPositiveMetric must be an object"
            )


def _validate_live_acceptance(value: Any, source: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise ManifestError(f"{source}.validation must be an object")
    if value.get("verdict") not in {"PASS", "PARTIAL", "FAIL", "BLOCKED", "NOT_TESTED"}:
        raise ManifestError(f"{source}.validation.verdict is invalid")
    if value.get("executable") is not None and not isinstance(value.get("executable"), bool):
        raise ManifestError(f"{source}.validation.executable must be boolean")
    for field in ("freeQueryComparison", "fixedAgentComparison"):
        if value.get(field) is not None and value.get(field) not in {
            "MATCH",
            "MISMATCH",
            "BLOCKED",
            "NOT_TESTED",
        }:
            raise ManifestError(f"{source}.validation.{field} is invalid")
    for field in (
        "codexDirectBaselineHash",
        "freeQueryHash",
        "adjudicatedResultHash",
        "fixedAgentHash",
        "comparisonHash",
    ):
        if value.get(field) is not None and not re.fullmatch(r"sha256:[0-9a-f]{64}", str(value[field])):
            raise ManifestError(f"{source}.validation.{field} must be a full SHA-256")


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
