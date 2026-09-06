from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError


ALLOWED_EXECUTORS = {"sap_read", "skill", "rule"}
ALLOWED_SAP_READ_OPERATIONS = {"execute_plan", "execute_get"}
ALLOWED_RULE_OPERATIONS = {
    "prepare_month_end_scope",
    "evaluate_month_end_closing",
    "resolve_month_end_skill_requirements",
    "assess_inventory_batch_expiry",
    "assess_api_evidence",
    "assess_adt_preflight",
    "assess_billing_block_incompletion",
    "prepare_billing_block_code_text_lookups",
    "prepare_ap_input",
    "prepare_fi_ledger_scope",
    "prepare_ar_cash_application_scope",
    "prepare_ar_collection_context",
    "assess_o2c_document_flow",
    "classify_control_object",
    "prepare_control_object_lookup",
    "resolve_control_object_master",
    "evidence_summary",
    "extract_bounded_values",
    "resolve_mrp_analysis_context",
    "resolve_demand_forecast_context",
    "resolve_new_sales_demand_context",
    "resolve_production_cost_scope",
    "resolve_inventory_health_window",
    "evaluate_business_agent",
    "evaluate_p2p_status",
    "evaluate_o2c_status",
    "managed_agent_rule",
}
ALLOWED_FAILURE_POLICIES = {"fail_run", "record_gap"}
_TEMPLATE_EXPRESSION = re.compile(r"\{\{\s*[^{}]+?\s*\}\}")
_HAN_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


class ManifestError(ValueError):
    pass


