from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from sap_business_agents_platform.sap_read import EmbeddedODataProvider


V4_METADATA = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">
  <edmx:DataServices>
    <Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="TEST">
      <EntityType Name="ProductType">
        <Key><PropertyRef Name="ID" /></Key>
        <Property Name="ID" Type="Edm.Guid" Nullable="false" />
        <Property Name="Name" Type="Edm.String" />
        <Property Name="CreatedOn" Type="Edm.Date" />
      </EntityType>
      <Function Name="SearchProduct" IsBound="false">
        <Parameter Name="Term" Type="Edm.String" Nullable="false" />
        <ReturnType Type="Collection(TEST.ProductType)" />
      </Function>
      <Action Name="Reprice"><Parameter Name="ID" Type="Edm.Guid" /></Action>
      <EntityContainer Name="Container">
        <EntitySet Name="Products" EntityType="TEST.ProductType" />
        <FunctionImport Name="SearchProducts" Function="TEST.SearchProduct" />
        <ActionImport Name="RepriceProduct" Action="TEST.Reprice" />
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""

V2_METADATA = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx"
  xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" Version="1.0">
  <edmx:DataServices m:DataServiceVersion="2.0">
    <Schema xmlns="http://schemas.microsoft.com/ado/2008/09/edm" Namespace="TEST">
      <EntityType Name="OrderType">
        <Key><PropertyRef Name="ID" /></Key>
        <Property Name="ID" Type="Edm.String" Nullable="false" />
        <Property Name="PostingDate" Type="Edm.DateTime" />
      </EntityType>
      <EntityContainer Name="Container"><EntitySet Name="Orders" EntityType="TEST.OrderType" /></EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""


