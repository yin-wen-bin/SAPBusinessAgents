"""Real Provider with fault-injected HTTP; no SAP, model or real credentials."""
import asyncio
from contextlib import nullcontext

import httpx
import pytest

from sap_business_agents_platform.sap_read import EmbeddedODataProvider
from tests.test_odata_v2_v4 import registry, V4_METADATA


PLAN = {"service_name": "API_V4_TEST", "odata_version": "4.0", "entity_set": "Products",
        "plan_kind": "direct", "http_method": "GET", "select_fields": ["ID", "Name"]}


def provider(tmp_path, handler):
    return EmbeddedODataProvider(base_url="https://sap.example.test", username="synthetic", password="synthetic",
        service_registry_path=registry(tmp_path), transport=httpx.MockTransport(handler))


def metadata():
    return httpx.Response(200, text=V4_METADATA, headers={"OData-Version": "4.0"})


@pytest.mark.parametrize("first_failure", ["timeout", "connection", 502, 503, 504])
def test_transient_metadata_failure_recovers_without_skipping_live_validation(tmp_path, first_failure):
    calls = []
    def handler(request):
        assert request.method == "GET"
        calls.append(request.url.path)
        if request.url.path.endswith("/$metadata"):
            if len(calls) == 1:
                if first_failure == "timeout":
                    raise httpx.ReadTimeout("synthetic", request=request)
                if first_failure == "connection":
                    raise httpx.ConnectError("synthetic", request=request)
                return httpx.Response(first_failure)
            return metadata()
        return httpx.Response(200, json={"value": [{"ID": "01234567-89ab-cdef-0123-456789abcdef", "Name": "fixture"}]},
            headers={"OData-Version": "4.0"})
    source = provider(tmp_path, handler)
    async def scenario():
        assert (await source.validate_plan(PLAN))["ok"] is True
        assert len(calls) == 2 and all(path.endswith("/$metadata") for path in calls)
        response = await source.execute_plan(PLAN)
        assert response["ok"] is True and response["data"]["results"][0]["Name"] == "fixture"
        # Execute still obtains fresh live metadata; the recovered validation
        # neither authorizes a stale execution nor substitutes business rows.
        assert len(calls) == 4 and calls[2].endswith("/$metadata") and calls[3].endswith("/Products")
    asyncio.run(scenario())


@pytest.mark.parametrize("scope,expected_attempts", [(None, 2), ("owned-run", 1)])
@pytest.mark.parametrize("failure", ["timeout", 503])
def test_exhausted_metadata_reads_fail_closed_without_inventing_missing_fields(tmp_path, scope, expected_attempts, failure):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        assert request.method == "GET" and request.url.path.endswith("/$metadata")
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic", request=request)
        return httpx.Response(failure)
    source = provider(tmp_path, handler)
    async def scenario():
        with source.metadata_scope(scope) if scope else nullcontext():
            result = await source.validate_plan(PLAN)
        assert result["ok"] is False
        expected = "sap_read_timeout" if failure == "timeout" else "sap_read_http_error"
        assert [issue["code"] for issue in result["validation_issues"]] == [expected]
        assert len(calls) == expected_attempts
    asyncio.run(scenario())


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_auth_registration_and_application_errors_are_not_retried(tmp_path, status):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(status)
    result = asyncio.run(provider(tmp_path, handler).validate_plan(PLAN))
    assert result["ok"] is False and len(calls) == 1
    assert [issue["code"] for issue in result["validation_issues"]] == ["sap_read_http_error"]
    assert result["validation_issues"][0]["detail"]["http_status"] == status


def test_invalid_metadata_is_rejected_without_retry_or_field_inference(tmp_path):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, text="<broken>")
    result = asyncio.run(provider(tmp_path, handler).validate_plan(PLAN))
    assert not result["ok"] and len(calls) == 1
    assert [issue["code"] for issue in result["validation_issues"]] == ["sap_metadata_invalid"]


def test_real_field_drift_is_still_rejected_on_the_next_live_validation(tmp_path):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        text = V4_METADATA if len(calls) == 1 else V4_METADATA.replace('Name="Name"', 'Name="Renamed"')
        return httpx.Response(200, text=text, headers={"OData-Version": "4.0"})
    source = provider(tmp_path, handler)
    async def scenario():
        assert (await source.validate_plan(PLAN))["ok"] is True
        result = await source.validate_plan(PLAN)
        assert not result["ok"] and len(calls) == 2
        assert any(issue["code"] == "schema_drift_field_unavailable" and issue["field"] == "Name"
            for issue in result["validation_issues"])
    asyncio.run(scenario())


def test_cancellation_is_not_retried(tmp_path):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(provider(tmp_path, handler).validate_plan(PLAN))
    assert len(calls) == 1