class AgentRepository:
    def __init__(self, agents_root: Path) -> None:
        self.agents_root = agents_root

    def list(self) -> list[dict[str, Any]]:
        manifests: list[dict[str, Any]] = []
        for path in sorted(self.agents_root.glob("*/*/agent.json")):
            if self.lifecycle(path.parent)["state"] == "active":
                manifests.append(self._load_path(path))
        return manifests

    def list_all(self) -> list[dict[str, Any]]:
        return [self._load_path(path) for path in sorted(self.agents_root.glob("*/*/agent.json"))]

    def get(self, agent_id: str) -> dict[str, Any]:
        for path in self.agents_root.glob("*/*/agent.json"):
            if path.parent.name == agent_id:
                return self._load_path(path)
        raise KeyError(agent_id)

    def get_version(
        self, agent_id: str, version: str, digest: str | None = None
    ) -> dict[str, Any]:
        current_path = self._path(agent_id)
        current = self._load_path(current_path)
        candidate = current
        if str(current.get("version") or "") != version:
            version_path = current_path.parent / "versions" / version / "agent.json"
            if not version_path.is_file():
                raise KeyError((agent_id, version))
            candidate = self._load_path(version_path)
        if digest:
            from .workflows import agent_digest

            if agent_digest(candidate) != digest:
                raise ManifestError(
                    f"Agent {agent_id} version {version} digest does not match the published package"
                )
        return candidate

    def package(
        self, agent_id: str, version: str | None = None, digest: str | None = None
    ) -> dict[str, Any]:
        manifest = self.get(agent_id) if version is None else self.get_version(agent_id, version, digest)
        current_path = self._path(agent_id)
        directory = current_path.parent
        if version is not None and str(manifest.get("version") or "") != str(
            self.get(agent_id).get("version") or ""
        ):
            directory = directory / "versions" / version
        rules_path = directory / "rules.py"
        return {
            "manifest": manifest,
            "rules_source": rules_path.read_text(encoding="utf-8") if rules_path.is_file() else None,
            "directory": str(directory),
        }

    def lifecycle(self, directory_or_agent: Path | str) -> dict[str, Any]:
        directory = (
            directory_or_agent
            if isinstance(directory_or_agent, Path)
            else self._path(directory_or_agent).parent
        )
        path = directory / "publication.json"
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ManifestError(f"Cannot load {path}: {exc}") from exc
            state = payload.get("lifecycle_state", payload.get("state"))
            if state not in {"active", "inactive"}:
                raise ManifestError(f"{path}.lifecycle_state must be active or inactive")
            payload["state"] = state
            return payload
        manifest_path = directory / "agent.json"
        payload = self._load_path(manifest_path)
        from .workflows import agent_digest

        return {
            "schemaVersion": 1,
            "agent_id": str(payload.get("slug") or directory.name),
            "state": "active",
            "lifecycle_state": "active",
            "active_version": str(payload.get("version") or ""),
            "latest_version": str(payload.get("version") or ""),
            "active_digest": agent_digest(payload),
        }

    def is_active(self, agent_id: str) -> bool:
        return self.lifecycle(agent_id)["state"] == "active"

    def _path(self, agent_id: str) -> Path:
        for path in self.agents_root.glob("*/*/agent.json"):
            if path.parent.name == agent_id:
                return path
        raise KeyError(agent_id)

    def executable(self) -> list[dict[str, Any]]:
        return [item for item in self.list() if is_agent_executable(item)]

    def _load_path(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestError(f"Cannot load {path}: {exc}") from exc
        if payload.get("schemaVersion") == 2:
            validate_manifest(payload, str(path))
        return payload


def validate_manifest(agent: dict[str, Any], source: str = "agent.json") -> None:
    if agent.get("kind") == "platform_assistant":
        assistant = agent.get("assistant")
        if not isinstance(assistant, dict):
            raise ManifestError(f"{source}.assistant must be an object")
        expected = {
            "type": "role_matching",
            "runtimeCapability": "role_matching",
            "composable": False,
            "localFileAccess": "read_only_user_selected",
        }
        for field, value in expected.items():
            if assistant.get(field) != value:
                raise ManifestError(f"{source}.assistant.{field} must be {value!r}")
        if agent.get("module") != "Common":
            raise ManifestError(f"{source}: platform assistants must be in Common")
        if "execution" in agent:
            raise ManifestError(f"{source}: platform assistants cannot declare execution")
        return
    validate_execution(agent, source)


def validate_execution(agent: dict[str, Any], source: str = "agent.json") -> None:
    execution = agent.get("execution")
    if not isinstance(execution, dict):
        raise ManifestError(f"{source}.execution must be an object")
    if execution.get("mode") != "deterministic":
        raise ManifestError(f"{source}.execution.mode must be deterministic")
    managed_steps = [
        step for step in execution.get("steps") or []
        if isinstance(step, dict) and step.get("executor") == "rule"
        and step.get("operation") == "managed_agent_rule"
    ]
    if managed_steps:
        managed = agent.get("managedRule")
        if not isinstance(managed, dict):
            raise ManifestError(f"{source}.managedRule is required for managed_agent_rule")
        if managed.get("entrypoint") != "evaluate":
            raise ManifestError(f"{source}.managedRule.entrypoint must be 'evaluate'")
        digest = managed.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ManifestError(f"{source}.managedRule.sha256 must be a SHA-256 digest")
    inputs = execution.get("inputSchema")
    if not isinstance(inputs, dict) or inputs.get("type") != "object":
        raise ManifestError(f"{source}.execution.inputSchema must be an object JSON Schema")
    _validate_json_schema(inputs, f"{source}.execution.inputSchema")
    input_properties = inputs.get("properties")
    if not isinstance(input_properties, dict):
        raise ManifestError(f"{source}.execution.inputSchema.properties must be an object")
    _validate_input_server_defaults(
        input_properties,
        f"{source}.execution.inputSchema.properties",
    )
    outputs = execution.get("outputSchema")
    output_mapping = execution.get("outputMapping")
    if outputs is not None:
        if not isinstance(outputs, dict) or outputs.get("type") != "object":
            raise ManifestError(f"{source}.execution.outputSchema must be an object JSON Schema")
        _validate_json_schema(outputs, f"{source}.execution.outputSchema")
        if not isinstance(outputs.get("properties"), dict):
            raise ManifestError(f"{source}.execution.outputSchema.properties must be an object")
        _validate_output_display(
            outputs["properties"], f"{source}.execution.outputSchema.properties"
        )
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
    _validate_template_syntax(steps, f"{source}.execution.steps")
    if output_mapping is not None:
        _validate_template_syntax(
            output_mapping, f"{source}.execution.outputMapping"
        )
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
        on_skip = step.get("onSkip")
        if on_skip is not None:
            if when is None:
                raise ManifestError(f"{location}.onSkip requires a conditional when clause")
            if not isinstance(on_skip, dict):
                raise ManifestError(f"{location}.onSkip must be an object")
            candidate = on_skip.get("output", on_skip)
            if not isinstance(candidate, dict):
                raise ManifestError(f"{location}.onSkip.output must be an object")
            if "private_refs" in candidate:
                raise ManifestError(f"{location}.onSkip cannot declare private_refs")
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
    if agent.get("kind") == "platform_assistant":
        return False
    validation = agent.get("validation")
    deterministic_runtime = bool(
        isinstance(validation, dict)
        and validation.get("acceptanceMode") == "deterministic_runtime"
        and validation.get("freeQueryComparison") == "NOT_TESTED"
    )
    return bool(
        agent.get("execution")
        and isinstance(validation, dict)
        and validation.get("verdict") == "PASS"
        and validation.get("executable") is True
        and (validation.get("freeQueryComparison") == "MATCH" or deterministic_runtime)
        and validation.get("fixedAgentComparison") == "MATCH"
    )


def _validate_template_syntax(value: Any, source: str) -> None:
    if isinstance(value, dict):
        for name, child in value.items():
            _validate_template_syntax(child, f"{source}.{name}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_template_syntax(child, f"{source}[{index}]")
        return
    if not isinstance(value, str) or ("{{" not in value and "}}" not in value):
        return
    remainder = _TEMPLATE_EXPRESSION.sub("", value)
    if "{{" in remainder or "}}" in remainder:
        raise ManifestError(f"{source} contains a malformed template expression")


def _localized_titles(
    properties: Any, source: str, *, exclude_workflow_only: bool = False
) -> dict[str, list[str]]:
    if not isinstance(properties, dict):
        raise ManifestError(f"{source}.properties must be an object")
    values = {"zh": [], "en": []}
    for name, schema in properties.items():
        if not isinstance(schema, dict):
            raise ManifestError(f"{source}.properties.{name} must be an object")
        if exclude_workflow_only and (
            schema.get("x-sapba-workflow-only") is True
            or schema.get("x-sapba-internal") is True
        ):
            continue
        title = schema.get("title")
        if not isinstance(title, dict) or not all(str(title.get(locale) or "").strip() for locale in values):
            raise ManifestError(f"{source}.properties.{name}.title must be bilingual")
        for locale in values:
            values[locale].append(str(title[locale]))
    return values


def _validate_public_input_title_languages(properties: Any, source: str) -> None:
    if not isinstance(properties, dict):
        raise ManifestError(f"{source}.properties must be an object")
    for name, schema in properties.items():
        location = f"{source}.properties.{name}"
        if not isinstance(schema, dict):
            raise ManifestError(f"{location} must be an object")
        if (
            schema.get("x-sapba-workflow-only") is True
            or schema.get("x-sapba-internal") is True
        ):
            continue
        title = schema.get("title")
        if not isinstance(title, dict) or not all(
            str(title.get(locale) or "").strip() for locale in ("zh", "en")
        ):
            raise ManifestError(f"{location}.title must be bilingual")
        zh_title = str(title["zh"]).strip()
        en_title = str(title["en"]).strip()
        if _HAN_CHARACTER.search(zh_title) is None:
            raise ManifestError(
                f"{location}.title.zh must contain a Chinese business label"
            )
        if _HAN_CHARACTER.search(en_title) is not None:
            raise ManifestError(
                f"{location}.title.en must not contain Chinese characters"
            )
        nested_properties = schema.get("properties")
        if nested_properties is not None:
            _validate_public_input_title_languages(nested_properties, location)
        items = schema.get("items")
        if isinstance(items, dict) and items.get("properties") is not None:
            _validate_public_input_title_languages(
                items.get("properties"), f"{location}.items"
            )


def _validate_input_server_defaults(properties: dict[str, Any], source: str) -> None:
    for name, schema in properties.items():
        if not isinstance(schema, dict):
            continue
        location = f"{source}.{name}"
        identifier_marker = schema.get("x-sapba-sap-identifier")
        if identifier_marker is not None and not isinstance(identifier_marker, bool):
            raise ManifestError(f"{location}.x-sapba-sap-identifier must be boolean")
        if identifier_marker is True and schema.get("type") != "string":
            raise ManifestError(
                f"{location}.x-sapba-sap-identifier=true requires type=string"
            )
        internal_marker = schema.get("x-sapba-internal")
        if internal_marker is not None and not isinstance(internal_marker, bool):
            raise ManifestError(f"{location}.x-sapba-internal must be boolean")
        marker = schema.get("x-sapba-server-default")
        if marker not in {None, False, True, "business_date"}:
            raise ManifestError(
                f"{location}.x-sapba-server-default must be boolean or business_date"
            )
        if marker == "business_date":
            if schema.get("type") != "string" or schema.get("format") != "date":
                raise ManifestError(
                    f"{location}.x-sapba-server-default=business_date requires a date string"
                )
        sensitive = schema.get("x-sapba-sensitive")
        if sensitive is not None and not isinstance(sensitive, bool):
            raise ManifestError(f"{location}.x-sapba-sensitive must be boolean")
        if sensitive is True:
            if schema.get("type") != "string":
                raise ManifestError(f"{location}.x-sapba-sensitive requires type=string")
            if not str(schema.get("x-sapba-secret-kind") or ""):
                raise ManifestError(
                    f"{location}.x-sapba-secret-kind is required for a sensitive input"
                )
        not_after_business_date = schema.get("x-sapba-not-after-business-date")
        if not_after_business_date is not None and not isinstance(
            not_after_business_date, bool
        ):
            raise ManifestError(
                f"{location}.x-sapba-not-after-business-date must be boolean"
            )
        if not_after_business_date is True and (
            schema.get("type") != "string" or schema.get("format") != "date"
        ):
            raise ManifestError(
                f"{location}.x-sapba-not-after-business-date requires a date string"
            )
        if marker == "business_date":
            continue
        if marker is not True:
            continue
        if "default" not in schema:
            raise ManifestError(
                f"{location}.x-sapba-server-default=true requires a default value"
            )
        _validate_schema_default(schema["default"], schema, location)


def _validate_schema_default(value: Any, schema: dict[str, Any], source: str) -> None:
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    except ValidationError as exc:
        messages = {
            "pattern": "default does not match pattern",
            "minLength": "default is shorter than minLength",
            "maxLength": "default exceeds maxLength",
            "minimum": "default is less than minimum",
            "maximum": "default exceeds maximum",
            "type": "default has the wrong type",
        }
        message = messages.get(str(exc.validator), "default does not satisfy its JSON Schema")
        raise ManifestError(f"{source}.{message}") from exc
    except SchemaError as exc:
        raise ManifestError(f"{source}.default does not satisfy its JSON Schema") from exc
    value_type = schema.get("type")
    if value_type == "string":
        if not isinstance(value, str):
            raise ManifestError(f"{source}.default must be a string")
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        pattern = schema.get("pattern")
        if isinstance(minimum, int) and len(value) < minimum:
            raise ManifestError(f"{source}.default is shorter than minLength")
        if isinstance(maximum, int) and len(value) > maximum:
            raise ManifestError(f"{source}.default is longer than maxLength")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            raise ManifestError(f"{source}.default does not match pattern")
        if schema.get("format") == "date":
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ManifestError(f"{source}.default must be an ISO date") from exc
        return
    if value_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ManifestError(f"{source}.default must be an integer")
    elif value_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ManifestError(f"{source}.default must be a number")
    elif value_type == "boolean":
        if not isinstance(value, bool):
            raise ManifestError(f"{source}.default must be boolean")
    elif value_type == "array":
        if not isinstance(value, list):
            raise ManifestError(f"{source}.default must be an array")
    elif value_type == "object" and not isinstance(value, dict):
        raise ManifestError(f"{source}.default must be an object")
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(minimum, (int, float)) and value < minimum:
            raise ManifestError(f"{source}.default is below minimum")
        if isinstance(maximum, (int, float)) and value > maximum:
            raise ManifestError(f"{source}.default is above maximum")


def _validate_json_schema(schema: dict[str, Any], source: str) -> None:
    try:
        Draft202012Validator.check_schema(validator_schema(schema))
    except SchemaError as exc:
        raise ManifestError(f"{source} is not a valid JSON Schema") from exc


def validator_schema(value: Any) -> Any:
    """Return the standard JSON Schema view of bilingual UI annotations."""

    if isinstance(value, list):
        return [validator_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, child in value.items():
        if key in {"title", "description"} and isinstance(child, dict):
            result[key] = str(child.get("en") or child.get("zh") or "")
        else:
            result[key] = validator_schema(child)
    return result


def _validate_output_display(properties: dict[str, Any], source: str) -> None:
    allowed_formats = {"text", "enum", "enum_list", "status"}
    for name, schema in properties.items():
        if not isinstance(schema, dict):
            continue
        enum_values = schema.get("enum")
        items = schema.get("items")
        if enum_values is None and isinstance(items, dict):
            enum_values = items.get("enum")
        display = schema.get("x-sapba-display")
        if display is None:
            if isinstance(enum_values, list):
                raise ManifestError(
                    f"{source}.{name}.x-sapba-display must localize every public enum"
                )
            continue
        location = f"{source}.{name}.x-sapba-display"
        if not isinstance(display, dict):
            raise ManifestError(f"{location} must be an object")
        if "visible" in display and not isinstance(display["visible"], bool):
            raise ManifestError(f"{location}.visible must be boolean")
        display_format = str(display.get("format") or "text")
        if display_format not in allowed_formats:
            raise ManifestError(f"{location}.format is invalid")
        labels = display.get("labels")
        if display_format == "enum_list":
            enum_values = items.get("enum") if isinstance(items, dict) else None
        if isinstance(enum_values, list) and not isinstance(labels, dict):
            raise ManifestError(
                f"{location}.labels must provide bilingual text for every public enum value"
            )
        if display_format in {"enum", "enum_list"} and not isinstance(labels, dict):
            raise ManifestError(f"{location}.labels must be an object")
        if isinstance(labels, dict):
            for value, label in labels.items():
                if not str(value):
                    raise ManifestError(f"{location}.labels contains an empty code")
                if not isinstance(label, dict) or not all(
                    str(label.get(locale) or "").strip() for locale in ("zh", "en")
                ):
                    raise ManifestError(
                        f"{location}.labels.{value} must be bilingual"
                    )
            if isinstance(enum_values, list):
                missing = [str(item) for item in enum_values if str(item) not in labels]
                if missing:
                    raise ManifestError(
                        f"{location}.labels is missing: " + ", ".join(missing)
                    )


def _validate_page_contract(agent: dict[str, Any], inputs: dict[str, Any], source: str) -> None:
    if "SAP ECC" in (agent.get("systems") or []):
        raise ManifestError(f"{source}.systems must not advertise SAP ECC")
    _validate_public_input_title_languages(
        inputs.get("properties"), f"{source}.execution.inputSchema"
    )
    expected_inputs = _localized_titles(
        inputs.get("properties"),
        f"{source}.execution.inputSchema",
        exclude_workflow_only=True,
    )
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
            "blankBusinessKeyFields",
            "compositeBlankFields",
            "nonBlockingObservationCodes",
        ):
            if not isinstance(value.get(field, []), list):
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
        if not isinstance(value.get("factDefinitions", {}), dict):
            raise ManifestError(
                f"{source}.execution.acceptance.factDefinitions must be an object"
            )
        if not isinstance(value.get("compositeKeyParts", {}), dict):
            raise ManifestError(
                f"{source}.execution.acceptance.compositeKeyParts must be an object"
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
        if not isinstance(value.get("testDataQualificationDefinition", ""), str):
            raise ManifestError(
                f"{source}.execution.acceptance.testDataQualificationDefinition must be text"
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
    if value.get("acceptanceMode") is not None and value.get("acceptanceMode") not in {
        "three_stage",
        "deterministic_runtime",
    }:
        raise ManifestError(f"{source}.validation.acceptanceMode is invalid")
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
