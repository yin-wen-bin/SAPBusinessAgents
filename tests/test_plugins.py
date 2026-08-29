from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
from typing import Any

import pytest
import httpx
from pydantic import ValidationError

from sap_business_agents_platform.plugins import (
    PluginError,
    PluginManager,
    PluginManifest,
    PluginStatus,
    official_plugin_manifests,
)
from sap_business_agents_platform.sap_read import EmbeddedODataProvider


class HealthyProvider:
    async def health(self) -> dict[str, Any]:
        return {"ok": True, "data": {"read_only": True}}


def _manager(tmp_path: Path) -> PluginManager:
    manager = PluginManager(
        tmp_path / "manifests",
        tmp_path / "state" / "registry.json",
        official_plugin_manifests(),
    )
    for item in manager.list():
        manager.bind_provider(item["plugin_id"], HealthyProvider())
    return manager


def _odata_registry(tmp_path: Path, *services: tuple[str, str]) -> Path:
    records = []
    for service_name, odata_version in services:
        root = (
            f"/sap/opu/odata/sap/{service_name}"
            if odata_version == "2.0"
            else f"/sap/opu/odata4/sap/{service_name}/0001"
        )
        records.append(
            {
                "service_name": service_name,
                "odata_version": odata_version,
                "service_root_path": root,
                "metadata_path": f"{root}/$metadata",
                "artifact_id": None,
                "artifact_version": None,
                "openapi_version": None,
                "catalog_source": "manual",
                "source_hash": f"sha256:{'0' * 64}",
                "status": "seed",
                "enabled": True,
            }
        )
    path = tmp_path / "odata-services.json"
    path.write_text(
        json.dumps({"schema_version": "2.0", "services": records}), encoding="utf-8"
    )
    return path


def test_manifest_rejects_sap_write_and_non_loopback_transport() -> None:
    base = {
        "schema_version": "1.0",
        "plugin_id": "unsafe-plugin",
        "version": "1.0.0",
        "name": {"zh": "不安全", "en": "Unsafe"},
        "publisher": "fixture",
        "capabilities": [
            {"capability": "sap_read.v2", "operations": ["execute_get"]}
        ],
        "transport": {"type": "http", "loopback_only": True},
        "permissions": {"sap_write": True},
    }
    with pytest.raises(ValidationError, match="SAP write permission is forbidden"):
        PluginManifest.model_validate(base)
    base["permissions"] = {}
    base["transport"]["loopback_only"] = False
    with pytest.raises(ValidationError, match="loopback-only"):
        PluginManifest.model_validate(base)


def test_registry_resolves_versioned_capabilities_and_persists_disable(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    asyncio.run(manager.start())
    binding = manager.resolve("sap_read.v2", "execute_plan")
    assert binding.manifest.plugin_id == "embedded-sap-odata"
    assert binding.trace("execute_plan")["plugin_version"] == "2.0.0"

    disabled = asyncio.run(manager.set_enabled("embedded-sap-odata", False))
    assert disabled["status"] == PluginStatus.disabled.value
    with pytest.raises(PluginError, match="No ready plugin"):
        manager.resolve("sap_read.v2", "execute_plan")

    restored = PluginManager(
        tmp_path / "manifests",
        tmp_path / "state" / "registry.json",
        official_plugin_manifests(),
    )
    assert restored.get("embedded-sap-odata")["enabled"] is False


def test_rescan_reports_invalid_manifest_without_loading_code(tmp_path: Path) -> None:
    root = tmp_path / "manifests"
    root.mkdir()
    (root / "invalid.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "plugin_id": "remote-marketplace",
                "version": "1.0.0",
                "name": {"zh": "远程", "en": "Remote"},
                "publisher": "fixture",
                "capabilities": [
                    {"capability": "sap_read.v2", "operations": ["execute_get"]}
                ],
                "transport": {"type": "http", "loopback_only": False},
                "permissions": {},
            }
        ),
        encoding="utf-8",
    )
    manager = PluginManager(
        root,
        tmp_path / "state.json",
        official_plugin_manifests(),
    )
    result = manager.rescan()
    assert len(result["errors"]) == 1
    assert {item["plugin_id"] for item in manager.list()} == {
        "business-agent-catalog",
        "codex-runtime",
        "embedded-sap-odata",
        "sapskillhub",
    }


