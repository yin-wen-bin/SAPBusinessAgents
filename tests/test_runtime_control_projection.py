import copy
import threading
import time
from types import SimpleNamespace

from sap_business_agents_platform.status_projection import SDKDisplayProjection, StatusProjection
from tests.test_status_projection import until


def setup_projection(*, enabled=True, revision=2, clock=time.monotonic, read=None, load=None):
    state = {'enabled': enabled, 'revision': revision}
    environment = SimpleNamespace(state=read or (lambda: copy.deepcopy(state)))
    manager = SimpleNamespace(default_provider_id='codex', workbuddy=SimpleNamespace(environment=environment))
    def snapshot():
        return {'enabled': state['enabled'], 'runtime_configuration_revision': state['revision'],
                'can_enable': True, 'selectable': state['enabled'],
                'blockers': [] if state['enabled'] else ['runtime_disabled']}
    full = StatusProjection({'workbuddy': load or snapshot, 'codex': lambda: {'enabled': True, 'can_enable': True}},
        {'workbuddy': lambda: 1, 'codex': lambda: 1},
        {'workbuddy': lambda: {}, 'codex': lambda: {}}, clock=clock, accept_late={'workbuddy'})
    return state, SDKDisplayProjection(manager, full, clock=clock)


def test_cold_control_get_has_no_disk_wait_and_never_starts_full_gate():
    entered, finish = threading.Event(), threading.Event()
    calls = []
    def read():
        entered.set(); finish.wait(2)
        return {'enabled': True, 'revision': 2}
    state, view = setup_projection(read=read, load=lambda: calls.append('full'))
    started = time.monotonic()
    unknown = view.get('workbuddy')
    assert time.monotonic() - started < .1
    assert entered.wait(1) and not calls
    assert unknown['enabled'] is None and unknown['control_status']['status'] == 'unknown'
    assert unknown['can_enable'] is False and unknown['selectable'] is False
    finish.set(); view.close()


def test_current_enabled_fact_overrides_old_disabled_projection_without_granting_qualification():
    entered, finish = threading.Event(), threading.Event()
    def load():
        entered.set(); finish.wait(2)
        return {'enabled': False, 'runtime_configuration_revision': 1,
                'can_enable': True, 'selectable': False, 'blockers': ['runtime_disabled']}
    _, view = setup_projection(load=load)
    view.qualification.publish('workbuddy', {'enabled': False, 'runtime_configuration_revision': 1,
        'can_enable': True, 'selectable': False, 'blockers': ['runtime_disabled']})
    until(lambda: view.get('workbuddy')['enabled'] is True)
    result = view.get('workbuddy')
    assert entered.wait(1)
    assert result['control_status']['revision'] == 2 and result['selected'] is False
    assert result['can_enable'] is False and result['selectable'] is False
    assert 'runtime_disabled' not in result['blockers']
    finish.set(); until(lambda: not view.qualification._slots['workbuddy']['running'])
    assert view.get('workbuddy')['can_enable'] is False
    view.close()


def test_control_expiry_and_read_error_return_unknown_not_old_enabled():
    now, broken = [0.], [False]
    def read():
        if broken[0]:
            raise ValueError('PRIVATE')
        return {'enabled': True, 'revision': 2}
    _, view = setup_projection(clock=lambda: now[0], read=read)
    until(lambda: view.get('workbuddy')['enabled'] is True)
    broken[0] = True; now[0] = 6
    result = view.get('workbuddy')
    assert result['enabled'] is None and result['can_enable'] is False
    until(lambda: not view.controls._slots['workbuddy']['running'])
    result = view.get('workbuddy')
    assert result['enabled'] is None and 'PRIVATE' not in str(result)
    assert result['control_status']['error_code'] == 'status_projection_failed'
    view.close()


def test_published_control_cannot_be_overwritten_by_old_reader_or_old_response():
    entered, finish = threading.Event(), threading.Event()
    calls = []
    def read():
        calls.append(1); entered.set(); finish.wait(2)
        return {'enabled': False, 'revision': 1}
    _, view = setup_projection(read=read)
    view.get('workbuddy'); assert entered.wait(1)
    view.publish_control('workbuddy', {'enabled': True, 'runtime_configuration_revision': 3})
    assert view.get('workbuddy')['enabled'] is True
    view.publish_control('workbuddy', {'enabled': False, 'runtime_configuration_revision': 2})
    assert view.get('workbuddy')['control_status']['revision'] == 3
    assert calls == [1]
    finish.set(); until(lambda: not view.controls._slots['workbuddy']['running'])
    result = view.get('workbuddy')
    assert result['enabled'] is True and result['control_status']['error_code'] is None
    view.close()


def test_control_change_invalidates_full_result_and_disabled_never_selectable():
    state, view = setup_projection()
    until(lambda: view.get('workbuddy')['selectable'] is True)
    state.update(enabled=False, revision=3)
    view.invalidate(['workbuddy'])
    until(lambda: view.get('workbuddy')['enabled'] is False)
    until(lambda: view.get('workbuddy')['status_refresh']['status'] == 'ready')
    value = view.get('workbuddy')
    assert value['can_enable'] is True and value['selectable'] is False
    assert 'runtime_disabled' in value['blockers']
    view.close()


def test_other_provider_projection_is_unchanged_and_never_reads_workbuddy_control():
    def read():
        raise AssertionError('must not read WorkBuddy')
    _, view = setup_projection(read=read)
    until(lambda: view.get('codex')['can_enable'] is True)
    result = view.get('codex')
    assert result['enabled'] is True and 'control_status' not in result
    assert not view.controls._slots
    view.close()


