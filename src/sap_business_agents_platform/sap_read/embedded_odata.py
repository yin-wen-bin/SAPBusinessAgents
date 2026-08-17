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
from urllib.parse import urlsplit

import httpx

from .base import SapReadError


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SERVICE = re.compile(r"^[A-Za-z0-9_]+(?:;v=[0-9]+)?$")
_OPERATORS = {"eq", "ne", "gt", "ge", "lt", "le", "contains", "in"}
_METHOD_KEYS = {"http_method", "httpMethod", "method"}
_SAP_NS = "http://www.sap.com/Protocols/SAPData"


class EmbeddedODataProvider:
    """Minimal in-process, GET-only SAP OData v2 provider.

    Planning and business-relationship validation remain in SAPBusinessAgents. This
    provider owns credentials, live metadata checks, request construction, paging,
    and evidence-completeness reporting.
    """

    provider_id = "embedded-odata"
    provider_version = "1.0.0"

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
        self.transport = transport
        self._cases: dict[str, dict[str, Any]] = {}
        self._metadata_cache: dict[str, dict[str, dict[str, Any]]] = {}

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
        return {
            "ok": not issues,
            "data": {
                "provider_id": self.provider_id,
                "provider_version": self.provider_version,
                "configured": not issues,
                "read_only": True,
                "sap_base_url_configured": bool(self.base_url),
                "sap_credentials_configured": bool(self.username and self.password),
                "live_probe_performed": False,
            },
            "validation_issues": issues,
        }

    async def catalog(
        self, query: str = "", skip: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        entries: dict[tuple[str, str], dict[str, Any]] = {}
        payload = self._relationship_payload()
        for item in payload.get("field_semantics") or []:
            if not isinstance(item, dict):
                continue
            service = str(item.get("service_name") or "")
            entity = str(item.get("entity_set") or "")
            field = str(item.get("field") or "")
            if not service or not entity or not field:
                continue
            entry = entries.setdefault(
                (service, entity),
                {
                    "service_name": service,
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
            tokens = [token for token in re.split(r"\s+", needle) if token]
            matched = [
                item
                for item in items
                if any(
                    token in json.dumps(item, ensure_ascii=False).casefold()
                    for token in tokens
                )
            ]
            if matched:
                items = matched
        page = items[max(0, skip) : max(0, skip) + max(1, limit)]
        return {
            "ok": True,
            "data": {
                "items": page,
                "total_count": len(items),
                "provider_id": self.provider_id,
                "catalog_scope": "approved_relationship_entities",
            },
        }

    async def guidance(self, query: str) -> dict[str, Any]:
        return {
            "ok": True,
            "data": {
                "query": query,
                "provider_id": self.provider_id,
                "evidence_policy": "live_schema_required_get_only",
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
        include_fields: bool = True,
        max_fields: int = 5000,
    ) -> dict[str, Any]:
        del query
        self._require_configured()
        service = self._validate_service(service_name)
        requested = [entity_sets] if isinstance(entity_sets, str) else list(entity_sets)
        requested = list(dict.fromkeys(str(item) for item in requested))
        if not requested:
            raise SapReadError("At least one entity set is required.", code="sap_schema_entity_missing")
        for entity in requested:
            self._validate_identifier(entity, "entity_set")

        started = time.perf_counter()
        response = await self._request(
            f"/sap/opu/odata/sap/{service}/$metadata",
            params={},
            accept="application/xml",
        )
        try:
            parsed = self._parse_metadata(response.text)
        except (ET.ParseError, ValueError) as exc:
            raise SapReadError(
                "SAP returned invalid OData metadata.",
                code="sap_metadata_invalid",
                detail={"service_name": service, "message": str(exc)},
            ) from exc
        self._metadata_cache[service] = parsed

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
                        "entity_set": entity,
                    }
                )
                continue
            entities.append(
                {
                    "service_name": service,
                    "entity_set": entity,
                    "key_fields": descriptor["keys"],
                    "supports_filter": True,
                    "supports_orderby": True,
                    "supports_top": True,
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
                "service": {"service_name": service},
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
        refs_by_service: dict[str, list[str]] = defaultdict(list)
        for step in candidates:
            service = str(step.get("service_name") or plan.get("service_name") or "")
            entity = str(step.get("entity_set") or "")
            if entity not in refs_by_service[service]:
                refs_by_service[service].append(entity)

        schema_fields: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        for service, entities in refs_by_service.items():
            response = await self.schema(service, entities)
            issues.extend(response.get("validation_issues") or [])
            data = response.get("data") or {}
            for field in data.get("fields") or []:
                key = (str(field.get("service_name") or service), str(field.get("entity_set") or ""))
                schema_fields.setdefault(key, {})[str(field.get("field_name") or "")] = field

        step_lookup = {
            str(step.get("step_id") or ""): step
            for step in candidates
            if str(step.get("step_id") or "")
        }
        for step in candidates:
            service = str(step.get("service_name") or plan.get("service_name") or "")
            entity = str(step.get("entity_set") or "")
            available = schema_fields.get((service, entity), {})
            for field_name, use in self._field_uses(step):
                descriptor = available.get(field_name)
                if descriptor is None:
                    issues.append(
                        {
                            "code": "schema_drift_field_unavailable",
                            "service_name": service,
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
                source_entity = str(source.get("entity_set") or "")
                source_field = str(binding.get("source_field") or "")
                if source_field not in schema_fields.get((source_service, source_entity), {}):
                    issues.append(
                        {
                            "code": "schema_drift_binding_source_field_unavailable",
                            "service_name": source_service,
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
            "validation_issues": [],
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
        entity = self._validate_identifier(str(step.get("entity_set") or ""), "entity_set")
        literal_filter = self._literal_filters(step.get("filters") or [])
        binding_groups = self._binding_filter_groups(step.get("filter_from_previous") or [], prior)
        if binding_groups == [] and step.get("filter_from_previous"):
            return self._empty_result()
        chunks: list[list[str]] = []
        if binding_groups:
            for offset in range(0, len(binding_groups), 20):
                chunks.append(binding_groups[offset : offset + 20])
        else:
            chunks = [[]]

        all_rows: list[dict[str, Any]] = []
        requests: list[dict[str, Any]] = []
        complete = True
        truncated = False
        for binding_chunk in chunks:
            pieces = list(literal_filter)
            if binding_chunk:
                pieces.append("(" + " or ".join(binding_chunk) + ")")
            result = await self._fetch_all(
                service,
                entity,
                step,
                " and ".join(f"({item})" for item in pieces if item),
                remaining=max(0, self.max_results - len(all_rows)),
            )
            all_rows.extend(result["results"])
            requests.extend(result["requests"])
            complete = complete and result["source_complete"]
            truncated = truncated or result["source_truncated"]
            if len(all_rows) >= self.max_results:
                truncated = True
                complete = False
                all_rows = all_rows[: self.max_results]
                break
        return {
            "ok": True,
            "service_name": service,
            "entity_set": entity,
            "results": all_rows,
            "result_count": len(all_rows),
            "source_complete": complete,
            "source_truncated": truncated,
            "requests": requests,
        }

    async def _fetch_all(
        self,
        service: str,
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
        if select_fields:
            params["$select"] = ",".join(select_fields)
        order_by = [str(item) for item in step.get("order_by") or []]
        if not order_by and explicit_top is None:
            entity_metadata = self._metadata_cache.get(service, {}).get(entity, {})
            sortable_fields = {
                str(field.get("name") or "")
                for field in entity_metadata.get("fields", [])
                if field.get("sortable") is True
            }
            order_by = [
                key for key in entity_metadata.get("keys", []) if key in sortable_fields
            ]
        if order_by:
            params["$orderby"] = ",".join(order_by)
        if filter_expression:
            params["$filter"] = filter_expression
        params["$top"] = str(min(self.page_size, requested_limit))

        path = f"/sap/opu/odata/sap/{service}/{entity}"
        rows: list[dict[str, Any]] = []
        requests: list[dict[str, Any]] = []
        next_url: str | None = path
        next_params: dict[str, str] | None = params
        manual_skip = 0
        source_complete = True
        source_truncated = False
        while next_url and len(rows) < requested_limit:
            response = await self._request(next_url, params=next_params or {})
            payload = self._response_json(response)
            page_rows, discovered_next = self._rows_and_next(payload)
            allowed = requested_limit - len(rows)
            rows.extend(page_rows[:allowed])
            requests.append(
                {
                    "http_method": "GET",
                    "service_name": service,
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
                next_url = self._safe_next_link(discovered_next, service)
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
        return {
            "results": rows,
            "requests": requests,
            "source_complete": source_complete,
            "source_truncated": source_truncated,
        }

    async def _request(
        self,
        path_or_url: str,
        *,
        params: dict[str, str],
        accept: str = "application/json",
    ) -> httpx.Response:
        self._require_configured()
        if self.client and "sap-client" not in params:
            params = {**params, "sap-client": self.client}
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
                response = await client.get(path_or_url, params=params)
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
        if any(key in plan for key in {"url", "resource_path", "headers", "authorization"}):
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
            if step_id in seen:
                issues.append({"code": "duplicate_step_id", "step_id": step_id})
            seen.add(step_id)
            method = next((str(step.get(key)) for key in _METHOD_KEYS if key in step), "GET")
            if method.upper() != "GET":
                issues.append({"code": "write_operation_rejected", "step_id": step_id})
            service = str(step.get("service_name") or plan.get("service_name") or "")
            entity = str(step.get("entity_set") or "")
            if not _SERVICE.fullmatch(service):
                issues.append({"code": "invalid_service_name", "step_id": step_id})
            if not _IDENTIFIER.fullmatch(entity):
                issues.append({"code": "invalid_entity_set", "step_id": step_id})
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
        if kind == "function_import":
            raise SapReadError(
                "Function imports are not enabled for the embedded prototype.",
                code="function_import_not_supported",
            )
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
                expressions.append(f"{field} eq {self._odata_literal(value, None)}")
            if expressions:
                expression = "(" + " and ".join(expressions) + ")"
                if expression not in seen:
                    groups.append(expression)
                    seen.add(expression)
        return groups

    def _literal_filters(self, filters: list[dict[str, Any]]) -> list[str]:
        expressions: list[str] = []
        for item in filters:
            field = self._validate_identifier(str(item.get("field") or ""), "filter field")
            operator = str(item.get("operator") or "eq").lower()
            value = item.get("value")
            value_type = item.get("value_type")
            if operator == "contains":
                expressions.append(f"substringof({self._odata_literal(value, value_type)},{field})")
            elif operator == "in":
                values = value if isinstance(value, list) else [part.strip() for part in str(value).split(",")]
                expressions.append(
                    "(" + " or ".join(
                        f"{field} eq {self._odata_literal(child, value_type)}" for child in values
                    ) + ")"
                )
            else:
                expressions.append(f"{field} {operator} {self._odata_literal(value, value_type)}")
        return expressions

    @staticmethod
    def _odata_literal(value: Any, value_type: Any) -> str:
        normalized = str(value_type or "").strip().lower()
        if value is None or normalized in {"null", "edm.null"}:
            return "null"
        if isinstance(value, bool) or normalized in {"boolean", "bool", "edm.boolean"}:
            return "true" if value is True or str(value).lower() in {"true", "1", "x"} else "false"
        if isinstance(value, (int, float)) or normalized in {
            "int", "integer", "number", "decimal", "edm.int16", "edm.int32", "edm.int64", "edm.decimal", "edm.double",
        }:
            return str(value)
        text = str(value).replace("'", "''")
        if normalized in {"date", "datetime", "date_start", "date_end", "edm.datetime"} or isinstance(value, (date, datetime)):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
                text += "T23:59:59" if normalized == "date_end" else "T00:00:00"
            return f"datetime'{text}'"
        if normalized in {"datetimeoffset", "edm.datetimeoffset"}:
            return f"datetimeoffset'{text}'"
        if normalized in {"guid", "edm.guid"}:
            return f"guid'{text}'"
        return f"'{text}'"

    def _safe_next_link(self, value: str, service: str) -> str:
        parsed = urlsplit(value)
        base = urlsplit(self.base_url)
        if parsed.scheme or parsed.netloc:
            if parsed.scheme != base.scheme or parsed.netloc != base.netloc:
                raise SapReadError("SAP paging link changed origin.", code="sap_paging_origin_rejected")
        path = parsed.path or value.split("?", 1)[0]
        required = f"/sap/opu/odata/sap/{service}/"
        if not path.startswith(required):
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
    def _parse_metadata(xml_text: str) -> dict[str, dict[str, Any]]:
        root = ET.fromstring(xml_text)
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
            entity_types[name] = {"keys": keys, "fields": fields}
        result: dict[str, dict[str, Any]] = {}
        for entity_set in root.findall(".//{*}EntitySet"):
            name = str(entity_set.attrib.get("Name") or "")
            type_name = str(entity_set.attrib.get("EntityType") or "").rsplit(".", 1)[-1]
            if name and type_name in entity_types:
                result[name] = entity_types[type_name]
        if not result:
            raise ValueError("metadata contains no entity sets")
        return result

    def _relationship_payload(self) -> dict[str, Any]:
        if not self.relationship_catalog_path or not self.relationship_catalog_path.is_file():
            return {}
        try:
            payload = json.loads(self.relationship_catalog_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _empty_result() -> dict[str, Any]:
        return {
            "ok": True,
            "results": [],
            "result_count": 0,
            "source_complete": True,
            "source_truncated": False,
            "requests": [],
        }

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