_METADATA = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx"
  xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
  xmlns:sap="http://www.sap.com/Protocols/SAPData" Version="1.0">
  <edmx:DataServices m:DataServiceVersion="2.0">
    <Schema xmlns="http://schemas.microsoft.com/ado/2008/09/edm" Namespace="TEST">
      <EntityType Name="OrderType">
        <Key><PropertyRef Name="OrderID" /></Key>
        <Property Name="OrderID" Type="Edm.String" Nullable="false" sap:filterable="false" sap:sortable="true" />
        <Property Name="Amount" Type="Edm.Decimal" Nullable="true" />
      </EntityType>
      <ComplexType Name="AvailabilityType">
        <Property Name="Material" Type="Edm.String" Nullable="false" />
        <Property Name="AvailableQuantity" Type="Edm.Decimal" Nullable="false" />
      </ComplexType>
      <EntityContainer Name="Container" m:IsDefaultEntityContainer="true">
        <EntitySet Name="A_Order" EntityType="TEST.OrderType" />
        <FunctionImport Name="DetermineAvailabilityAt" ReturnType="TEST.AvailabilityType" m:HttpMethod="GET">
          <Parameter Name="Material" Type="Edm.String" Mode="In" />
          <Parameter Name="SupplyingPlant" Type="Edm.String" Mode="In" />
          <Parameter Name="ATPCheckingRule" Type="Edm.String" Mode="In" />
          <Parameter Name="RequestedUTCDateTime" Type="Edm.DateTimeOffset" Mode="In" />
        </FunctionImport>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""


_PARTITION_METADATA = _METADATA.replace(
    '<Property Name="Amount" Type="Edm.Decimal" Nullable="true" />',
    '<Property Name="Amount" Type="Edm.Decimal" Nullable="true" />\n'
    '        <Property Name="PostingDate" Type="Edm.DateTime" Nullable="false" />',
)


def test_embedded_provider_validates_live_schema_and_executes_get_only(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        if request.url.path.endswith("/$metadata"):
            return httpx.Response(200, text=_METADATA, headers={"content-type": "application/xml"})
        assert request.url.path.endswith("/A_Order")
        assert request.url.params["$filter"] == "(OrderID eq '42')"
        return httpx.Response(200, json={"d": {"results": [{"OrderID": "42", "Amount": "12.00"}]}})

    provider = EmbeddedODataProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
        client="100",
        page_size=100,
        transport=httpx.MockTransport(handler),
        service_registry_path=_odata_registry(tmp_path, ("API_TEST_SRV", "2.0")),
    )
    plan = {
        "service_name": "API_TEST_SRV",
        "odata_version": "2.0",
        "entity_set": "A_Order",
        "http_method": "GET",
        "select_fields": ["OrderID", "Amount"],
        "filters": [
            {"field": "OrderID", "operator": "eq", "value": "42", "value_type": "string"}
        ],
    }
    validation = asyncio.run(provider.validate_plan(plan))
    assert validation["ok"] is True
    schema = asyncio.run(
        provider.schema("API_TEST_SRV", ["A_Order"], odata_version="2.0")
    )
    order_id = next(
        field for field in schema["data"]["fields"] if field["field_name"] == "OrderID"
    )
    assert order_id["filterable"] is True
    assert order_id["metadata_filterable"] is False
    result = asyncio.run(provider.execute_plan(plan))
    assert result["ok"] is True
    assert result["provider_id"] == "embedded-odata"
    assert result["source_complete"] is True
    assert result["step_results"]["step_1"]["results"][0]["OrderID"] == "42"
    assert all("fixture-password" not in str(request.url) for request in requests)


def test_embedded_provider_rejects_write_before_network_access() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    provider = EmbeddedODataProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
        transport=httpx.MockTransport(handler),
    )
    validation = asyncio.run(
        provider.validate_plan(
            {
                "service_name": "API_TEST_SRV",
                "entity_set": "A_Order",
                "http_method": "POST",
            }
        )
    )
    assert validation["ok"] is False
    assert validation["validation_issues"][0]["code"] == "write_operation_rejected"
    assert called is False


