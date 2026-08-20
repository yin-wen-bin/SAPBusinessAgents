from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


EndpointKey = tuple[str, str, str, str]


@dataclass(frozen=True, slots=True)
class RelationshipEndpoint:
    service_name: str
    odata_version: str
    entity_set: str
    field: str

    @property
    def key(self) -> EndpointKey:
        return (self.service_name, self.odata_version, self.entity_set, self.field)

    def as_dict(self) -> dict[str, str]:
        return {
            "service_name": self.service_name,
            "odata_version": self.odata_version,
            "entity_set": self.entity_set,
            "field": self.field,
        }


@dataclass(frozen=True, slots=True)
class _PlanNode:
    node_id: str
    scope_id: str
    local_step_id: str
    endpoint: RelationshipEndpoint
    filters: tuple[dict[str, Any], ...]
    bindings: tuple[dict[str, Any], ...]
    order: int


class RelationshipCatalog:
    """Machine-readable business-key semantics for guarded cross-entity queries."""

    def __init__(
        self,
        *,
        schema_version: str,
        field_semantics: dict[EndpointKey, str],
        relationships: list[dict[str, Any]],
    ) -> None:
        self.schema_version = schema_version
        self.field_semantics = field_semantics
        self.relationships = relationships
        self._allowed: set[tuple[str, EndpointKey, EndpointKey]] = set()
        for relationship in relationships:
            source = _endpoint(relationship.get("source"), "relationship source")
            target = _endpoint(relationship.get("target"), "relationship target")
            modes = relationship.get("modes") or ["binding"]
            if not isinstance(modes, list) or not modes:
                raise ValueError("Relationship modes must be a non-empty array.")
            for mode in modes:
                normalized = str(mode).strip()
                if normalized not in {"binding", "literal"}:
                    raise ValueError(f"Unsupported relationship mode: {normalized}")
                self._allowed.add((normalized, source.key, target.key))

    @classmethod
    def empty(cls) -> "RelationshipCatalog":
        return cls(schema_version="2.0", field_semantics={}, relationships=[])

    @classmethod
    def load(cls, path: Path) -> "RelationshipCatalog":
        if not path.is_file():
            return cls.empty()
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != "2.0":
            raise ValueError("Business relationship catalog must use schema_version 2.0.")
        semantics: dict[EndpointKey, str] = {}
        fields = raw.get("field_semantics") or []
        if not isinstance(fields, list):
            raise ValueError("field_semantics must be an array.")
        for item in fields:
            endpoint = _endpoint(item, "field semantic")
            semantic = str(item.get("semantic") or "").strip()
            if not semantic:
                raise ValueError(f"Missing semantic for {endpoint.key}.")
            previous = semantics.get(endpoint.key)
            if previous and previous != semantic:
                raise ValueError(f"Conflicting semantics for {endpoint.key}.")
            semantics[endpoint.key] = semantic
        relationships = raw.get("relationships") or []
        if not isinstance(relationships, list):
            raise ValueError("relationships must be an array.")
        return cls(
            schema_version=str(raw["schema_version"]),
            field_semantics=semantics,
            relationships=[dict(item) for item in relationships if isinstance(item, dict)],
        )

    def snapshot_for(self, refs: set[tuple[str, str, str]]) -> dict[str, Any]:
        fields = [
            {
                "service_name": service_name,
                "odata_version": odata_version,
                "entity_set": entity_set,
                "field": field,
                "semantic": semantic,
            }
            for (service_name, odata_version, entity_set, field), semantic in sorted(
                self.field_semantics.items()
            )
            if (service_name, odata_version, entity_set) in refs
        ]
        relationships: list[dict[str, Any]] = []
        for relationship in self.relationships:
            source = _endpoint(relationship.get("source"), "relationship source")
            target = _endpoint(relationship.get("target"), "relationship target")
            if (
                (source.service_name, source.odata_version, source.entity_set) in refs
                and (target.service_name, target.odata_version, target.entity_set) in refs
            ):
                relationships.append(relationship)
        return {
            "schema_version": self.schema_version,
            "field_semantics": fields,
            "relationships": relationships,
        }

    def snapshot(self) -> dict[str, Any]:
        refs = {
            (service_name, odata_version, entity_set)
            for service_name, odata_version, entity_set, _field in self.field_semantics
        }
        return self.snapshot_for(refs)

    def validate_plans(
        self,
        plans: Iterable[tuple[str, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        nodes, local_nodes = _flatten_plans(plans)
        issues_by_step: dict[str, list[dict[str, Any]]] = {}

        for node in nodes:
            scope_nodes = local_nodes.get(node.scope_id, {})
            for binding in node.bindings:
                source_step_id = str(binding.get("source_step_id") or "").strip()
                source_field = str(binding.get("source_field") or "").strip()
                target_field = str(binding.get("field") or "").strip()
                source = scope_nodes.get(source_step_id)
                if source is None or source.order >= node.order:
                    _add_issue(
                        issues_by_step,
                        node.node_id,
                        {
                            "code": "relationship_source_step_unavailable",
                            "source_step_id": source_step_id,
                            "target": {**node.endpoint.as_dict(), "field": target_field},
                        },
                    )
                    continue
                source_endpoint = RelationshipEndpoint(
                    source.endpoint.service_name,
                    source.endpoint.odata_version,
                    source.endpoint.entity_set,
                    source_field,
                )
                target_endpoint = RelationshipEndpoint(
                    node.endpoint.service_name,
                    node.endpoint.odata_version,
                    node.endpoint.entity_set,
                    target_field,
                )
                if source_endpoint.key == target_endpoint.key:
                    continue
                if not self._is_allowed("binding", source_endpoint.key, target_endpoint.key):
                    _add_issue(
                        issues_by_step,
                        node.node_id,
                        self._relationship_issue(
                            code="relationship_binding_unapproved",
                            mode="binding",
                            source=source_endpoint,
                            target=target_endpoint,
                        ),
                    )

        literal_origins: dict[str, tuple[_PlanNode, RelationshipEndpoint]] = {}
        for node in nodes:
            for item in node.filters:
                if str(item.get("operator") or "eq").lower() != "eq":
                    continue
                field = str(item.get("field") or "").strip()
                endpoint = RelationshipEndpoint(
                    node.endpoint.service_name,
                    node.endpoint.odata_version,
                    node.endpoint.entity_set,
                    field,
                )
                if endpoint.key not in self.field_semantics and not _identifier_like(field):
                    continue
                literal_key = _literal_key(item.get("value"), item.get("value_type"))
                if literal_key is None:
                    continue
                origin = literal_origins.setdefault(literal_key, (node, endpoint))
                origin_node, origin_endpoint = origin
                if origin_node is node or origin_endpoint.key == endpoint.key:
                    continue
                if (
                    origin_endpoint.service_name == endpoint.service_name
                    and origin_endpoint.odata_version == endpoint.odata_version
                    and origin_endpoint.entity_set == endpoint.entity_set
                ):
                    continue
                if self._is_allowed("literal", origin_endpoint.key, endpoint.key):
                    continue
                origin_semantic = self.field_semantics.get(origin_endpoint.key)
                target_semantic = self.field_semantics.get(endpoint.key)
                if origin_semantic and origin_semantic == target_semantic:
                    continue
                _add_issue(
                    issues_by_step,
                    node.node_id,
                    self._relationship_issue(
                        code="relationship_literal_semantic_mismatch",
                        mode="literal",
                        source=origin_endpoint,
                        target=endpoint,
                    ),
                )

        return [
            {
                "step_id": step_id,
                "layer": "business_relationship",
                "status": "rejected",
                "validation_issues": issues,
            }
            for step_id, issues in issues_by_step.items()
        ]

    def _is_allowed(self, mode: str, source: EndpointKey, target: EndpointKey) -> bool:
        return (mode, source, target) in self._allowed

    def _relationship_issue(
        self,
        *,
        code: str,
        mode: str,
        source: RelationshipEndpoint,
        target: RelationshipEndpoint,
    ) -> dict[str, Any]:
        return {
            "code": code,
            "mode": mode,
            "source": source.as_dict(),
            "source_semantic": self.field_semantics.get(source.key),
            "target": target.as_dict(),
            "target_semantic": self.field_semantics.get(target.key),
            "message": (
                "The cross-entity business-key relationship is not approved by the local "
                "relationship contract."
            ),
        }


def _endpoint(value: Any, label: str) -> RelationshipEndpoint:
    if not isinstance(value, dict):
        raise ValueError(f"Invalid {label}.")
    endpoint = RelationshipEndpoint(
        str(value.get("service_name") or "").strip(),
        str(value.get("odata_version") or "").strip(),
        str(value.get("entity_set") or "").strip(),
        str(value.get("field") or "").strip(),
    )
    if not all(endpoint.key):
        raise ValueError(f"Incomplete {label}: {endpoint.key}")
    return endpoint


def _flatten_plans(
    plans: Iterable[tuple[str, dict[str, Any]]],
) -> tuple[list[_PlanNode], dict[str, dict[str, _PlanNode]]]:
    nodes: list[_PlanNode] = []
    local_nodes: dict[str, dict[str, _PlanNode]] = {}
    order = 0
    for scope_id, plan in plans:
        nested = plan.get("steps")
        candidates = (
            [item for item in nested if isinstance(item, dict)]
            if isinstance(nested, list) and nested
            else [plan]
        )
        scope_nodes: dict[str, _PlanNode] = {}
        for index, candidate in enumerate(candidates, start=1):
            order += 1
            local_step_id = str(
                candidate.get("step_id") or candidate.get("id") or f"step_{index}"
            )
            service_name = str(
                candidate.get("service_name") or plan.get("service_name") or ""
            ).strip()
            odata_version = str(
                candidate.get("odata_version") or plan.get("odata_version") or ""
            ).strip()
            entity_set = str(candidate.get("entity_set") or "").strip()
            node_id = f"{scope_id}/{local_step_id}" if candidate is not plan else scope_id
            node = _PlanNode(
                node_id=node_id,
                scope_id=scope_id,
                local_step_id=local_step_id,
                endpoint=RelationshipEndpoint(service_name, odata_version, entity_set, ""),
                filters=tuple(
                    item for item in (candidate.get("filters") or []) if isinstance(item, dict)
                ),
                bindings=tuple(
                    item
                    for item in (candidate.get("filter_from_previous") or [])
                    if isinstance(item, dict)
                ),
                order=order,
            )
            nodes.append(node)
            scope_nodes[local_step_id] = node
        local_nodes[scope_id] = scope_nodes
    return nodes, local_nodes


def _identifier_like(field: str) -> bool:
    return bool(re.search(r"(?:ID|Document|Order|Invoice|Advice|Reference)$", field, re.I))


def _literal_key(value: Any, value_type: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    if not text:
        return None
    return json.dumps([str(value_type or ""), text], ensure_ascii=False)


def _add_issue(
    issues_by_step: dict[str, list[dict[str, Any]]],
    step_id: str,
    issue: dict[str, Any],
) -> None:
    issues_by_step.setdefault(step_id, []).append(issue)
