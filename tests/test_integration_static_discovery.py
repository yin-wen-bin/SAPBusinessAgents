"""Integration metadata discovery must not inspect execution eligibility."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from sap_business_agents_platform import integrations


@pytest.fixture
def recorded(monkeypatch):
    def constructor(kind):
        def build(*args):
            return kind, deepcopy(args)
        return build
    for kind in ('CodexAppServerIntegrationAdapter', 'WorkBuddyIntegrationAdapter', 'UnavailableIntegrationAdapter'):
        monkeypatch.setattr(integrations, kind, constructor(kind))


def test_definitions_produce_identical_adapters_without_status_or_model_checks(tmp_path, recorded):
    values = [
        {'provider_id': 'codex', 'integration_runtime': {'adapter_id': 'codex-app-server', 'features': {'direct_tool_call': True}}},
        {'provider_id': 'workbuddy', 'integration_runtime': {'adapter_id': 'workbuddy', 'features': {'direct_tool_call': False}}},
        {'provider_id': 'future-provider', 'integration_runtime': {'adapter_id': None}},
    ]
    reference = integrations.build_integration_adapters(tmp_path, SimpleNamespace(list=lambda: deepcopy(values)))
    def forbidden():
        pytest.fail('status/model/qualification inspection during static construction')
    manager = SimpleNamespace(definitions=tuple(SimpleNamespace(**deepcopy(v)) for v in values), list=forbidden)
    assert integrations.build_integration_adapters(tmp_path, manager) == reference
    assert [vars(v) for v in manager.definitions] == values


def test_legacy_manager_fallback_and_empty_definitions(tmp_path, recorded):
    calls = []
    manager = SimpleNamespace(list=lambda: calls.append(True) or [])
    assert integrations.build_integration_adapters(tmp_path, manager) == []
    assert calls == [True]
    manager.definitions = ()
    assert integrations.build_integration_adapters(tmp_path, manager) == []
    assert calls == [True]
