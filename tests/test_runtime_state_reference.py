"""Query artifacts may be isolated while retaining the real runtime gates."""
from sap_business_agents_platform.config import Settings


def test_runtime_state_defaults_follow_the_query_store(tmp_path, monkeypatch):
    monkeypatch.delenv('SAPBA_RUNTIME_STATE_ROOT',raising=False)
    settings=Settings.from_env(tmp_path)
    assert settings.sdk_runtime_state_path==settings.data_root/'sdk-runtimes/default.json'


def test_runtime_state_reference_does_not_redirect_query_database(tmp_path, monkeypatch):
    runtime=tmp_path/'existing-runtime'
    artifacts=tmp_path/'test-artifacts'
    monkeypatch.setenv('SAPBA_DATA_ROOT',str(artifacts))
    monkeypatch.setenv('SAPBA_RUNTIME_STATE_ROOT',str(runtime))
    settings=Settings.from_env(tmp_path)
    assert settings.sdk_runtime_state_path==runtime/'default.json'
    assert settings.database_path==artifacts/'platform.sqlite3'