def registry(tmp_path: Path, *, version: str = "4.0") -> Path:
    root = (
        "/sap/opu/odata4/sap/API_V4_TEST/0001"
        if version == "4.0"
        else "/sap/opu/odata/sap/API_V4_TEST"
    )
    path = tmp_path / "odata-services.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "services": [
                    {
                        "service_name": "API_V4_TEST",
                        "odata_version": version,
                        "service_root_path": root,
                        "metadata_path": f"{root}/$metadata",
                        "artifact_id": "FIXTURE",
                        "artifact_version": "0001",
                        "openapi_version": "3.0.1",
                        "catalog_source": "manual",
                        "source_hash": f"sha256:{'a' * 64}",
                        "status": "seed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_v4_edmx_contains_literals_next_link_and_versioned_audit(tmp_path: Path) -> None:
    entity_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if request.url.path.endswith("/$metadata"):
            return httpx.Response(
                200,
                text=V4_METADATA,
                headers={"OData-Version": "4.0"},
            )
        entity_requests.append(request)
        if request.url.params.get("$skiptoken") is None:
            assert request.url.path.endswith("/Products")
            assert request.url.params["$filter"] == (
                "(contains(Name,'bolt')) and (CreatedOn eq 2026-08-19)"
            )
            assert request.url.params["$orderby"] == "ID"
            return httpx.Response(
                200,
                json={
                    "value": [{"ID": "00000000-0000-0000-0000-000000000001", "Name": "bolt"}],
                    "@odata.nextLink": (
                        "https://sap.example.test/sap/opu/odata4/sap/API_V4_TEST/0001/"
                        "Products?$skiptoken=NEXT"
                    ),
                },
            )
        return httpx.Response(
            200,
            json={"value": [{"ID": "00000000-0000-0000-0000-000000000002", "Name": "bolt 2"}]},
        )

    provider = EmbeddedODataProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
        client="100",
        service_registry_path=registry(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    plan = {
        "service_name": "API_V4_TEST",
        "odata_version": "4.0",
        "entity_set": "Products",
        "http_method": "GET",
        "select_fields": ["ID", "Name", "CreatedOn"],
        "filters": [
            {"field": "Name", "operator": "contains", "value": "bolt"},
            {"field": "CreatedOn", "operator": "eq", "value": "2026-08-19"},
        ],
    }
    result = asyncio.run(provider.execute_plan(plan))
    assert result["status"] == "completed"
    assert result["source_complete"] is True
    assert [row["Name"] for row in result["data"]["results"]] == ["bolt", "bolt 2"]
    audit = result["step_results"]["step_1"]["requests"]
    assert audit and all(item["odata_version"] == "4.0" for item in audit)
    assert len(entity_requests) == 2
    assert entity_requests[1].url.params["$skiptoken"] == "NEXT"
    assert entity_requests[1].url.params["sap-client"] == "100"


def test_v2_filter_literal_type_is_inferred_from_live_metadata(tmp_path: Path) -> None:
    entity_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if request.url.path.endswith("/$metadata"):
            return httpx.Response(200, text=V2_METADATA)
        entity_requests.append(request)
        assert request.url.params["$filter"] == (
            "(PostingDate le datetime'2018-10-01T23:59:59')"
        )
        return httpx.Response(200, json={"d": {"results": []}})

    provider = EmbeddedODataProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
        service_registry_path=registry(tmp_path, version="2.0"),
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(
        provider.execute_plan(
            {
                "service_name": "API_V4_TEST",
                "odata_version": "2.0",
                "entity_set": "Orders",
                "http_method": "GET",
                "select_fields": ["ID", "PostingDate"],
                "filters": [
                    {
                        "field": "PostingDate",
                        "operator": "le",
                        "value": "2018-10-01T23:59:59",
                    }
                ],
            }
        )
    )
    assert result["status"] == "completed"
    assert entity_requests


def test_v4_unbound_function_import_uses_get_path_literals(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/$metadata"):
            return httpx.Response(200, text=V4_METADATA)
        calls.append(request)
        assert request.method == "GET"
        if request.url.params.get("$skiptoken") is None:
            assert request.url.path.endswith("/SearchProducts(Term='bolt')")
            return httpx.Response(
                200,
                json={
                    "value": [{"Name": "bolt"}],
                    "@odata.nextLink": (
                        "https://sap.example.test/sap/opu/odata4/sap/API_V4_TEST/0001/"
                        "SearchProducts?$skiptoken=F2"
                    ),
                },
            )
        return httpx.Response(200, json={"value": [{"Name": "bolt 2"}]})

    provider = EmbeddedODataProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
        service_registry_path=registry(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    plan = {
        "service_name": "API_V4_TEST",
        "odata_version": "4.0",
        "entity_set": "SearchProducts",
        "http_method": "GET",
        "plan_kind": "function_import",
        "function_parameters": [{"name": "Term", "value": "bolt", "value_type": "string"}],
    }
    result = asyncio.run(provider.execute_plan(plan))
    assert result["data"]["results"] == [{"Name": "bolt"}, {"Name": "bolt 2"}]
    assert result["step_results"]["step_1"]["entity_kind"] == "function_import"
    assert len(calls) == 2


def test_v4_direct_unbound_function_uses_namespace_qualified_get_path(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/$metadata"):
            return httpx.Response(200, text=V4_METADATA)
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path.endswith("/TEST.SearchProduct(Term='bolt')")
        return httpx.Response(200, json={"value": [{"Name": "bolt"}]})

    provider = EmbeddedODataProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
        service_registry_path=registry(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(
        provider.execute_plan(
            {
                "service_name": "API_V4_TEST",
                "odata_version": "4.0",
                "entity_set": "SearchProduct",
                "http_method": "GET",
                "plan_kind": "function",
                "function_parameters": [
                    {"name": "Term", "value": "bolt", "value_type": "string"}
                ],
            }
        )
    )
    assert result["data"]["results"] == [{"Name": "bolt"}]
    assert result["step_results"]["step_1"]["entity_kind"] == "function"
    assert calls


def test_version_is_required_and_v4_action_is_rejected_before_network(tmp_path: Path) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    provider = EmbeddedODataProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
        service_registry_path=registry(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    missing = asyncio.run(
        provider.validate_plan(
            {"service_name": "API_V4_TEST", "entity_set": "Products", "http_method": "GET"}
        )
    )
    assert missing["validation_issues"][0]["code"] == "odata_version_required"
    action = asyncio.run(
        provider.validate_plan(
            {
                "service_name": "API_V4_TEST",
                "odata_version": "4.0",
                "entity_set": "RepriceProduct",
                "http_method": "GET",
                "plan_kind": "action_import",
            }
        )
    )
    assert any(item["code"] == "write_operation_rejected" for item in action["validation_issues"])
    assert calls == 0


def test_live_metadata_protocol_mismatch_stops_before_data_query(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=V4_METADATA, headers={"OData-Version": "4.0"})

    provider = EmbeddedODataProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
        service_registry_path=registry(tmp_path, version="2.0"),
        transport=httpx.MockTransport(handler),
    )
    validation = asyncio.run(
        provider.validate_plan(
            {
                "service_name": "API_V4_TEST",
                "odata_version": "2.0",
                "entity_set": "Products",
                "http_method": "GET",
            }
        )
    )
    assert validation["ok"] is False
    assert validation["validation_issues"][0]["code"] == "odata_version_mismatch"
    assert len(requests) == 1 and requests[0].url.path.endswith("/$metadata")


def test_v4_next_link_cannot_leave_registered_service_root(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/$metadata"):
            return httpx.Response(200, text=V4_METADATA)
        return httpx.Response(
            200,
            json={
                "value": [{"ID": "00000000-0000-0000-0000-000000000001"}],
                "@odata.nextLink": "https://sap.example.test/sap/opu/odata4/sap/OTHER/0001/Products?$skiptoken=x",
            },
        )

    provider = EmbeddedODataProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
        service_registry_path=registry(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    plan = {
        "service_name": "API_V4_TEST",
        "odata_version": "4.0",
        "entity_set": "Products",
        "http_method": "GET",
    }
    try:
        asyncio.run(provider.execute_plan(plan))
    except Exception as exc:  # SapReadError without weakening the assertion to its text.
        assert getattr(exc, "code", "") == "sap_paging_path_rejected"
    else:
        raise AssertionError("A cross-service V4 next-link was accepted")


def test_v4_time_guid_and_typed_literals_are_lexically_guarded() -> None:
    literal = EmbeddedODataProvider._odata_literal
    assert literal("23:59:58.5", "Edm.TimeOfDay", "4.0") == "23:59:58.5"
    assert (
        literal("00000000-0000-0000-0000-000000000001", "Edm.Guid", "4.0")
        == "00000000-0000-0000-0000-000000000001"
    )
    for value, value_type in (
        ("25:00:00", "Edm.TimeOfDay"),
        ("not-a-guid", "Edm.Guid"),
        ("1 or Name eq 'x'", "Edm.Decimal"),
        ("2026-08-19 or true", "Edm.Date"),
    ):
        try:
            literal(value, value_type, "4.0")
        except Exception as exc:
            assert getattr(exc, "code", "") == "odata_literal_invalid"
        else:
            raise AssertionError(f"Unsafe typed literal was accepted: {value_type}")


def test_compact_date_literal_is_validated_and_normalized_as_string() -> None:
    literal = EmbeddedODataProvider._odata_literal
    assert literal("2017-10-01", "date_compact", "2.0") == "'20171001'"
    assert literal("20171031", "date_compact", "2.0") == "'20171031'"
    try:
        literal("2017-10-01 or true", "date_compact", "2.0")
    except Exception as exc:
        assert getattr(exc, "code", "") == "odata_literal_invalid"
    else:
        raise AssertionError("Unsafe compact-date literal was accepted")


def test_mixed_version_multi_step_uses_each_registered_adapter(tmp_path: Path) -> None:
    services = []
    for name, version, root in (
        ("API_V2_TEST", "2.0", "/sap/opu/odata/sap/API_V2_TEST"),
        ("API_V4_TEST", "4.0", "/sap/opu/odata4/sap/API_V4_TEST/0001"),
    ):
        services.append(
            {
                "service_name": name,
                "odata_version": version,
                "service_root_path": root,
                "metadata_path": f"{root}/$metadata",
                "catalog_source": "manual",
                "source_hash": f"sha256:{'c' * 64}",
                "status": "seed",
            }
        )
    registry_path = tmp_path / "mixed-services.json"
    registry_path.write_text(
        json.dumps({"schema_version": "2.0", "services": services}), encoding="utf-8"
    )
    data_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/$metadata"):
            return httpx.Response(
                200,
                text=V2_METADATA if "API_V2_TEST" in request.url.path else V4_METADATA,
            )
        data_paths.append(request.url.path)
        if "API_V2_TEST" in request.url.path:
            return httpx.Response(200, json={"d": {"results": [{"ID": "V2"}]}})
        return httpx.Response(200, json={"value": [{"ID": "00000000-0000-0000-0000-000000000004"}]})

    provider = EmbeddedODataProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
        service_registry_path=registry_path,
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(
        provider.execute_plan(
            {
                "plan_kind": "multi_step",
                "steps": [
                    {
                        "step_id": "v2",
                        "service_name": "API_V2_TEST",
                        "odata_version": "2.0",
                        "entity_set": "Orders",
                        "http_method": "GET",
                    },
                    {
                        "step_id": "v4",
                        "service_name": "API_V4_TEST",
                        "odata_version": "4.0",
                        "entity_set": "Products",
                        "http_method": "GET",
                    },
                ],
            }
        )
    )
    assert result["step_results"]["v2"]["odata_version"] == "2.0"
    assert result["step_results"]["v4"]["odata_version"] == "4.0"
    assert any("/sap/opu/odata/sap/API_V2_TEST/Orders" in path for path in data_paths)
    assert any("/sap/opu/odata4/sap/API_V4_TEST/0001/Products" in path for path in data_paths)


def test_repeated_v4_next_link_is_rejected_as_a_paging_cycle(tmp_path: Path) -> None:
    repeated = (
        "https://sap.example.test/sap/opu/odata4/sap/API_V4_TEST/0001/"
        "Products?$skiptoken=REPEAT"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/$metadata"):
            return httpx.Response(200, text=V4_METADATA)
        return httpx.Response(
            200,
            json={
                "value": [{"ID": "00000000-0000-0000-0000-000000000005"}],
                "@odata.nextLink": repeated,
            },
        )

    provider = EmbeddedODataProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
        service_registry_path=registry(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    try:
        asyncio.run(
            provider.execute_plan(
                {
                    "service_name": "API_V4_TEST",
                    "odata_version": "4.0",
                    "entity_set": "Products",
                    "http_method": "GET",
                }
            )
        )
    except Exception as exc:
        assert getattr(exc, "code", "") == "sap_paging_cycle_rejected"
    else:
        raise AssertionError("A repeated V4 next-link was accepted")


def test_duplicate_stable_keys_make_v2_source_inconclusive(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/$metadata"):
            return httpx.Response(200, text=V2_METADATA)
        assert request.url.params["$orderby"] == "ID"
        return httpx.Response(
            200,
            json={"d": {"results": [{"ID": "A"}, {"ID": "A"}]}},
        )

    provider = EmbeddedODataProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
        service_registry_path=registry(tmp_path, version="2.0"),
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(
        provider.execute_plan(
            {
                "service_name": "API_V4_TEST",
                "odata_version": "2.0",
                "entity_set": "Orders",
                "http_method": "GET",
            }
        )
    )
    assert result["status"] == "inconclusive"
    assert result["source_complete"] is False
    assert result["step_results"]["step_1"]["source_complete"] is False
    assert result["validation_issues"] == [
        {"step_id": "step_1", "code": "duplicate_stable_key", "fields": ["ID"]}
    ]


def test_explicit_projection_includes_implicit_stable_ordering_key(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/$metadata"):
            return httpx.Response(200, text=V2_METADATA)
        assert request.url.params["$orderby"] == "ID"
        assert request.url.params["$select"] == "PostingDate,ID"
        return httpx.Response(
            200,
            json={"d": {"results": [{"ID": "A", "PostingDate": "/Date(0)/"}]}},
        )

    provider = EmbeddedODataProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
        service_registry_path=registry(tmp_path, version="2.0"),
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(
        provider.execute_plan(
            {
                "service_name": "API_V4_TEST",
                "odata_version": "2.0",
                "entity_set": "Orders",
                "http_method": "GET",
                "select_fields": ["PostingDate"],
            }
        )
    )

    assert result["status"] == "completed"
    assert result["source_complete"] is True


def test_digit_only_sap_string_keys_use_natural_monotonic_order(tmp_path: Path) -> None:
    def execute(rows: list[dict[str, str]]) -> dict[str, object]:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/$metadata"):
                return httpx.Response(200, text=V2_METADATA)
            return httpx.Response(200, json={"d": {"results": rows}})

        provider = EmbeddedODataProvider(
            base_url="https://sap.example.test",
            username="fixture-user",
            password="fixture-password",
            service_registry_path=registry(tmp_path, version="2.0"),
            transport=httpx.MockTransport(handler),
        )
        return asyncio.run(
            provider.execute_plan(
                {
                    "service_name": "API_V4_TEST",
                    "odata_version": "2.0",
                    "entity_set": "Orders",
                    "http_method": "GET",
                }
            )
        )

    monotonic = execute([{"ID": "60000000"}, {"ID": "200000001"}])
    non_monotonic = execute([{"ID": "200000001"}, {"ID": "60000000"}])

    assert monotonic["source_complete"] is True
    assert monotonic["validation_issues"] == []
    assert non_monotonic["source_complete"] is False
    assert non_monotonic["validation_issues"] == [
        {"step_id": "step_1", "code": "non_monotonic_stable_key", "fields": ["ID"]}
    ]


def test_encoded_v4_next_link_path_traversal_is_rejected(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/$metadata"):
            return httpx.Response(200, text=V4_METADATA)
        return httpx.Response(
            200,
            json={
                "value": [{"ID": "00000000-0000-0000-0000-000000000006"}],
                "@odata.nextLink": (
                    "https://sap.example.test/sap/opu/odata4/sap/API_V4_TEST/0001/"
                    "%2e%2e/OTHER/Products?$skiptoken=x"
                ),
            },
        )

    provider = EmbeddedODataProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
        service_registry_path=registry(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    try:
        asyncio.run(
            provider.execute_plan(
                {
                    "service_name": "API_V4_TEST",
                    "odata_version": "4.0",
                    "entity_set": "Products",
                    "http_method": "GET",
                }
            )
        )
    except Exception as exc:
        assert getattr(exc, "code", "") == "sap_paging_path_rejected"
    else:
        raise AssertionError("An encoded next-link path traversal was accepted")


def test_live_metadata_is_refetched_so_a_stale_cached_version_cannot_authorize_execution(
    tmp_path: Path,
) -> None:
    metadata_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal metadata_calls
        assert request.url.path.endswith("/$metadata")
        metadata_calls += 1
        return httpx.Response(200, text=V2_METADATA if metadata_calls == 1 else V4_METADATA)

    provider = EmbeddedODataProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
        service_registry_path=registry(tmp_path, version="2.0"),
        transport=httpx.MockTransport(handler),
    )
    first = asyncio.run(
        provider.schema("API_V4_TEST", ["Orders"], odata_version="2.0")
    )
    assert first["ok"] is True
    second = asyncio.run(
        provider.validate_plan(
            {
                "service_name": "API_V4_TEST",
                "odata_version": "2.0",
                "entity_set": "Orders",
                "http_method": "GET",
            }
        )
    )
    assert second["ok"] is False
    assert second["validation_issues"][0]["code"] == "odata_version_mismatch"
    assert metadata_calls == 2