def test_embedded_provider_executes_live_schema_validated_get_function_import(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        if request.url.path.endswith("/$metadata"):
            return httpx.Response(200, text=_METADATA)
        assert request.url.path.endswith("/DetermineAvailabilityAt")
        assert request.url.params["Material"] == "'TG0011'"
        assert request.url.params["SupplyingPlant"] == "'1710'"
        assert request.url.params["ATPCheckingRule"] == "'A'"
        assert request.url.params["RequestedUTCDateTime"] == "datetimeoffset'2026-08-17T00:00:00Z'"
        assert not any(key.startswith("$") for key in request.url.params)
        return httpx.Response(
            200,
            json={"d": {"Material": "TG0011", "AvailableQuantity": "12.000"}},
        )

    provider = EmbeddedODataProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
        transport=httpx.MockTransport(handler),
        service_registry_path=_odata_registry(
            tmp_path, ("API_PRODUCT_AVAILY_INFO_BASIC", "2.0")
        ),
    )
    plan = {
        "service_name": "API_PRODUCT_AVAILY_INFO_BASIC",
        "odata_version": "2.0",
        "entity_set": "DetermineAvailabilityAt",
        "http_method": "GET",
        "plan_kind": "function_import",
        "function_parameters": [
            {"name": "Material", "value": "TG0011", "value_type": "string"},
            {"name": "SupplyingPlant", "value": "1710", "value_type": "string"},
            {"name": "ATPCheckingRule", "value": "A", "value_type": "string"},
            {
                "name": "RequestedUTCDateTime",
                "value": "2026-08-17T00:00:00Z",
                "value_type": "datetimeoffset",
            },
        ],
    }
    validation = asyncio.run(provider.validate_plan(plan))
    assert validation["ok"] is True
    result = asyncio.run(provider.execute_plan(plan))
    assert result["source_complete"] is True
    assert result["data"]["results"][0]["AvailableQuantity"] == "12.000"
    assert all("fixture-password" not in str(request.url) for request in requests)


def test_embedded_provider_uses_metadata_keys_for_complete_manual_paging(tmp_path: Path) -> None:
    entity_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/$metadata"):
            return httpx.Response(200, text=_METADATA)
        entity_requests.append(request)
        skip = int(request.url.params.get("$skip", "0"))
        rows = (
            [{"OrderID": "41", "Amount": "1"}, {"OrderID": "42", "Amount": "2"}]
            if skip == 0
            else [{"OrderID": "43", "Amount": "3"}]
        )
        return httpx.Response(200, json={"d": {"results": rows}})

    provider = EmbeddedODataProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
        page_size=2,
        max_results=10,
        transport=httpx.MockTransport(handler),
        service_registry_path=_odata_registry(tmp_path, ("API_TEST_SRV", "2.0")),
    )
    result = asyncio.run(
        provider.execute_plan(
            {
                "service_name": "API_TEST_SRV",
                "odata_version": "2.0",
                "entity_set": "A_Order",
                "http_method": "GET",
                "select_fields": ["OrderID", "Amount"],
            }
        )
    )
    assert result["source_complete"] is True
    assert [row["OrderID"] for row in result["data"]["results"]] == ["41", "42", "43"]
    assert entity_requests[0].url.params["$orderby"] == "OrderID"
    assert entity_requests[1].url.params["$skip"] == "2"