def test_control_projection_contains_only_safe_configured_identifiers():
    state, view = setup_projection()
    state.update(default_model_id='deepseek-v4.1-flash', service_site='china', service_site_revision=1,
        environment_digest='a' * 64, access_token='PRIVATE', password='PRIVATE')
    until(lambda: view.get('workbuddy')['enabled'] is True)
    value = view.get('workbuddy')
    assert value['default_model_id'] == 'deepseek-v4.1-flash' and value['service_site'] == 'china'
    assert 'PRIVATE' not in str(value)
    view.close()


def test_three_layer_control_revision_is_not_the_model_configuration_revision():
    def load():
        return {'enabled': True, 'runtime_configuration_revision': 0,
                'control_status': {'status': 'ready', 'revision': 300},
                'can_enable': True, 'selectable': True, 'blockers': []}
    _, view = setup_projection(revision=300, load=load)
    until(lambda: view.get('workbuddy')['selectable'] is True)
    value = view.get('workbuddy')
    assert value['runtime_configuration_revision'] == 0
    assert value['control_status']['revision'] == 300
    assert value['can_enable'] is True and value['blockers'] == []
    # A stale full result cannot certify a later control fact, even though the
    # outgoing response itself is overlaid with the latest enabled fact.
    view.publish_control('workbuddy', {'enabled': False, 'runtime_configuration_revision': 0,
        'control_status': {'status': 'ready', 'revision': 301}})
    value = view.get('workbuddy')
    assert value['enabled'] is False and value['control_status']['revision'] == 301
    assert not value['can_enable'] and not value['selectable']
    view.publish_control('workbuddy', {'enabled': True, 'runtime_configuration_revision': 999,
        'control_status': {'status': 'ready', 'revision': 300}})
    assert view.get('workbuddy')['enabled'] is False
    view.close()


def test_explicit_unknown_control_does_not_fall_back_to_model_revision():
    _, view = setup_projection(revision=3)
    view.publish_control('workbuddy', {'enabled': True, 'runtime_configuration_revision': 3,
        'control_status': {'status': 'unknown', 'revision': None}})
    assert not view.controls._slots
    view.close()


def test_real_three_layer_manager_and_display_projection_agree(tmp_path, monkeypatch):
    from tests.test_workbuddy_three_layers import fixture
    manager, _ = fixture(tmp_path, monkeypatch)
    manager.set_enabled(True)
    state = manager.environment.state()
    assert state['revision'] != state.get('configuration_revision', 0)
    _, view = setup_projection(read=manager.environment.state,
        load=lambda: manager.snapshot(admission=True))
    until(lambda: view.get('workbuddy')['selectable'] is True)
    value = view.get('workbuddy')
    assert value['sdk_integration']['status'] == 'certified'
    assert value['model_compatibility']['compatible'] is True
    assert value['control_status']['revision'] == state['revision']
    assert value['can_enable'] is True and value['blockers'] == []
    view.close()


def test_enable_disable_api_publish_confirmed_controls_after_invalidation(tmp_path, monkeypatch):
    from pathlib import Path
    from fastapi.testclient import TestClient
    from sap_business_agents_platform.app import create_app
    from sap_business_agents_platform.config import Settings
    from sap_business_agents_platform.sdk_manager import SDKManagerError
    from sap_business_agents_platform import status_projection
    from tests.test_sdk_manager import FakeSDKManager, HealthyEmbeddedProvider

    events = []
    class Display:
        def get(self, provider):
            return {}
        def invalidate(self):
            events.append('invalidate')
        def publish_control(self, provider, item):
            events.append(('publish', provider, item['enabled']))
        def close(self):
            pass
    class Manager(FakeSDKManager):
        definitions = []
        calls = []
        reject = False
        def set_enabled(self, provider, enabled):
            self.calls.append((provider, enabled))
            if self.reject:
                raise SDKManagerError('Not ready', code='runtime_not_selectable')
            self.item.update(provider_id=provider, enabled=enabled, runtime_configuration_revision=len(self.calls))
            return dict(self.item)
    monkeypatch.setattr(status_projection, 'sdk_display_projection', lambda manager: Display())
    manager = Manager()
    settings = Settings(repository_root=Path(__file__).resolve().parents[1], data_root=tmp_path / 'data',
        draft_root=tmp_path / 'drafts', skillhub_root=tmp_path / 'skills', max_run_seconds=10)
    app = create_app(settings, planner=object(), embedded_provider=HealthyEmbeddedProvider(), sdk_manager=manager)
    with TestClient(app) as client:
        assert client.get('/api/system/sdk-runtimes').headers['cache-control'] == 'no-store'
        assert client.post('/api/system/sdk-runtimes/workbuddy/enable').status_code == 200
        assert events[-2:] == ['invalidate', ('publish', 'workbuddy', True)]
        assert client.post('/api/system/sdk-runtimes/workbuddy/disable',
            headers={'X-SAPBA-Action': 'runtime-disable'}, json={'confirm_provider_id': 'workbuddy'}).status_code == 200
        assert events[-2:] == ['invalidate', ('publish', 'workbuddy', False)]
        manager.reject = True
        assert client.post('/api/system/sdk-runtimes/workbuddy/enable').status_code == 409
        assert events[-1] == 'invalidate' and manager.item['enabled'] is False
        assert manager.calls == [('workbuddy', True), ('workbuddy', False), ('workbuddy', True)]


def test_controlled_restart_can_preserve_every_existing_site_build():
    import re
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / 'scripts/Start-SAPBusinessAgents.ps1').read_text(encoding='utf-8')
    assert '[switch]$PreserveSiteBuilds' in source
    # Normal startup no longer performs cache cleanup at all. Preserve the
    # compatibility switch and require the stronger no-cleanup invariant,
    # rather than requiring the old conditional destructive call to return.
    assert not re.findall(r'(?im)^(?!\s*function\b).*?\bRemove-OldSiteBuilds\b', source)
