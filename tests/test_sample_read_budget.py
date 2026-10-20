from types import SimpleNamespace

import pytest

from sap_business_agents_platform.sample_read_budget import bind, before_get
from sap_business_agents_platform.sample_discovery import SampleDiscoveryError


def test_paging_counts_actual_gets_and_cannot_hide_reads_behind_one_tool_call():
    ctx = SimpleNamespace(read_count=1, max_reads=10, business_get_count=0, closed=False, remaining=lambda **kw: 100)
    with bind(ctx):
        for _ in range(10): before_get()
        assert ctx.read_count == ctx.business_get_count == 10
        with pytest.raises(SampleDiscoveryError, match="sample_read_budget_exceeded"): before_get()
    before_get()  # Ordinary fixed Agent/free-query paths have no sample budget.
    assert ctx.business_get_count == 10


def test_existing_token_cannot_read_after_cancel_or_finalization():
    ctx = SimpleNamespace(read_count=1, max_reads=10, business_get_count=0, closed=True, remaining=lambda **kw: 0)
    with bind(ctx), pytest.raises(SampleDiscoveryError, match="sample_finalization_only"):
        before_get()
    assert ctx.business_get_count == 0


def test_embedded_transport_checks_budget_before_every_real_business_get():
    import asyncio
    import httpx
    from sap_business_agents_platform.sap_read.embedded_odata import EmbeddedODataProvider
    calls = []
    def transport(request):
        calls.append(request.method)
        return httpx.Response(200, json={"value": []})
    provider = EmbeddedODataProvider(base_url="https://sap.invalid", username="fixture", password="fixture",
                                     transport=httpx.MockTransport(transport))
    ctx = SimpleNamespace(read_count=1, max_reads=2, business_get_count=0, closed=False, remaining=lambda **kw: 100)
    async def run():
        with bind(ctx):
            for _ in range(2): await provider._request("/Items", params={})
            with pytest.raises(SampleDiscoveryError, match="sample_read_budget_exceeded"):
                await provider._request("/Items", params={})
        await provider._request("/Items", params={})
    asyncio.run(run())
    assert calls == ["GET", "GET", "GET"]
    assert ctx.business_get_count == ctx.read_count == 2
