from __future__ import annotations

import json
import re
import time
import uuid
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from .base import SapReadError
from .odata_catalog import (
    ODataCatalogError,
    ODataServiceBinding,
    ODataServiceRegistry,
    normalize_odata_version,
)


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SERVICE = re.compile(r"^[A-Za-z0-9_]+(?:;v=[0-9]+)?$")
_OPERATORS = {"eq", "ne", "gt", "ge", "lt", "le", "contains", "in"}
_METHOD_KEYS = {"http_method", "httpMethod", "method"}
_SAP_NS = "http://www.sap.com/Protocols/SAPData"


def _sortable_stable_value(value: Any) -> tuple[int, int, str, str]:
    """Use natural ordering for SAP ALPHA-style digit-only string keys."""
    text = str(value or "")
    # SAP Gateway sorts an empty character key before populated values.  Keep
    # that ordering when a compound stable key contains optional components
    # such as Plant, otherwise a valid server-side $orderby is falsely marked
    # non-monotonic when it transitions from an empty Plant to a numeric Plant.
    if not text:
        return (-1, 0, "", "")
    if text.isdecimal():
        normalized = text.lstrip("0") or "0"
        return (0, len(normalized), normalized, text)
    return (1, 0, text, text)


class EmbeddedODataProvider:
    """In-process, GET-only SAP OData V2/V4 provider.

    Planning and business-relationship validation remain in SAPBusinessAgents. This
    provider owns credentials, live metadata checks, request construction, paging,
    and evidence-completeness reporting.
    """

    provider_id = "embedded-odata"
    provider_version = "2.0.0"

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        client: str = "",
        verify_ssl: bool = True,
        auth_type: str = "basic",
        timeout_seconds: float = 60,
        max_results: int = 5000,
        page_size: int = 1000,
        relationship_catalog_path: Path | None = None,
        service_registry_path: Path | None = None,
        catalog_seed_path: Path | None = None,
        curated_catalog_path: Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.client = client.strip()
        self.verify_ssl = verify_ssl
        self.auth_type = auth_type.strip().lower() or "basic"
        self.timeout_seconds = timeout_seconds
        self.max_results = max(1, max_results)
        self.page_size = max(1, min(page_size, self.max_results))
        self.relationship_catalog_path = relationship_catalog_path
        self.service_registry_path = service_registry_path
        self.catalog_seed_path = catalog_seed_path
        self.transport = transport
        self._cases: dict[str, dict[str, Any]] = {}
        self._metadata_cache: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self._service_registry = ODataServiceRegistry.load(service_registry_path)
        self._catalog_seed = self._load_catalog_seed(catalog_seed_path)
        self._merge_curated_catalog(curated_catalog_path)

    async def health(self) -> dict[str, Any]:
        issues: list[dict[str, str]] = []
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            issues.append({"code": "sap_base_url_missing", "message": "SAP_BASE_URL is not configured."})
        if not self.username or not self.password:
            issues.append(
                {
                    "code": "sap_credentials_missing",
                    "message": "SAP_USERNAME and SAP_PASSWORD are required.",
                }
            )
        if self.auth_type != "basic":
            issues.append(
                {
                    "code": "sap_auth_type_unsupported",
                    "message": "The embedded prototype currently supports basic authentication only.",
                }
            )
        registry_services = self._service_registry.public_services()
        if not registry_services:
            issues.append(
                {
                    "code": "odata_registry_empty",
                    "message": "No versioned OData services are registered.",
                }
            )
        return {
            "ok": not issues,
            "data": {
                "provider_id": self.provider_id,
                "provider_version": self.provider_version,
                "capability": "sap_read.v2",
                "configured": not issues,
                "read_only": True,
                "supported_odata_versions": ["2.0", "4.0"],
                "registered_services": len(registry_services),
                "sap_base_url_configured": bool(self.base_url),
                "sap_credentials_configured": bool(self.username and self.password),
                "live_probe_performed": False,
            },
            "validation_issues": issues,
        }

    async def catalog(
        self, query: str = "", skip: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        entries: dict[tuple[str, str, str], dict[str, Any]] = {}
        public_registry = {
            (item["service_name"], item["odata_version"]): item
            for item in self._service_registry.public_services()
        }
        for service_record in self._catalog_seed.get("services") or []:
            if not isinstance(service_record, dict):
                continue
            service = str(service_record.get("service_name") or "")
            version = str(service_record.get("odata_version") or "")
            registry_record = public_registry.get((service, version))
            if not registry_record:
                continue
            terms_by_entity: dict[str, list[str]] = defaultdict(list)
            for term in service_record.get("business_terms") or []:
                if not isinstance(term, dict):
                    continue
                entity = str(term.get("entity_set") or "")
                text = str(term.get("term") or "")
                if entity and text and not _looks_mojibake(text) and text not in terms_by_entity[entity]:
                    terms_by_entity[entity].append(text)
            for entity_record in service_record.get("entities") or []:
                if not isinstance(entity_record, dict):
                    continue
                entity = str(entity_record.get("entity_set") or "")
                if not entity:
                    continue
                fields = [
                    str(field.get("field_name") or "")
                    for field in entity_record.get("fields") or []
                    if isinstance(field, dict) and str(field.get("field_name") or "")
                ]
                entries[(service, version, entity)] = {
                    **registry_record,
                    "entity_set": entity,
                    "description": entity_record.get("description") or "",
                    "business_aliases": entity_record.get("business_aliases") or [],
                    "business_terms": terms_by_entity.get(entity, [])[:200],
                    "fields": fields,
                    "supported_operations": ["GET"],
                    "read_only": True,
                    "schema_authority": "live_metadata_required_before_execution",
                    "provider_id": self.provider_id,
                }
        for curated in self._catalog_seed.get("curated_search") or []:
            if not isinstance(curated, dict):
                continue
            service = str(curated.get("service_name") or "")
            version = str(curated.get("odata_version") or "")
            entity = str(curated.get("entity_set") or "")
            registry_record = public_registry.get((service, version))
            if not registry_record or not entity:
                continue
            entry = entries.setdefault(
                (service, version, entity),
                {
                    **registry_record,
                    "entity_set": entity,
                    "description": "",
                    "business_aliases": [],
                    "business_terms": [],
                    "fields": [],
                    "supported_operations": ["GET"],
                    "read_only": True,
                    "schema_authority": "live_metadata_required_before_execution",
                    "provider_id": self.provider_id,
                },
            )
            terms = curated.get("terms") or {}
            for locale in ("zh", "en"):
                localized_terms = terms.get(locale) if isinstance(terms, dict) else []
                for term in localized_terms or []:
                    text = str(term).strip()
                    if text and not _looks_mojibake(text) and text not in entry["business_terms"]:
                        entry["business_terms"].append(text)
            for field in curated.get("candidate_fields") or []:
                name = str(field).strip()
                if name and name not in entry["fields"]:
                    entry["fields"].append(name)
                if name:
                    entry.setdefault("curated_fields", [])
                    if name not in entry["curated_fields"]:
                        entry["curated_fields"].append(name)
            entry.setdefault("curated_topics", []).append(str(curated.get("topic") or ""))
            entry.setdefault("search_purpose", curated.get("purpose") or {})
        payload = self._relationship_payload()
        for item in payload.get("field_semantics") or []:
            if not isinstance(item, dict):
                continue
            service = str(item.get("service_name") or "")
            version = str(item.get("odata_version") or "")
            entity = str(item.get("entity_set") or "")
            field = str(item.get("field") or "")
            registry_record = public_registry.get((service, version))
            if not service or not version or not entity or not field or not registry_record:
                continue
            entry = entries.setdefault(
                (service, version, entity),
                {
                    **registry_record,
                    "service_name": service,
                    "odata_version": version,
                    "entity_set": entity,
                    "fields": [],
                    "supported_operations": ["GET"],
                    "read_only": True,
                    "schema_authority": "live_metadata_required_before_execution",
                    "provider_id": self.provider_id,
                },
            )
            if field not in entry["fields"]:
                entry["fields"].append(field)
        items = list(entries.values())
        needle = query.casefold().strip()
        if needle:
            tokens = _catalog_query_tokens(needle)
            ranked: list[tuple[int, str, str, dict[str, Any]]] = []
            minimum_hits = 2 if len(tokens) >= 3 else 1
            for item in items:
                searchable = _catalog_searchable_text(item)
                token_hits = sum(token in searchable for token in tokens)
                if token_hits < minimum_hits:
                    continue
                exact_term = any(
                    needle == str(term).casefold()
                    for term in item.get("business_terms") or []
                )
                score = token_hits * 10
                score += 40 if token_hits == len(tokens) else 0
                score += 80 if needle in searchable else 0
                score += 120 if exact_term else 0
                score += 20 if item.get("curated_topics") else 0
                ranked.append(
                    (
                        -score,
                        str(item.get("service_name") or ""),
                        str(item.get("entity_set") or ""),
                        item,
                    )
                )
            items = [item for _score, _service, _entity, item in sorted(ranked)]
        page = items[max(0, skip) : max(0, skip) + max(1, limit)]
        return {
            "ok": True,
            "data": {
                "items": page,
                "total_count": len(items),
                "provider_id": self.provider_id,
                "catalog_scope": "sanitized_seed_and_approved_relationship_entities",
            },
        }

    async def guidance(self, query: str) -> dict[str, Any]:
        return {
            "ok": True,
            "data": {
                "query": query,
                "provider_id": self.provider_id,
                "evidence_policy": "live_schema_required_get_only",
                "odata_version_policy": (
                    "Every service reference must declare 2.0 or 4.0 and match the "
                    "registered binding plus live metadata."
                ),
                "catalog_matches": [
                    item
                    for item in (await self.catalog(query=query, limit=20)).get("data", {}).get("items", [])
                ],
                "source_complete_policy": (
                    "Explicit top bounds are incomplete; unbounded plans page until the source "
                    "ends or the configured result ceiling is reached."
                ),
            },
        }

    async def schema(
        self,
        service_name: str,
        entity_sets: list[str] | str,
        query: str = "",
        *,
        odata_version: str,
        include_fields: bool = True,
        max_fields: int = 5000,
    ) -> dict[str, Any]:
        del query
        self._require_configured()
        service = self._validate_service(service_name)
        version = self._normalize_version(odata_version)
        binding = self._resolve_binding(service, version)
        requested = [entity_sets] if isinstance(entity_sets, str) else list(entity_sets)
        requested = list(dict.fromkeys(str(item) for item in requested))
        if not requested:
            raise SapReadError("At least one entity set is required.", code="sap_schema_entity_missing")
        for entity in requested:
            self._validate_identifier(entity, "entity_set")

        started = time.perf_counter()
        response = await self._request(
            binding.metadata_path,
            params={},
            accept="application/xml",
        )
        try:
            detected_version, parsed = self._parse_metadata(response.text)
        except (ET.ParseError, ValueError) as exc:
            raise SapReadError(
                "SAP returned invalid OData metadata.",
                code="sap_metadata_invalid",
                detail={"service_name": service, "odata_version": version, "message": str(exc)},
            ) from exc
        header_version = str(response.headers.get("OData-Version") or response.headers.get("DataServiceVersion") or "").strip()
        header_observed_version = self._normalize_observed_version(header_version)
        if header_observed_version and header_observed_version != detected_version:
            raise SapReadError(
                "Live OData metadata and response headers declare conflicting versions.",
                code="odata_version_mismatch",
                detail={
                    "service_name": service,
                    "declared_odata_version": version,
                    "metadata_odata_version": detected_version,
                    "header_odata_version": header_observed_version,
                },
            )
        observed_version = header_observed_version or detected_version
        if observed_version != version:
            raise SapReadError(
                "Registered OData version does not match live metadata.",
                code="odata_version_mismatch",
                detail={
                    "service_name": service,
                    "declared_odata_version": version,
                    "observed_odata_version": observed_version,
                },
            )
        cache_key = (service, version)
        self._metadata_cache[cache_key] = parsed

        issues: list[dict[str, Any]] = []
        entities: list[dict[str, Any]] = []
        fields: list[dict[str, Any]] = []
        for entity in requested:
            descriptor = parsed.get(entity)
            if descriptor is None:
                issues.append(
                    {
                        "code": "schema_drift_entity_unavailable",
                        "service_name": service,
                        "odata_version": version,
                        "entity_set": entity,
                    }
                )
                continue
            entities.append(
                {
                    "service_name": service,
                    "odata_version": version,
                    "entity_set": entity,
                    "entity_kind": descriptor.get("kind", "entity_set"),
                    "key_fields": descriptor["keys"],
                    "function_parameters": descriptor.get("parameters", []),
                    "supports_filter": descriptor.get("kind", "entity_set") == "entity_set",
                    "supports_orderby": descriptor.get("kind", "entity_set") == "entity_set",
                    "supports_top": descriptor.get("kind", "entity_set") == "entity_set",
                    "runtime_available": True,
                    "executable": True,
                }
            )
            if include_fields:
                for field in descriptor["fields"]:
                    if len(fields) >= max_fields:
                        break
                    fields.append(
                        {
                            "service_name": service,
                            "odata_version": version,
                            "entity_set": entity,
                            "field_name": field["name"],
                            "data_type": field["type"],
                            "nullable": field["nullable"],
                            "selectable": field["selectable"],
                            # Some SAP Gateway services publish filterable=false but still
                            # execute $filter correctly. Treat that annotation as advisory;
                            # sortable remains enforced because it is required for stable paging.
                            "filterable": True,
                            "sortable": field["sortable"],
                            "metadata_filterable": field["filterable"],
                            "metadata_sortable": field["sortable"],
                            "runtime_available": True,
                            "executable": True,
                        }
                    )
        fields_truncated = include_fields and sum(
            len(parsed[item]["fields"]) for item in requested if item in parsed
        ) > len(fields)
        return {
            "ok": not issues,
            "data": {
                "service": {
                    "service_name": service,
                    "odata_version": version,
                    **binding.public_dict(),
                },
                "entities": entities,
                "fields": fields,
                "schema_authority": True,
                "fields_truncated": fields_truncated,
                "compatibility_status": "compatible" if not issues else "incompatible",
                "metadata_timestamp": datetime.now(timezone.utc).isoformat(),
                "provider_id": self.provider_id,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            },
            "validation_issues": issues,
        }

    async def validate_plan(
        self, plan: dict[str, Any], query: str = ""
    ) -> dict[str, Any]:
        del query
        if not isinstance(plan, dict):
            return self._validation_failure("invalid_plan", "SAP read plan must be an object.")
        issues = self._validate_plan_shape(plan)
        if issues:
            return {"ok": False, "status": "rejected", "validation_issues": issues}

        candidates = self._plan_steps(plan)
        refs_by_service: dict[tuple[str, str], list[str]] = defaultdict(list)
        for step in candidates:
            service = str(step.get("service_name") or plan.get("service_name") or "")
            version = str(step.get("odata_version") or plan.get("odata_version") or "")
            entity = str(step.get("entity_set") or "")
            if entity not in refs_by_service[(service, version)]:
                refs_by_service[(service, version)].append(entity)

        schema_fields: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
        for (service, version), entities in refs_by_service.items():
            try:
                response = await self.schema(service, entities, odata_version=version)
            except SapReadError as exc:
                issues.append(
                    {
                        "code": exc.code,
                        "service_name": service,
                        "odata_version": version,
                        "message": str(exc),
                        "detail": exc.detail,
                    }
                )
                continue
            issues.extend(response.get("validation_issues") or [])
            data = response.get("data") or {}
            for field in data.get("fields") or []:
                key = (
                    str(field.get("service_name") or service),
                    str(field.get("odata_version") or version),
                    str(field.get("entity_set") or ""),
                )
                schema_fields.setdefault(key, {})[str(field.get("field_name") or "")] = field

        step_lookup = {
            str(step.get("step_id") or ""): step
            for step in candidates
            if str(step.get("step_id") or "")
        }
        for step in candidates:
            service = str(step.get("service_name") or plan.get("service_name") or "")
            version = str(step.get("odata_version") or plan.get("odata_version") or "")
            entity = str(step.get("entity_set") or "")
            available = schema_fields.get((service, version, entity), {})
            descriptor = self._metadata_cache.get((service, version), {}).get(entity, {})
            if str(step.get("plan_kind") or plan.get("plan_kind") or "direct") in {"function", "function_import"}:
                supplied = {
                    str(item.get("name") or ""): item
                    for item in step.get("function_parameters") or []
                    if isinstance(item, dict)
                }
                expected = {
                    str(item.get("name") or ""): item
                    for item in descriptor.get("parameters") or []
                    if isinstance(item, dict)
                }
                if descriptor.get("kind") not in {"function", "function_import"}:
                    issues.append(
                        {
                            "code": "schema_drift_function_import_unavailable",
                            "service_name": service,
                            "odata_version": version,
                            "entity_set": entity,
                        }
                    )
                if descriptor.get("is_bound") is True:
                    issues.append(
                        {
                            "code": "bound_function_unsupported",
                            "service_name": service,
                            "odata_version": version,
                            "entity_set": entity,
                        }
                    )
                for name in sorted(set(expected).difference(supplied)):
                    issues.append(
                        {
                            "code": "function_parameter_missing",
                            "service_name": service,
                            "odata_version": version,
                            "entity_set": entity,
                            "field": name,
                        }
                    )
                for name in sorted(set(supplied).difference(expected)):
                    issues.append(
                        {
                            "code": "function_parameter_unavailable",
                            "service_name": service,
                            "odata_version": version,
                            "entity_set": entity,
                            "field": name,
                        }
                    )
                continue
            for field_name, use in self._field_uses(step):
                descriptor = available.get(field_name)
                if descriptor is None:
                    issues.append(
                        {
                            "code": "schema_drift_field_unavailable",
                            "service_name": service,
                            "odata_version": version,
                            "entity_set": entity,
                            "field": field_name,
                            "use": use,
                        }
                    )
                    continue
                restriction = {
                    "select": "selectable",
                    "filter": "filterable",
                    "order": "sortable",
                }.get(use)
                if restriction and descriptor.get(restriction) is False:
                    issues.append(
                        {
                            "code": f"schema_field_not_{restriction}",
                            "service_name": service,
                            "odata_version": version,
                            "entity_set": entity,
                            "field": field_name,
                        }
                    )
            for binding in step.get("filter_from_previous") or []:
                source_id = str(binding.get("source_step_id") or "")
                source = step_lookup.get(source_id)
                if source is None:
                    issues.append(
                        {
                            "code": "binding_source_step_unavailable",
                            "step_id": step.get("step_id"),
                            "source_step_id": source_id,
                        }
                    )
                    continue
                source_service = str(source.get("service_name") or plan.get("service_name") or "")
                source_version = str(source.get("odata_version") or plan.get("odata_version") or "")
                source_entity = str(source.get("entity_set") or "")
                source_field = str(binding.get("source_field") or "")
                if source_field not in schema_fields.get((source_service, source_version, source_entity), {}):
                    issues.append(
                        {
                            "code": "schema_drift_binding_source_field_unavailable",
                            "service_name": source_service,
                            "odata_version": source_version,
                            "entity_set": source_entity,
                            "field": source_field,
                            "source_step_id": source_id,
                        }
                    )
        return {
            "ok": not issues,
            "status": "validated" if not issues else "rejected",
            "provider_id": self.provider_id,
            "validation_issues": issues,
        }

    async def execute_plan(
        self,
        plan: dict[str, Any],
        query: str = "",
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        del conversation_id
        validation = await self.validate_plan(plan, query)
        if validation.get("ok") is not True:
            raise SapReadError(
                "The embedded SAP provider rejected the query plan.",
                code="sap_read_plan_rejected",
                detail=validation,
            )
        started = time.perf_counter()
        case_id = f"embedded_{uuid.uuid4().hex[:16]}"
        steps = self._plan_steps(plan)
        step_results: dict[str, dict[str, Any]] = {}
        for index, step in enumerate(steps, start=1):
            step_id = str(step.get("step_id") or f"step_{index}")
            effective = {**step}
            effective.setdefault("service_name", plan.get("service_name"))
            effective.setdefault("odata_version", plan.get("odata_version"))
            result = await self._execute_query(effective, step_results)
            step_results[step_id] = result
        final = next(reversed(step_results.values())) if step_results else self._empty_result()
        source_complete = bool(step_results) and all(
            item.get("source_complete") is True for item in step_results.values()
        )
        source_truncated = any(
            item.get("source_truncated") is True for item in step_results.values()
        )
        response = {
            "ok": True,
            "status": "completed" if source_complete else "inconclusive",
            "case_id": case_id,
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "capability": "sap_read.v2",
            "data": {
                "results": final.get("results") or [],
                "source_complete": source_complete,
                "source_truncated": source_truncated,
            },
            "step_results": step_results,
            "pagination": {
                "total_count": sum(len(item.get("results") or []) for item in step_results.values()),
                "total_count_known": source_complete,
                "has_next": source_truncated,
            },
            "source_complete": source_complete,
            "source_truncated": source_truncated,
            "validation_issues": [
                {"step_id": step_id, **issue}
                for step_id, item in step_results.items()
                for issue in item.get("validation_issues") or []
                if isinstance(issue, dict)
            ],
            "presentation": {
                "text": {
                    "zh": (
                        f"已完成 {len(step_results)} 个严格只读的 SAP 查询步骤；"
                        f"查询数据范围{'完整' if source_complete else '仍存在限制'}。"
                    ),
                    "en": (
                        f"Executed {len(step_results)} GET-only SAP step(s); "
                        f"query-source completeness is {'complete' if source_complete else 'inconclusive'}."
                    ),
                }
            },
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        self._cases[case_id] = response
        return response

    async def execute_get(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self.execute_plan(request)

    async def page(self, case_id: str, skip: int = 0) -> dict[str, Any]:
        del skip
        try:
            return self._cases[case_id]
        except KeyError as exc:
            raise SapReadError("Embedded evidence case was not found.", code="sap_read_case_not_found") from exc

    async def _execute_query(
        self,
        step: dict[str, Any],
        prior: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        service = self._validate_service(str(step.get("service_name") or ""))
        version = self._normalize_version(step.get("odata_version"))
        service_binding = self._resolve_binding(service, version)
        entity = self._validate_identifier(str(step.get("entity_set") or ""), "entity_set")
        if str(step.get("plan_kind") or "direct") in {"function", "function_import"}:
            return await self._execute_function_import(
                service, version, service_binding, entity, step
            )
        descriptor = self._metadata_cache.get((service, version), {}).get(entity, {})
        field_types = {
            str(field.get("name") or ""): str(field.get("type") or "")
            for field in descriptor.get("fields") or []
            if isinstance(field, dict) and field.get("name")
        }
        typed_filters: list[dict[str, Any]] = []
        for raw_filter in step.get("filters") or []:
            item = dict(raw_filter)
            field_type = field_types.get(str(item.get("field") or ""))
            if not item.get("value_type") and field_type:
                item["value_type"] = field_type
            typed_filters.append(item)
        literal_filter = self._literal_filters(typed_filters, version)
        binding_groups = self._binding_filter_groups(
            step.get("filter_from_previous") or [], prior, version
        )
        if binding_groups == [] and step.get("filter_from_previous"):
            return self._empty_result(
                service_name=service, odata_version=version, entity_set=entity
            )
        chunks: list[list[str]] = []
        if binding_groups:
            for offset in range(0, len(binding_groups), 20):
                chunks.append(binding_groups[offset : offset + 20])
        else:
            chunks = [[]]

        all_rows: list[dict[str, Any]] = []
        requests: list[dict[str, Any]] = []
        validation_issues: list[dict[str, Any]] = []
        complete = True
        truncated = False
        for binding_chunk in chunks:
            pieces = list(literal_filter)
            if binding_chunk:
                pieces.append("(" + " or ".join(binding_chunk) + ")")
            result = await self._fetch_all(
                service,
                version,
                service_binding,
                entity,
                step,
                " and ".join(f"({item})" for item in pieces if item),
                remaining=max(0, self.max_results - len(all_rows)),
            )
            all_rows.extend(result["results"])
            requests.extend(result["requests"])
            complete = complete and result["source_complete"]
            truncated = truncated or result["source_truncated"]
            validation_issues.extend(result.get("validation_issues") or [])
            if len(all_rows) >= self.max_results:
                truncated = True
                complete = False
                all_rows = all_rows[: self.max_results]
                break
        aggregate_order = [str(item) for item in step.get("order_by") or []]
        if not aggregate_order and step.get("top") is None:
            sortable_fields = {
                str(field.get("name") or "")
                for field in descriptor.get("fields", [])
                if field.get("sortable") is True
            }
            aggregate_order = [
                key for key in descriptor.get("keys", []) if key in sortable_fields
            ]
        if aggregate_order and all_rows:
            aggregate_values = [
                tuple(str(row.get(field) or "") for field in aggregate_order)
                for row in all_rows
            ]
            if len(aggregate_values) != len(set(aggregate_values)) and not any(
                issue.get("code") == "duplicate_stable_key"
                for issue in validation_issues
            ):
                validation_issues.append(
                    {"code": "duplicate_stable_key", "fields": aggregate_order}
                )
            if validation_issues:
                complete = False
        return {
            "ok": True,
            "service_name": service,
            "odata_version": version,
            "entity_set": entity,
            "results": all_rows,
            "result_count": len(all_rows),
            "source_complete": complete,
            "source_truncated": truncated,
            "requests": requests,
            "validation_issues": validation_issues,
        }

    async def _execute_function_import(
        self,
        service: str,
        version: str,
        service_binding: ODataServiceBinding,
        function_name: str,
        step: dict[str, Any],
    ) -> dict[str, Any]:
        descriptor = self._metadata_cache.get((service, version), {}).get(function_name, {})
        if descriptor.get("kind") in {"action", "action_import"}:
            raise SapReadError(
                "OData actions are forbidden by the GET-only boundary.",
                code="write_operation_rejected",
            )
        expected = {
            str(item.get("name") or ""): str(item.get("type") or "Edm.String")
            for item in descriptor.get("parameters") or []
            if isinstance(item, dict)
        }
        params: dict[str, str] = {}
        for item in step.get("function_parameters") or []:
            name = self._validate_identifier(str(item.get("name") or ""), "function parameter")
            edm_type = expected.get(name, str(item.get("value_type") or "Edm.String"))
            params[name] = self._odata_literal(
                item.get("value"), item.get("value_type") or edm_type, version
            )
        if version == "4.0":
            arguments = ",".join(f"{name}={value}" for name, value in params.items())
            invoke_name = str(descriptor.get("invoke_name") or function_name)
            function_path = f"{service_binding.service_root_path}/{invoke_name}({arguments})"
            request_params: dict[str, str] = {}
        else:
            function_path = f"{service_binding.service_root_path}/{function_name}"
            request_params = params
        response = await self._request(function_path, params=request_params)
        payload = self._response_json(response)
        rows, next_link = self._rows_and_next(payload)
        requests = [
            {
                "http_method": "GET",
                "service_name": service,
                "odata_version": version,
                "entity_set": function_name,
                "request_path": str(response.request.url.copy_with(scheme=None, host=None)),
                "http_status": response.status_code,
                "returned_rows": len(rows),
            }
        ]
        source_complete = True
        source_truncated = False
        seen_next_links: set[str] = set()
        while next_link and len(rows) < self.max_results:
            safe_next = self._safe_next_link(next_link, service_binding)
            if safe_next in seen_next_links:
                raise SapReadError(
                    "SAP paging returned a repeated next-link.",
                    code="sap_paging_cycle_rejected",
                )
            seen_next_links.add(safe_next)
            response = await self._request(safe_next, params=None)
            page_rows, next_link = self._rows_and_next(self._response_json(response))
            remaining = self.max_results - len(rows)
            rows.extend(page_rows[:remaining])
            requests.append(
                {
                    "http_method": "GET",
                    "service_name": service,
                    "odata_version": version,
                    "entity_set": function_name,
                    "request_path": str(response.request.url.copy_with(scheme=None, host=None)),
                    "http_status": response.status_code,
                    "returned_rows": min(len(page_rows), remaining),
                }
            )
            if len(page_rows) > remaining:
                source_complete = False
                source_truncated = True
                break
        if next_link:
            source_complete = False
            source_truncated = True
        return {
            "ok": True,
            "service_name": service,
            "odata_version": version,
            "entity_set": function_name,
            "entity_kind": descriptor.get("kind") or "function_import",
            "results": rows,
            "result_count": len(rows),
            "source_complete": source_complete,
            "source_truncated": source_truncated,
            "requests": requests,
        }

    async def _fetch_all(
        self,
        service: str,
        version: str,
        service_binding: ODataServiceBinding,
        entity: str,
        step: dict[str, Any],
        filter_expression: str,
        *,
        remaining: int,
    ) -> dict[str, Any]:
        if remaining <= 0:
            return {"results": [], "requests": [], "source_complete": False, "source_truncated": True}
        explicit_top = step.get("top")
        requested_limit = min(int(explicit_top), remaining) if explicit_top is not None else remaining
        params: dict[str, str] = {"$format": "json"}
        select_fields = [str(item) for item in step.get("select_fields") or []]
        order_by = [str(item) for item in step.get("order_by") or []]
        if not order_by and explicit_top is None:
            entity_metadata = self._metadata_cache.get((service, version), {}).get(entity, {})
            sortable_fields = {
                str(field.get("name") or "")
                for field in entity_metadata.get("fields", [])
                if field.get("sortable") is True
            }
            order_by = [
                key for key in entity_metadata.get("keys", []) if key in sortable_fields
            ]
        if select_fields:
            # Stable paging checks operate on returned rows.  SAP does not
            # return fields omitted by an explicit $select, so every ordering
            # key must be included even when the business projection does not
            # display it.
            select_fields = list(dict.fromkeys([*select_fields, *order_by]))
            params["$select"] = ",".join(select_fields)
        if order_by:
            params["$orderby"] = ",".join(order_by)
        if filter_expression:
            params["$filter"] = filter_expression
        params["$top"] = str(min(self.page_size, requested_limit))

        path = f"{service_binding.service_root_path}/{entity}"
        rows: list[dict[str, Any]] = []
        requests: list[dict[str, Any]] = []
        next_url: str | None = path
        next_params: dict[str, str] | None = params
        manual_skip = 0
        source_complete = True
        source_truncated = False
        seen_next_links: set[str] = set()
        while next_url and len(rows) < requested_limit:
            response = await self._request(next_url, params=next_params)
            payload = self._response_json(response)
            page_rows, discovered_next = self._rows_and_next(payload)
            allowed = requested_limit - len(rows)
            rows.extend(page_rows[:allowed])
            requests.append(
                {
                    "http_method": "GET",
                    "service_name": service,
                    "odata_version": version,
                    "entity_set": entity,
                    "request_path": str(response.request.url.copy_with(scheme=None, host=None)),
                    "http_status": response.status_code,
                    "returned_rows": min(len(page_rows), allowed),
                }
            )
            if len(page_rows) > allowed:
                source_truncated = True
                source_complete = False
                break
            if discovered_next:
                next_url = self._safe_next_link(discovered_next, service_binding)
                if next_url in seen_next_links:
                    raise SapReadError(
                        "SAP paging returned a repeated next-link.",
                        code="sap_paging_cycle_rejected",
                    )
                seen_next_links.add(next_url)
                next_params = None
            else:
                requested_page_size = int((next_params or params).get("$top", self.page_size))
                can_continue = (
                    explicit_top is None
                    and len(page_rows) == requested_page_size
                    and len(rows) < requested_limit
                )
                if can_continue and order_by:
                    manual_skip += len(page_rows)
                    next_url = path
                    next_params = {
                        **params,
                        "$skip": str(manual_skip),
                        "$top": str(min(self.page_size, requested_limit - len(rows))),
                    }
                elif can_continue:
                    next_url = None
                    source_complete = False
                    source_truncated = True
                else:
                    next_url = None
        if explicit_top is not None:
            source_complete = False
            source_truncated = source_truncated or bool(next_url)
        elif next_url or (len(rows) >= self.max_results and len(rows) >= requested_limit):
            source_complete = False
            source_truncated = True
        validation_issues: list[dict[str, Any]] = []
        if order_by and rows:
            stable_values = [
                tuple(str(row.get(field) or "") for field in order_by)
                for row in rows
            ]
            if len(stable_values) != len(set(stable_values)):
                validation_issues.append(
                    {
                        "code": "duplicate_stable_key",
                        "fields": order_by,
                    }
                )
            elif [
                tuple(_sortable_stable_value(row.get(field)) for field in order_by)
                for row in rows
            ] != sorted(
                tuple(_sortable_stable_value(row.get(field)) for field in order_by)
                for row in rows
            ):
                validation_issues.append(
                    {
                        "code": "non_monotonic_stable_key",
                        "fields": order_by,
                    }
                )
            if validation_issues:
                source_complete = False
        return {
            "results": rows,
            "requests": requests,
            "source_complete": source_complete,
            "source_truncated": source_truncated,
            "validation_issues": validation_issues,
        }

    async def _request(
        self,
        path_or_url: str,
        *,
        params: dict[str, str] | None,
        accept: str = "application/json",
    ) -> httpx.Response:
        self._require_configured()
        request_target: str | httpx.URL = path_or_url
        request_params = dict(params) if params is not None else None
        if self.client and (request_params is None or "sap-client" not in request_params):
            if request_params is None:
                parsed_target = httpx.URL(path_or_url)
                request_params = dict(parsed_target.params.multi_items())
                request_target = parsed_target.copy_with(query=None)
            request_params["sap-client"] = self.client
        auth = httpx.BasicAuth(self.username, self.password)
        async with httpx.AsyncClient(
            base_url=self.base_url,
            auth=auth,
            headers={"Accept": accept},
            timeout=self.timeout_seconds,
            verify=self.verify_ssl,
            trust_env=False,
            follow_redirects=False,
            transport=self.transport,
        ) as client:
            try:
                response = await client.get(request_target, params=request_params)
            except httpx.TimeoutException as exc:
                raise SapReadError("SAP GET timed out.", code="sap_read_timeout") from exc
            except httpx.HTTPError as exc:
                raise SapReadError(
                    "SAP GET failed before a response was received.",
                    code="sap_read_unavailable",
                    detail={"message": str(exc)},
                ) from exc
        if response.status_code >= 400:
            raise SapReadError(
                f"SAP returned HTTP {response.status_code} for a GET request.",
                code="sap_read_http_error",
                detail={
                    "http_status": response.status_code,
                    "service_path": response.request.url.path,
                    "sap_error": self._safe_sap_error(response),
                },
            )
        return response

    def _require_configured(self) -> None:
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SapReadError("SAP_BASE_URL is not configured.", code="sap_base_url_missing")
        if parsed.username or parsed.password:
            raise SapReadError(
                "Credentials must not be embedded in SAP_BASE_URL.",
                code="sap_base_url_credentials_forbidden",
            )
        if not self.username or not self.password:
            raise SapReadError(
                "SAP_USERNAME and SAP_PASSWORD are required.",
                code="sap_credentials_missing",
            )
        if self.auth_type != "basic":
            raise SapReadError(
                "Unsupported SAP authentication type.", code="sap_auth_type_unsupported"
            )

    def _validate_plan_shape(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        forbidden_transport = {
            "url",
            "resource_path",
            "service_root_path",
            "metadata_path",
            "headers",
            "authorization",
            "sap_client",
        }
        if self._contains_any_key(plan, forbidden_transport):
            issues.append(
                {
                    "code": "raw_transport_fields_forbidden",
                    "message": "Plans cannot provide URLs, headers, or authorization values.",
                }
            )
        try:
            candidates = self._plan_steps(plan)
        except SapReadError as exc:
            return [{"code": exc.code, "message": str(exc)}]
        seen: set[str] = set()
        for index, step in enumerate(candidates, start=1):
            step_id = str(step.get("step_id") or f"step_{index}")
            if "bindings" in step:
                issues.append(
                    {
                        "code": "unsupported_binding_contract",
                        "step_id": step_id,
                        "message": "Use filter_from_previous; bindings is not an executable Provider contract.",
                    }
                )
            if step_id in seen:
                issues.append({"code": "duplicate_step_id", "step_id": step_id})
            seen.add(step_id)
            method = next((str(step.get(key)) for key in _METHOD_KEYS if key in step), "GET")
            if method.upper() != "GET":
                issues.append({"code": "write_operation_rejected", "step_id": step_id})
            service = str(step.get("service_name") or plan.get("service_name") or "")
            version_value = step.get("odata_version", plan.get("odata_version"))
            entity = str(step.get("entity_set") or "")
            if not _SERVICE.fullmatch(service):
                issues.append({"code": "invalid_service_name", "step_id": step_id})
            if version_value not in {"2.0", "4.0"}:
                version = ""
                issues.append(
                    {
                        "code": "odata_version_required" if version_value in {None, ""} else "odata_version_unsupported",
                        "step_id": step_id,
                        "message": "odata_version must explicitly be 2.0 or 4.0.",
                    }
                )
            else:
                version = str(version_value)
            if service and version:
                try:
                    self._resolve_binding(service, version)
                except SapReadError as exc:
                    issues.append({"code": exc.code, "step_id": step_id, "message": str(exc)})
            if not _IDENTIFIER.fullmatch(entity):
                issues.append({"code": "invalid_entity_set", "step_id": step_id})
            kind = str(step.get("plan_kind") or plan.get("plan_kind") or "direct")
            if kind in {"action", "action_import"}:
                issues.append({"code": "write_operation_rejected", "step_id": step_id})
            if kind in {"function", "function_import"}:
                parameters = step.get("function_parameters")
                if not isinstance(parameters, list) or not parameters:
                    issues.append({"code": "function_parameters_missing", "step_id": step_id})
                else:
                    names: set[str] = set()
                    for item in parameters:
                        name = str(item.get("name") or "") if isinstance(item, dict) else ""
                        if not _IDENTIFIER.fullmatch(name) or name in names or "value" not in item:
                            issues.append({"code": "invalid_function_parameter", "step_id": step_id})
                        names.add(name)
                forbidden = set(step).intersection(
                    {"filters", "filter_from_previous", "order_by", "top", "select_fields"}
                )
                if forbidden:
                    issues.append(
                        {
                            "code": "function_import_query_options_forbidden",
                            "step_id": step_id,
                            "fields": sorted(forbidden),
                        }
                    )
            top = step.get("top")
            if top is not None and (not isinstance(top, int) or isinstance(top, bool) or top <= 0):
                issues.append({"code": "invalid_top", "step_id": step_id})
            for order in step.get("order_by") or []:
                if not _IDENTIFIER.fullmatch(str(order)):
                    issues.append({"code": "invalid_order_by_expression", "step_id": step_id})
            for item in step.get("filters") or []:
                if not isinstance(item, dict) or str(item.get("operator") or "eq").lower() not in _OPERATORS:
                    issues.append({"code": "unsupported_filter_operator", "step_id": step_id})
        return issues

    def _plan_steps(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        kind = str(plan.get("plan_kind") or "direct")
        if kind in {"function", "function_import", "action", "action_import"}:
            return [dict(plan)]
        nested = plan.get("steps")
        if kind in {"lookup", "multi_step"}:
            if not isinstance(nested, list) or not nested:
                raise SapReadError("Multi-step plans require steps.", code="plan_steps_missing")
            return [dict(item) for item in nested if isinstance(item, dict)]
        return [dict(plan)]

    @staticmethod
    def _field_uses(step: dict[str, Any]) -> list[tuple[str, str]]:
        uses: list[tuple[str, str]] = []
        for field in step.get("select_fields") or []:
            uses.append((str(field), "select"))
        for field in step.get("response_summary_fields") or []:
            uses.append((str(field), "select"))
        for field in step.get("order_by") or []:
            uses.append((str(field), "order"))
        for item in step.get("filters") or []:
            if isinstance(item, dict):
                uses.append((str(item.get("field") or ""), "filter"))
        for item in step.get("filter_from_previous") or []:
            if isinstance(item, dict):
                uses.append((str(item.get("field") or ""), "filter"))
        output = step.get("output_contract")
        if isinstance(output, dict):
            for key in ("requested_fields", "display_fields", "support_fields"):
                for field in output.get(key) or []:
                    uses.append((str(field), "select"))
        return [(field, use) for field, use in uses if field]

    def _binding_filter_groups(
        self,
        bindings: list[dict[str, Any]],
        prior: dict[str, dict[str, Any]],
        odata_version: str,
    ) -> list[str] | None:
        if not bindings:
            return None
        source_ids = {str(item.get("source_step_id") or "") for item in bindings}
        if len(source_ids) != 1:
            raise SapReadError(
                "A binding step must use one coherent source row set.",
                code="cross_source_binding_not_supported",
            )
        source_id = next(iter(source_ids))
        rows = prior.get(source_id, {}).get("results") or []
        groups: list[str] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            expressions: list[str] = []
            for binding in bindings:
                value = row.get(str(binding.get("source_field") or ""))
                if value in {None, ""}:
                    expressions = []
                    break
                field = self._validate_identifier(str(binding.get("field") or ""), "binding field")
                expressions.append(
                    f"{field} eq {self._odata_literal(value, None, odata_version)}"
                )
            if expressions:
                expression = "(" + " and ".join(expressions) + ")"
                if expression not in seen:
                    groups.append(expression)
                    seen.add(expression)
        return groups

    def _literal_filters(
        self, filters: list[dict[str, Any]], odata_version: str
    ) -> list[str]:
        expressions: list[str] = []
        for item in filters:
            field = self._validate_identifier(str(item.get("field") or ""), "filter field")
            operator = str(item.get("operator") or "eq").lower()
            value = item.get("value")
            value_type = item.get("value_type")
            if operator == "contains":
                literal = self._odata_literal(value, value_type, odata_version)
                expressions.append(
                    f"substringof({literal},{field})"
                    if odata_version == "2.0"
                    else f"contains({field},{literal})"
                )
            elif operator == "in":
                values = value if isinstance(value, list) else [part.strip() for part in str(value).split(",")]
                expressions.append(
                    "(" + " or ".join(
                        f"{field} eq {self._odata_literal(child, value_type, odata_version)}"
                        for child in values
                    ) + ")"
                )
            else:
                expressions.append(
                    f"{field} {operator} {self._odata_literal(value, value_type, odata_version)}"
                )
        return expressions

    @staticmethod
    def _odata_literal(value: Any, value_type: Any, odata_version: str = "2.0") -> str:
        normalized = str(value_type or "").strip().lower()
        if value is None or normalized in {"null", "edm.null"}:
            return "null"
        if isinstance(value, bool) or normalized in {"boolean", "bool", "edm.boolean"}:
            return "true" if value is True or str(value).lower() in {"true", "1", "x"} else "false"
        if isinstance(value, (int, float)) or normalized in {
            "int", "integer", "number", "decimal", "edm.int16", "edm.int32", "edm.int64", "edm.decimal", "edm.double",
        }:
            numeric = str(value)
            if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", numeric):
                raise SapReadError("Invalid numeric OData literal.", code="odata_literal_invalid")
            suffix = "M" if odata_version == "2.0" and normalized in {"decimal", "edm.decimal"} else ""
            return f"{numeric}{suffix}"
        if normalized == "date_compact":
            compact = str(value).replace("-", "")
            if not re.fullmatch(r"\d{8}", compact):
                raise SapReadError("Invalid compact-date OData literal.", code="odata_literal_invalid")
            return f"'{compact}'"
        text = str(value).replace("'", "''")
        if normalized in {"date", "datetime", "date_start", "date_end", "edm.date", "edm.datetime"} or isinstance(value, (date, datetime)):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
                if odata_version == "4.0" and normalized in {"date", "edm.date"}:
                    return text
                text += "T23:59:59" if normalized == "date_end" else "T00:00:00"
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?", text):
                raise SapReadError("Invalid date or datetime OData literal.", code="odata_literal_invalid")
            return text if odata_version == "4.0" else f"datetime'{text}'"
        if normalized in {"datetimeoffset", "edm.datetimeoffset"}:
            if not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
                text,
            ):
                raise SapReadError("Invalid datetime-offset OData literal.", code="odata_literal_invalid")
            return text if odata_version == "4.0" else f"datetimeoffset'{text}'"
        if normalized in {"guid", "edm.guid"}:
            if not re.fullmatch(
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                text,
            ):
                raise SapReadError("Invalid GUID OData literal.", code="odata_literal_invalid")
            return text if odata_version == "4.0" else f"guid'{text}'"
        if normalized in {"time", "timeofday", "edm.timeofday"}:
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?", text):
                raise SapReadError("Invalid time-of-day OData literal.", code="odata_literal_invalid")
            if odata_version == "4.0":
                return text
            hours, minutes, seconds = text.split(":", 2)
            return f"time'PT{int(hours)}H{int(minutes)}M{seconds}S'"
        return f"'{text}'"

    def _safe_next_link(self, value: str, binding: ODataServiceBinding) -> str:
        parsed = urlsplit(value)
        base = urlsplit(self.base_url)
        if parsed.scheme or parsed.netloc:
            if parsed.scheme != base.scheme or parsed.netloc != base.netloc:
                raise SapReadError("SAP paging link changed origin.", code="sap_paging_origin_rejected")
        path = parsed.path or value.split("?", 1)[0]
        decoded_path = unquote(path)
        if "\\" in decoded_path or any(
            segment in {".", ".."} for segment in decoded_path.split("/")
        ):
            raise SapReadError(
                "SAP paging link contains an unsafe path.",
                code="sap_paging_path_rejected",
            )
        required = binding.service_root_path.rstrip("/") + "/"
        if not decoded_path.startswith(required):
            raise SapReadError("SAP paging link left the approved service.", code="sap_paging_path_rejected")
        return value

    @staticmethod
    def _rows_and_next(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
        data = payload.get("d") if isinstance(payload.get("d"), dict) else payload
        rows = data.get("results") if isinstance(data, dict) else None
        if rows is None and isinstance(data, dict):
            rows = data.get("value")
        if rows is None and isinstance(data, dict):
            rows = [data]
        safe_rows = [dict(item) for item in (rows or []) if isinstance(item, dict)]
        next_link = None
        if isinstance(data, dict):
            next_link = data.get("__next") or data.get("@odata.nextLink") or data.get("odata.nextLink")
        return safe_rows, str(next_link) if next_link else None

    @staticmethod
    def _response_json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise SapReadError("SAP returned non-JSON query content.", code="sap_read_invalid_json") from exc
        if not isinstance(payload, dict):
            raise SapReadError("SAP returned an invalid JSON root.", code="sap_read_invalid_json")
        return payload

    @staticmethod
    def _safe_sap_error(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            return {"message": response.text[:500]}
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            return {"message": "SAP request failed."}
        message = error.get("message")
        if isinstance(message, dict):
            message = message.get("value")
        return {"code": str(error.get("code") or ""), "message": str(message or "")[:1000]}

    @staticmethod
    def _parse_metadata(xml_text: str) -> tuple[str, dict[str, dict[str, Any]]]:
        root = ET.fromstring(xml_text)
        root_version = str(root.attrib.get("Version") or "").strip()
        if root_version.startswith("4"):
            odata_version = "4.0"
        elif root_version.startswith("1"):
            odata_version = "2.0"
        else:
            data_service_version = next(
                (
                    str(value)
                    for element in root.findall(".//{*}DataServices")
                    for key, value in element.attrib.items()
                    if key.rsplit("}", 1)[-1] in {"DataServiceVersion", "MaxDataServiceVersion"}
                ),
                "",
            )
            if data_service_version.startswith("4"):
                odata_version = "4.0"
            elif data_service_version:
                odata_version = "2.0"
            else:
                raise ValueError("metadata does not declare an OData protocol version")
        entity_types: dict[str, dict[str, Any]] = {}
        for entity_type in root.findall(".//{*}EntityType"):
            name = str(entity_type.attrib.get("Name") or "")
            if not name:
                continue
            keys = [
                str(item.attrib.get("Name") or "")
                for item in entity_type.findall("./{*}Key/{*}PropertyRef")
                if item.attrib.get("Name")
            ]
            fields: list[dict[str, Any]] = []
            for prop in entity_type.findall("./{*}Property"):
                field_name = str(prop.attrib.get("Name") or "")
                if not field_name:
                    continue
                fields.append(
                    {
                        "name": field_name,
                        "type": str(prop.attrib.get("Type") or "Edm.String"),
                        "nullable": str(prop.attrib.get("Nullable", "true")).lower() != "false",
                        "selectable": str(prop.attrib.get(f"{{{_SAP_NS}}}visible", "true")).lower() != "false",
                        "filterable": str(prop.attrib.get(f"{{{_SAP_NS}}}filterable", "true")).lower() != "false",
                        "sortable": str(prop.attrib.get(f"{{{_SAP_NS}}}sortable", "true")).lower() != "false",
                    }
                )
            entity_types[name] = {"keys": keys, "fields": fields, "kind": "entity_set"}
        for complex_type in root.findall(".//{*}ComplexType"):
            name = str(complex_type.attrib.get("Name") or "")
            if not name:
                continue
            fields = []
            for prop in complex_type.findall("./{*}Property"):
                field_name = str(prop.attrib.get("Name") or "")
                if field_name:
                    fields.append(
                        {
                            "name": field_name,
                            "type": str(prop.attrib.get("Type") or "Edm.String"),
                            "nullable": str(prop.attrib.get("Nullable", "true")).lower() != "false",
                            "selectable": True,
                            "filterable": False,
                            "sortable": False,
                        }
                    )
            entity_types[name] = {"keys": [], "fields": fields, "kind": "complex_type"}
        result: dict[str, dict[str, Any]] = {}
        for entity_set in root.findall(".//{*}EntitySet"):
            name = str(entity_set.attrib.get("Name") or "")
            type_name = str(entity_set.attrib.get("EntityType") or "").rsplit(".", 1)[-1]
            if name and type_name in entity_types:
                result[name] = entity_types[type_name]
        functions_by_name: dict[str, dict[str, Any]] = {}
        if odata_version == "4.0":
            for schema in root.findall(".//{*}Schema"):
                namespace = str(schema.attrib.get("Namespace") or "")
                for function in schema.findall("./{*}Function"):
                    name = str(function.attrib.get("Name") or "")
                    if not name:
                        continue
                    return_element = function.find("./{*}ReturnType")
                    return_name = (
                        str(return_element.attrib.get("Type") or "")
                        if return_element is not None
                        else ""
                    ).replace("Collection(", "").rstrip(")").rsplit(".", 1)[-1]
                    returned = entity_types.get(return_name, {"keys": [], "fields": []})
                    functions_by_name[name] = {
                        "keys": list(returned.get("keys") or []),
                        "fields": list(returned.get("fields") or []),
                        "kind": "function",
                        "invoke_name": f"{namespace}.{name}" if namespace else name,
                        "parameters": [
                            {
                                "name": str(item.attrib.get("Name") or ""),
                                "type": str(item.attrib.get("Type") or "Edm.String"),
                                "nullable": str(item.attrib.get("Nullable", "true")).lower() != "false",
                            }
                            for item in function.findall("./{*}Parameter")
                            if item.attrib.get("Name")
                        ],
                        "http_method": "GET",
                        "is_bound": str(function.attrib.get("IsBound", "false")).lower() == "true",
                    }
            for function_import in root.findall(".//{*}FunctionImport"):
                import_name = str(function_import.attrib.get("Name") or "")
                function_name = str(function_import.attrib.get("Function") or "").rsplit(".", 1)[-1]
                if import_name and function_name in functions_by_name:
                    result[import_name] = {
                        **functions_by_name[function_name],
                        "kind": "function_import",
                        "invoke_name": import_name,
                    }
            for name, descriptor in functions_by_name.items():
                result.setdefault(name, descriptor)
            for action in root.findall(".//{*}Action"):
                name = str(action.attrib.get("Name") or "")
                if name:
                    result[name] = {
                        "keys": [],
                        "fields": [],
                        "kind": "action",
                        "parameters": [],
                        "http_method": "POST",
                    }
            for action_import in root.findall(".//{*}ActionImport"):
                name = str(action_import.attrib.get("Name") or "")
                if name:
                    result[name] = {
                        "keys": [],
                        "fields": [],
                        "kind": "action_import",
                        "parameters": [],
                        "http_method": "POST",
                    }
        else:
            for function in root.findall(".//{*}FunctionImport"):
                name = str(function.attrib.get("Name") or "")
                return_name = str(function.attrib.get("ReturnType") or "").replace("Collection(", "").rstrip(")").rsplit(".", 1)[-1]
                if not name:
                    continue
                returned = entity_types.get(return_name, {"keys": [], "fields": []})
                parameters = [
                    {
                        "name": str(item.attrib.get("Name") or ""),
                        "type": str(item.attrib.get("Type") or "Edm.String"),
                        "nullable": str(item.attrib.get("Nullable", "false")).lower() != "false",
                    }
                    for item in function.findall("./{*}Parameter")
                    if item.attrib.get("Name")
                ]
                result[name] = {
                    "keys": [],
                    "fields": list(returned.get("fields") or []),
                    "kind": "function_import",
                    "parameters": parameters,
                    "http_method": str(function.attrib.get("{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}HttpMethod") or "GET").upper(),
                }
        if not result:
            raise ValueError("metadata contains no entity sets")
        return odata_version, result

    def _relationship_payload(self) -> dict[str, Any]:
        if not self.relationship_catalog_path or not self.relationship_catalog_path.is_file():
            return {}
        try:
            payload = json.loads(self.relationship_catalog_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _load_catalog_seed(path: Path | None) -> dict[str, Any]:
        if path is None or not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict) or payload.get("schema_version") != "2.0":
            return {}
        return payload

    def _merge_curated_catalog(self, path: Path | None) -> None:
        """Overlay reviewed search terms without regenerating the migrated seed.

        The migrated SAPClaw snapshot is immutable.  Reviewed platform-owned
        terminology can continue to evolve independently, while live metadata
        remains the execution authority for every entity and field.
        """
        if path is None or not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict) or payload.get("schema_version") != "2.0":
            return
        existing = {
            (
                str(item.get("service_name") or ""),
                str(item.get("odata_version") or ""),
                str(item.get("entity_set") or ""),
            ): item
            for item in self._catalog_seed.get("curated_search") or []
            if isinstance(item, dict)
        }
        for item in payload.get("entries") or []:
            if not isinstance(item, dict):
                continue
            key = (
                str(item.get("service_name") or ""),
                str(item.get("odata_version") or ""),
                str(item.get("entity_set") or ""),
            )
            if all(key):
                existing[key] = item
        self._catalog_seed["curated_search"] = list(existing.values())

    @staticmethod
    def _normalize_observed_version(value: str) -> str | None:
        normalized = value.strip().split(";", 1)[0]
        if normalized.startswith("4"):
            return "4.0"
        if normalized.startswith("2") or normalized.startswith("1") or normalized.startswith("3"):
            return "2.0"
        return None

    @staticmethod
    def _normalize_version(value: Any) -> str:
        try:
            return normalize_odata_version(value)
        except ODataCatalogError as exc:
            raise SapReadError(str(exc), code=exc.code) from exc

    def _resolve_binding(self, service_name: str, odata_version: str) -> ODataServiceBinding:
        try:
            return self._service_registry.resolve(service_name, odata_version)
        except ODataCatalogError as exc:
            raise SapReadError(str(exc), code=exc.code) from exc

    @staticmethod
    def _contains_any_key(value: Any, keys: set[str]) -> bool:
        if isinstance(value, dict):
            return bool(keys.intersection(value)) or any(
                EmbeddedODataProvider._contains_any_key(child, keys)
                for child in value.values()
            )
        if isinstance(value, list):
            return any(
                EmbeddedODataProvider._contains_any_key(child, keys) for child in value
            )
        return False

    @staticmethod
    def _empty_result(
        *,
        service_name: str | None = None,
        odata_version: str | None = None,
        entity_set: str | None = None,
    ) -> dict[str, Any]:
        result = {
            "ok": True,
            "results": [],
            "result_count": 0,
            "source_complete": True,
            "source_truncated": False,
            "requests": [],
        }
        if service_name:
            result["service_name"] = service_name
        if odata_version:
            result["odata_version"] = odata_version
        if entity_set:
            result["entity_set"] = entity_set
        return result

    @staticmethod
    def _validation_failure(code: str, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "status": "rejected",
            "validation_issues": [{"code": code, "message": message}],
        }

    @staticmethod
    def _validate_service(value: str) -> str:
        if not _SERVICE.fullmatch(value):
            raise SapReadError("Invalid SAP service name.", code="invalid_service_name")
        return value

    @staticmethod
    def _validate_identifier(value: str, label: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise SapReadError(f"Invalid {label}.", code="invalid_odata_identifier")
        return value


def _catalog_query_tokens(value: str) -> list[str]:
    """Tokenize mixed Chinese/business identifiers without external NLP dependencies."""
    tokens: list[str] = []
    for item in re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", value.casefold()):
        if re.fullmatch(r"[\u3400-\u9fff]+", item):
            if len(item) <= 6:
                tokens.append(item)
            for size in range(2, min(8, len(item)) + 1):
                tokens.extend(item[index : index + size] for index in range(len(item) - size + 1))
        elif item not in {"a", "an", "and", "no", "of", "or", "sap", "such", "the", "to"}:
            tokens.append(item)
    # Longest phrases score first and duplicates do not distort ranking.
    return sorted(dict.fromkeys(tokens), key=lambda item: (-len(item), item))[:200]


def _catalog_searchable_text(value: Any) -> str:
    """Index Catalog values without matching structural JSON property names."""

    parts: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, str):
            parts.append(item.casefold())

    walk(value)
    return "\n".join(parts)


def _looks_mojibake(value: str) -> bool:
    if "\ufffd" in value:
        return True
    markers = ("Ã", "Â", "â€", "æœ", "çš", "ï¿½")
    return any(marker in value for marker in markers)
