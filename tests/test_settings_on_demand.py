"""Display-route compatibility only; no SDK, authentication, model or SAP calls."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform import status_projection
from tests.test_sdk_manager import FakeSDKManager, HealthyEmbeddedProvider


@pytest.fixture
def display_client(tmp_path, monkeypatch):
    calls = []

    class Display:
        def get_summary(self, provider):
            calls.append(('summary', provider))
            return {'provider_id': provider, 'enabled': True, 'can_enable': False, 'selectable': False}

        def get(self, provider):
            calls.append(('full', provider))
            return {'provider_id': provider, 'enabled': True}

        def close(self):
            pass

    class Manager(FakeSDKManager):
        definitions = [SimpleNamespace(provider_id=name, integration_runtime=False) for name in ('codex', 'workbuddy', 'other')]

        def maintenance_status(self, provider):
            calls.append(('maintenance', provider))
            return {'operation_id': provider + '-existing', 'status': 'running', 'request_id': 'original'}

    monkeypatch.setattr(status_projection, 'sdk_display_projection', lambda manager: Display())
    settings = Settings(repository_root=Path(__file__).resolve().parents[1], data_root=tmp_path/'data',
                        draft_root=tmp_path/'drafts', skillhub_root=tmp_path/'skills')
    app = create_app(settings, planner=object(), embedded_provider=HealthyEmbeddedProvider(),
                     sdk_manager=Manager(), authentication_adapters={})
    with TestClient(app) as client:
        calls.clear()
        yield client, calls


@pytest.mark.parametrize('provider', ['codex', 'workbuddy'])
def test_filtered_summary_reads_only_selected_projection_and_existing_operation(display_client, provider):
    client, calls = display_client
    response = client.get('/api/system/sdk-runtimes', params={'view': 'summary', 'provider_id': provider})
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    value = response.json()
    assert value['default_provider_id'] == 'codex'
    assert [item['provider_id'] for item in value['items']] == [provider]
    assert value['items'][0]['update_status']['request_id'] == 'original'
    assert value['items'][0]['can_enable'] is False
    assert calls == [('summary', provider), ('maintenance', provider)]


def test_unknown_provider_is_rejected_before_any_projection_read(display_client):
    client, calls = display_client
    response = client.get('/api/system/sdk-runtimes?view=summary&provider_id=unknown')
    assert response.status_code == 404
    assert response.json()['detail']['code'] == 'runtime_not_found'
    assert calls == []


@pytest.mark.parametrize('view,kind', [('summary', 'summary'), ('full', 'full')])
def test_unfiltered_list_preserves_all_existing_providers_and_semantics(display_client, view, kind):
    client, calls = display_client
    response = client.get('/api/system/sdk-runtimes', params={'view': view})
    assert response.status_code == 200
    assert [item['provider_id'] for item in response.json()['items']] == ['codex', 'workbuddy', 'other']
    assert calls == [(kind, name) for name in ('codex', 'workbuddy', 'other')] + [
        ('maintenance', name) for name in ('codex', 'workbuddy', 'other')]
