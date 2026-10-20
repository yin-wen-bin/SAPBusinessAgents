"""Runtime plugin health consumes installation, not another SDK's cases."""
import asyncio
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.sdk_manager import SDKManager
from sap_business_agents_platform.plugins import CodexRuntimePluginProvider
from sap_business_agents_platform.workbuddy_environment import WorkBuddyError


@pytest.mark.parametrize('provider,version', [('codex', '1.0'), ('codex', None), ('workbuddy', '0.3.247'), ('workbuddy', None)])
def test_installation_metadata_never_enters_model_or_qualification_status(provider, version):
    manager = SDKManager.__new__(SDKManager)
    definition = SimpleNamespace(provider_id=provider, ecosystem='python')
    manager._get_provider = lambda value: definition if value == provider else pytest.fail('wrong provider')
    def installed(definition):
        assert provider == 'codex', 'unrelated package probe'
        return version
    manager.adapters = {'python': SimpleNamespace(installed_version=installed)}
    def release():
        assert provider == 'workbuddy', 'unrelated WorkBuddy release inspection'
        if version is None:
            raise WorkBuddyError('workbuddy_environment_missing')
        return {'sdk_version': version}
    manager.workbuddy = SimpleNamespace(environment=SimpleNamespace(release=release))
    manager.list = manager._snapshot = lambda *_: pytest.fail('full qualification/status requested')
    assert manager.installation_status(provider) == {
        'provider_id': provider, 'installed': version is not None, 'current_version': version}
    assert asyncio.run(CodexRuntimePluginProvider(SimpleNamespace(
        manager=manager, current_provider_id=provider)).health())['ok'] is (version is not None)


def test_runtime_health_preserves_legacy_manager_contract():
    manager = SimpleNamespace(list=lambda: [{'provider_id': 'codex', 'installed': True, 'can_enable': False}])
    value = asyncio.run(CodexRuntimePluginProvider(SimpleNamespace(manager=manager, current_provider_id='codex')).health())
    assert value['ok'] is True and value['data']['provider_id'] == 'codex'
    assert value['data']['automatic_fallback'] is False