def test_embedded_provider_adaptively_partitions_a_full_date_range(tmp_path: Path) -> None:
    entity_requests: list[httpx.Request] = []
    rows_by_date = {
        "2026-01-01": {"OrderID": "1", "Amount": "1", "PostingDate": "/Date(1767225600000)/"},
        "2026-01-02": {"OrderID": "2", "Amount": "2", "PostingDate": "/Date(1767312000000)/"},
        "2026-01-03": {"OrderID": "3", "Amount": "3", "PostingDate": "/Date(1767398400000)/"},
        "2026-01-04": {"OrderID": "4", "Amount": "4", "PostingDate": "/Date(1767484800000)/"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/$metadata"):
            return httpx.Response(200, text=_PARTITION_METADATA)
        entity_requests.append(request)
        expression = request.url.params["$filter"]
        dates = re.findall(r"datetime'(\d{4}-\d{2}-\d{2})T", expression)
        assert len(dates) == 2
        start, end = dates
        rows = [row for day, row in rows_by_date.items() if start <= day <= end]
        return httpx.Response(200, json={"d": {"results": rows[:2]}})

    provider = EmbeddedODataProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
        page_size=2,
        max_results=2,
        transport=httpx.MockTransport(handler),
        service_registry_path=_odata_registry(tmp_path, ("API_TEST_SRV", "2.0")),
    )
    plan = {
        "service_name": "API_TEST_SRV",
        "odata_version": "2.0",
        "entity_set": "A_Order",
        "http_method": "GET",
        "select_fields": ["OrderID", "Amount", "PostingDate"],
        "order_by": ["OrderID"],
        "partition": {
            "field": "PostingDate",
            "strategy": "adaptive_date",
            "from": "2026-01-01",
            "to": "2026-01-04",
            "maxPartitions": 8,
            "maxTotalResults": 10,
        },
    }

    validation = asyncio.run(provider.validate_plan(plan))
    assert validation["ok"] is True
    result = asyncio.run(provider.execute_plan(plan))

    assert result["source_complete"] is True
    assert [row["OrderID"] for row in result["data"]["results"]] == ["1", "2", "3", "4"]
    assert result["step_results"]["step_1"]["partition_count"] == 4
    assert len(entity_requests) > 1
    assert all(request.method == "GET" for request in entity_requests)


def test_embedded_provider_bounds_parallel_binding_chunks(tmp_path: Path) -> None:
    class ConcurrentProvider(EmbeddedODataProvider):
        active_chunks = 0
        max_active_chunks = 0

        async def _fetch_all(
            self,
            service,
            version,
            service_binding,
            entity,
            step,
            filter_expression,
            *,
            remaining,
        ):
            del service_binding, step, remaining
            if not filter_expression:
                return {
                    "results": [
                        {"OrderID": str(index), "Amount": str(index)}
                        for index in range(80)
                    ],
                    "requests": [],
                    "source_complete": True,
                    "source_truncated": False,
                    "validation_issues": [],
                }
            self.active_chunks += 1
            self.max_active_chunks = max(self.max_active_chunks, self.active_chunks)
            try:
                await asyncio.sleep(0.01)
            finally:
                self.active_chunks -= 1
            return {
                "results": [],
                "requests": [],
                "source_complete": True,
                "source_truncated": False,
                "validation_issues": [],
            }

    provider = ConcurrentProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=_METADATA)
            if request.url.path.endswith("/$metadata")
            else httpx.Response(500)
        ),
        service_registry_path=_odata_registry(tmp_path, ("API_TEST_SRV", "2.0")),
    )
    plan = {
        "service_name": "API_TEST_SRV",
        "odata_version": "2.0",
        "entity_set": "A_Order",
        "http_method": "GET",
        "plan_kind": "multi_step",
        "steps": [
            {
                "step_id": "source",
                "service_name": "API_TEST_SRV",
                "odata_version": "2.0",
                "entity_set": "A_Order",
                "http_method": "GET",
                "select_fields": ["OrderID", "Amount"],
            },
            {
                "step_id": "target",
                "service_name": "API_TEST_SRV",
                "odata_version": "2.0",
                "entity_set": "A_Order",
                "http_method": "GET",
                "select_fields": ["OrderID", "Amount"],
                "max_concurrent_chunks": 4,
                "filter_from_previous": [
                    {
                        "field": "OrderID",
                        "source_step_id": "source",
                        "source_field": "OrderID",
                    }
                ],
            },
        ],
    }

    result = asyncio.run(provider.execute_plan(plan))

    assert result["source_complete"] is True
    assert provider.max_active_chunks == 4
