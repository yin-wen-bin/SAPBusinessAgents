"""Real provider with mock HTTP; no model, SAP, credentials or qualification writes."""
import asyncio
from contextlib import contextmanager, asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from sap_business_agents_platform import agent_acceptance_campaigns as campaigns
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.sap_read import EmbeddedODataProvider
from tests.test_agent_acceptance_campaigns import _campaign, _Coordinator, _Runtime, _AnchorBroker, _Lifecycle
from tests.test_odata_v2_v4 import registry, V4_METADATA


@pytest.mark.parametrize('runtime_provider', ['codex', 'workbuddy'])
@pytest.mark.parametrize('change_after', [False, True])
def test_anchor_reuses_fresh_metadata_only_and_replays_every_business_get(tmp_path, runtime_provider, change_after):
    counts = {'metadata': 0, 'business_get': 0}
    current_name = ['fixture']
    def handler(request):
        assert request.method == 'GET'
        if request.url.path.endswith('/$metadata'):
            counts['metadata'] += 1
            return httpx.Response(200, text=V4_METADATA, headers={'OData-Version': '4.0'})
        counts['business_get'] += 1
        return httpx.Response(200, json={'value': [{'ID': '01234567-89ab-cdef-0123-456789abcdef', 'Name': current_name[0]}]},
            headers={'OData-Version': '4.0'})
    provider = EmbeddedODataProvider(base_url='https://sap.example.test', username='fixture', password='fixture',
        service_registry_path=registry(tmp_path), transport=httpx.MockTransport(handler))
    async def scenario():
        expected, calls = {}, []
        # Different step IDs make six distinct plans. No business GET is
        # deduplicated, and saved baseline rows are never an anchor result.
        with provider.metadata_scope('saved-baseline'):
            for index in range(6):
                plan = {'plan_kind': 'multi_step', 'service_name': 'API_V4_TEST', 'odata_version': '4.0', 'http_method': 'GET', 'steps': [{
                    'step_id': f'step_{index}', 'entity_set': 'Products', 'http_method': 'GET',
                    'select_fields': ['ID', 'Name']}]}
                ref = f'ev_fixture_{index}'
                validation = await provider.validate_plan(plan)
                assert validation['ok'], validation
                expected[ref] = await provider.execute_plan(plan)
                calls.append({'tool': 'sap_query_execute', 'evidence_ref': ref,
                    'input': {'plan': plan, 'query': 'fixture'}})
        # Intentionally retain the old baseline cache to prove captures never
        # reuse it. They use fresh unique scopes, then clear only their own.
        assert counts['metadata'] == 1
        counts.update(metadata=0, business_get=0)
        broker = SimpleNamespace(_read_evidence=lambda run, ref: (expected[ref], {}))
        coordinator = _Coordinator()
        coordinator.harness, coordinator.sap_read = SimpleNamespace(broker=broker), provider
        store = RunStore(tmp_path / 'anchors.sqlite3')
        created, _ = _campaign(store, tmp_path)
        jobs = campaigns.AgentAcceptanceJobs(SimpleNamespace(data_root=tmp_path), store, _Lifecycle(), coordinator, _Runtime())
        baseline = {'runtime': {'provider_id': runtime_provider}, 'result': {'tool_calls': calls}}
        for index, position in enumerate(('before', 'after'), start=1):
            if position == 'after' and change_after:
                current_name[0] = 'changed'
            value = await jobs._capture_source_anchor(created['draft_id'], created['campaign_id'], 'baseline',
                baseline, position=position)
            assert value['verdict'] == ('CHANGED' if position == 'after' and change_after else 'PASS')
            assert value['source_count'] == 6
            assert counts == {'metadata': index, 'business_get': index * 6}
            assert provider._metadata_scope.get() is None
            assert all(key[0] == 'saved-baseline' for key in provider._run_metadata)
        provider.clear_schema_scope('saved-baseline')
        assert not provider._run_metadata and not provider._run_metadata_locks
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['cancel', 'timeout', 'rejected'])
def test_anchor_scope_cleanup_and_fail_closed_budget(tmp_path, monkeypatch, failure):
    scopes, cleared, budgets = [], [], []
    raw = {'data': {'results': [], 'source_complete': True}, 'source_complete': True}
    class Provider:
        @contextmanager
        def metadata_scope(self, scope):
            scopes.append(scope)
            yield
        def clear_schema_scope(self, scope):
            cleared.append(scope)
        async def validate_plan(self, plan, query):
            if failure == 'cancel':
                raise asyncio.CancelledError()
            return {'ok': failure != 'rejected', 'normalized_plan': plan}
        async def execute_plan(self, *a, **kw):
            assert failure == 'timeout'
            return raw
    @asynccontextmanager
    async def timeout(seconds):
        budgets.append(seconds)
        yield
        if failure == 'timeout':
            raise TimeoutError()
    monkeypatch.setattr(campaigns.asyncio, 'timeout', timeout)
    async def scenario():
        store = RunStore(tmp_path / 'failure.sqlite3')
        created, _ = _campaign(store, tmp_path)
        coordinator = _Coordinator()
        coordinator.sap_read = Provider()
        coordinator.harness = SimpleNamespace(broker=_AnchorBroker(raw))
        jobs = campaigns.AgentAcceptanceJobs(SimpleNamespace(data_root=tmp_path), store, _Lifecycle(), coordinator, _Runtime())
        baseline = {'result': {'tool_calls': [{'tool': 'sap_query_execute', 'evidence_ref': 'ev_fixture',
            'input': {'plan': {'http_method': 'GET'}, 'query': 'fixture'}}]}}
        if failure == 'cancel':
            with pytest.raises(asyncio.CancelledError):
                await jobs._capture_source_anchor(created['draft_id'], created['campaign_id'], 'baseline', baseline, position='before')
        else:
            result = await jobs._capture_source_anchor(created['draft_id'], created['campaign_id'], 'baseline', baseline, position='before')
            assert result == {'verdict': 'UNAVAILABLE', 'reason_code': 'source_anchor_timeout' if failure == 'timeout' else 'source_anchor_plan_rejected'}
        assert cleared == scopes and len(scopes) == 1
        assert budgets == [120]
    asyncio.run(scenario())
