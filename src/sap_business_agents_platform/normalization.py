from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


class SapInputNormalizationError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "sap_input_normalization_failed",
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class FieldReference:
    service_name: str
    odata_version: str
    entity_set: str
    field: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (
            self.service_name,
            self.odata_version,
            self.entity_set,
            self.field,
        )


class SapValueNormalizer:
    """Normalize user strings without guessing SAP conversion exits.

    Natural-language text is only stripped. Structured SAP values are upper-cased
    solely when live metadata, an exact compatibility entry, or a declared input
    semantic says that they are upper-case identifiers.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        payload: dict[str, Any] = {}
        if config_path and config_path.is_file():
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        self._exact: dict[tuple[str, str, str, str], dict[str, str]] = {}
        for item in payload.get("fields") or []:
            if not isinstance(item, dict):
                continue
            ref = FieldReference(
                str(item.get("service_name") or ""),
                str(item.get("odata_version") or ""),
                str(item.get("entity_set") or ""),
                str(item.get("field") or ""),
            )
            if all(ref.key):
                self._exact[ref.key] = {
                    "input_normalization": str(item.get("input_normalization") or "preserve"),
                    "sap_semantics": str(item.get("sap_semantics") or ""),
                    "normalization_source": "compatibility_whitelist",
                }
        aliases = payload.get("input_aliases") or {}
        self._input_aliases = {
            str(name): str(mode)
            for name, mode in aliases.items()
            if str(mode) in {"uppercase", "preserve"}
        }

    @staticmethod
    def strip_text(value: str) -> str:
        # str.strip() follows Python's Unicode whitespace definition and includes
        # ordinary spaces, tabs and line breaks without changing internal content.
        return value.strip()

    def normalize_input(
        self,
        value: dict[str, Any],
        schema: dict[str, Any],
        *,
        field_references: dict[str, Iterable[FieldReference]] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise SapInputNormalizationError("Input must be an object.")
        properties = schema.get("properties") or {}
        required = {str(item) for item in schema.get("required") or []}
        normalized: dict[str, Any] = {}
        for name, raw in value.items():
            property_schema = properties.get(name) if isinstance(properties, dict) else None
            property_schema = property_schema if isinstance(property_schema, dict) else {}
            clean = self._normalize_schema_value(
                raw,
                property_schema,
                field_name=str(name),
                required=str(name) in required,
            )
            if clean is _OMIT:
                continue
            if isinstance(clean, str):
                rules = self._input_rules(
                    str(name), (field_references or {}).get(str(name), [])
                )
                if len(rules) > 1:
                    raise SapInputNormalizationError(
                        f"Input {name} maps to conflicting SAP normalization rules.",
                        code="sap_input_normalization_conflict",
                        detail={"field": str(name), "rules": sorted(rules)},
                    )
                if rules == {"uppercase"}:
                    clean = clean.upper()
            normalized[str(name)] = clean
        missing = sorted(name for name in required if name not in normalized)
        if missing:
            raise SapInputNormalizationError(
                "Missing required input: " + ", ".join(missing),
                detail={"fields": missing},
            )
        return normalized

    def normalize_plan(
        self,
        plan: dict[str, Any],
        *,
        metadata: dict[tuple[str, str, str, str], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        normalized = copy.deepcopy(plan)
        for step in _plan_steps(normalized):
            service = str(step.get("service_name") or normalized.get("service_name") or "")
            version = str(step.get("odata_version") or normalized.get("odata_version") or "")
            entity = str(step.get("entity_set") or "")
            for item in step.get("filters") or []:
                if not isinstance(item, dict):
                    continue
                field = str(item.get("field") or "")
                rule = self.field_rule(
                    service, version, entity, field, metadata=metadata
                )
                item["value"] = self.normalize_structured_value(
                    item.get("value"),
                    rule=rule,
                    label=f"{service}/{entity}.{field}",
                    reject_empty_items=str(item.get("operator") or "").lower() == "in",
                )
            for item in step.get("function_parameters") or []:
                if not isinstance(item, dict):
                    continue
                field = str(item.get("name") or "")
                rule = self.field_rule(
                    service, version, entity, field, metadata=metadata
                )
                item["value"] = self.normalize_structured_value(
                    item.get("value"), rule=rule, label=f"{service}/{entity}.{field}"
                )
        return normalized

    def field_rule(
        self,
        service_name: str,
        odata_version: str,
        entity_set: str,
        field: str,
        *,
        metadata: dict[tuple[str, str, str, str], dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        key = (service_name, odata_version, entity_set, field)
        live = (metadata or {}).get(key)
        if isinstance(live, dict):
            mode = str(live.get("input_normalization") or "")
            if mode in {"uppercase", "preserve"}:
                return {
                    "input_normalization": mode,
                    "sap_semantics": str(live.get("sap_semantics") or ""),
                    "normalization_source": str(
                        live.get("normalization_source") or "live_metadata"
                    ),
                }
        return self._exact.get(
            key,
            {
                "input_normalization": "preserve",
                "sap_semantics": "",
                "normalization_source": "none",
            },
        )

    def normalize_field_value(
        self,
        value: Any,
        reference: FieldReference,
        *,
        metadata: dict[tuple[str, str, str, str], dict[str, Any]] | None = None,
    ) -> Any:
        return self.normalize_structured_value(
            value,
            rule=self.field_rule(*reference.key, metadata=metadata),
            label=f"{reference.service_name}/{reference.entity_set}.{reference.field}",
        )

    def normalize_structured_value(
        self,
        value: Any,
        *,
        rule: dict[str, str] | None = None,
        label: str = "value",
        reject_empty_items: bool = False,
    ) -> Any:
        if isinstance(value, str):
            clean = self.strip_text(value)
            if reject_empty_items and not clean:
                raise SapInputNormalizationError(
                    f"{label} contains an empty value.",
                    detail={"field": label},
                )
            return clean.upper() if (rule or {}).get("input_normalization") == "uppercase" else clean
        if isinstance(value, list):
            result: list[Any] = []
            for child in value:
                clean = self.normalize_structured_value(
                    child, rule=rule, label=label, reject_empty_items=True
                )
                result.append(clean)
            return result
        return value

    def describe_field(
        self,
        service_name: str,
        odata_version: str,
        entity_set: str,
        field: dict[str, Any],
    ) -> dict[str, str]:
        display_format = str(field.get("display_format") or "")
        is_upper = field.get("is_uppercase") is True or display_format.lower() == "uppercase"
        if is_upper:
            return {
                "display_format": "UpperCase",
                "sap_semantics": str(field.get("sap_semantics") or "sap_identifier"),
                "input_normalization": "uppercase",
                "normalization_source": "odata_v4_common_isuppercase"
                if field.get("is_uppercase") is True and not display_format
                else "odata_v2_display_format",
            }
        configured = self.field_rule(service_name, odata_version, entity_set, str(field.get("name") or ""))
        return {
            "display_format": display_format,
            **configured,
        }

    def _input_rules(
        self, name: str, references: Iterable[FieldReference]
    ) -> set[str]:
        rules = {
            self.field_rule(*reference.key)["input_normalization"]
            for reference in references
            if self.field_rule(*reference.key)["normalization_source"] != "none"
        }
        alias = self._input_aliases.get(name)
        if not rules and alias:
            rules.add(alias)
        return rules

    def _normalize_schema_value(
        self,
        value: Any,
        schema: dict[str, Any],
        *,
        field_name: str,
        required: bool,
    ) -> Any:
        if isinstance(value, str):
            clean = self.strip_text(value)
            if not clean:
                if required:
                    raise SapInputNormalizationError(
                        f"Missing required input: {field_name}",
                        detail={"fields": [field_name]},
                    )
                return _OMIT
            mode = str(schema.get("x-sapba-input-normalization") or "")
            return clean.upper() if mode == "uppercase" else clean
        if isinstance(value, list):
            item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {}
            result: list[Any] = []
            for index, child in enumerate(value):
                clean = self._normalize_schema_value(
                    child,
                    item_schema,
                    field_name=f"{field_name}[{index}]",
                    required=True,
                )
                result.append(clean)
            return result
        if isinstance(value, dict):
            child_schema = schema if schema.get("type") == "object" else {}
            return self.normalize_input(value, child_schema)
        return value


def discover_agent_input_references(agent: dict[str, Any]) -> dict[str, list[FieldReference]]:
    found: dict[str, list[FieldReference]] = {}
    template = re.compile(r"^\{\{input\.([A-Za-z0-9_]+)\}\}$")

    def visit(node: Any, plan: dict[str, Any] | None = None) -> None:
        if isinstance(node, dict):
            active = node if "service_name" in node and "entity_set" in node else plan
            match = template.fullmatch(str(node.get("value") or ""))
            if match and active and node.get("field"):
                reference = FieldReference(
                    str(active.get("service_name") or ""),
                    str(active.get("odata_version") or ""),
                    str(active.get("entity_set") or ""),
                    str(node.get("field") or ""),
                )
                found.setdefault(match.group(1), []).append(reference)
            for child in node.values():
                visit(child, active)
        elif isinstance(node, list):
            for child in node:
                visit(child, plan)

    visit((agent.get("execution") or {}).get("steps") or [])
    return found


def _plan_steps(plan: dict[str, Any]) -> list[dict[str, Any]]:
    steps = plan.get("steps")
    if isinstance(steps, list):
        return [item for item in steps if isinstance(item, dict)]
    return [plan]


_OMIT = object()
