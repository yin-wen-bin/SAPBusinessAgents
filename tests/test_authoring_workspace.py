import asyncio
import json

import pytest

from sap_business_agents_platform.authoring_harness import AuthoringHarnessError
from sap_business_agents_platform.authoring_workspace import AuthoringWorkspace, safe_relative, sandbox_preflight


def successful_observations():
    return {"workspace_write": True, "outside_read_error": {"errno": 13, "winerror": 5},
            "outside_write_error": {"errno": 13, "winerror": 5},
            "network_error": {"errno": 10013, "winerror": 10013}}


@pytest.mark.parametrize("path", ["../x", "/x", "C:/secret", "a/../x", "a\\b", "a//b", "CON.py", "a. /x", "a:stream", "a?b", "a\x00b"])
def test_paths_are_portably_safe(path):
    with pytest.raises(AuthoringHarnessError):
        safe_relative(path)


def test_package_roundtrip_and_protected_diff(tmp_path):
    workspace = AuthoringWorkspace(tmp_path / "repo", tmp_path / "job")
    workspace.source.mkdir(parents=True)
    package = {"manifest": {"slug": "example"}, "readme": "readme", "rules": None, "files": {"docs/guide.md": "guide"}}
    workspace.write_package(package)
    assert workspace.read_package() == package
    protected = workspace.source / "src/security.py"
    protected.parent.mkdir()
    protected.write_text("changed", encoding="utf-8")
    workspace.base_files["src/security.py"] = "original"
    with pytest.raises(AuthoringHarnessError, match="protected_file_changed"):
        workspace.platform_changes()


@pytest.mark.parametrize("failed", ["outside_read_error", "outside_write_error", "network_error", "workspace_write"])
def test_any_failed_isolation_check_blocks_start(tmp_path, monkeypatch, failed):
    workspace = AuthoringWorkspace(tmp_path / "repo", tmp_path / "job")
    workspace.source.mkdir(parents=True)
    async def command(*args, **kwargs):
        result = successful_observations()
        result[failed] = False if failed == "workspace_write" else None
        return 0, json.dumps(result).encode(), b""
    monkeypatch.setattr("sap_business_agents_platform.authoring_workspace.isolated_command", command)
    with pytest.raises(AuthoringHarnessError, match="preflight_failed"):
        asyncio.run(sandbox_preflight(workspace))


def test_success_requires_all_controller_probes(tmp_path, monkeypatch):
    workspace = AuthoringWorkspace(tmp_path / "repo", tmp_path / "job")
    workspace.source.mkdir(parents=True)
    async def command(*args, **kwargs):
        return 0, json.dumps(successful_observations()).encode(), b""
    monkeypatch.setattr("sap_business_agents_platform.authoring_workspace.isolated_command", command)
    monkeypatch.setattr("sap_business_agents_platform.authoring_workspace.sandbox_overrides", lambda _: ["fixed-profile"])
    result = asyncio.run(sandbox_preflight(workspace))
    assert result["status"] == "passed" and len(result["policy_digest"]) == 64


def test_malformed_probe_output_never_counts_as_success(tmp_path, monkeypatch):
    workspace = AuthoringWorkspace(tmp_path / "repo", tmp_path / "job")
    workspace.source.mkdir(parents=True)
    async def command(*args, **kwargs):
        return 0, b"PASS", b""
    monkeypatch.setattr("sap_business_agents_platform.authoring_workspace.isolated_command", command)
    with pytest.raises(AuthoringHarnessError, match="preflight_failed"):
        asyncio.run(sandbox_preflight(workspace))


@pytest.mark.parametrize("observation", [None, {}, True, {"errno": True, "winerror": None},
    {"errno": 10106, "winerror": 10106}, {"errno": 13, "winerror": 10106},
    {"errno": 2, "winerror": 2}, {"errno": 10060, "winerror": 10060},
    {"errno": 10061, "winerror": 10061}, {"errno": None, "winerror": None}])
def test_environment_or_connection_errors_never_prove_isolation(observation):
    from sap_business_agents_platform.authoring_workspace import _permission_denied
    assert not _permission_denied(observation)


@pytest.mark.parametrize("observation", [{"errno": 13, "winerror": None},
    {"errno": 1, "winerror": None}, {"errno": 13, "winerror": 5},
    {"errno": 10013, "winerror": 10013}])
def test_only_access_denied_codes_prove_isolation(observation):
    from sap_business_agents_platform.authoring_workspace import _permission_denied
    assert _permission_denied(observation)


def test_winsock_initialization_failure_blocks_preflight(tmp_path, monkeypatch):
    workspace = AuthoringWorkspace(tmp_path / "repo", tmp_path / "job")
    workspace.source.mkdir(parents=True)
    async def command(*args, **kwargs):
        result = successful_observations()
        result['network_error'] = {'errno': 10106, 'winerror': 10106}
        return 0, json.dumps(result).encode(), b''
    monkeypatch.setattr('sap_business_agents_platform.authoring_workspace.isolated_command', command)
    with pytest.raises(AuthoringHarnessError, match='preflight_failed'):
        asyncio.run(sandbox_preflight(workspace))


def test_command_environment_keeps_windows_runtime_but_not_secrets(monkeypatch):
    import os
    from sap_business_agents_platform.authoring_workspace import isolated_environment
    monkeypatch.setenv('SYSTEMROOT', os.environ.get('SYSTEMROOT', '/system'))
    for key in ['SAP_PASSWORD', 'SAPBA_SAP_URL', 'OPENAI_API_KEY', 'PYTHONPATH', 'NODE_OPTIONS', 'UNRELATED_TOKEN']:
        monkeypatch.setenv(key, 'must-not-inherit')
    result = isolated_environment()
    assert result['SYSTEMROOT']
    assert 'must-not-inherit' not in result.values()
    assert result['PYTHONUTF8'] == '1'


def test_accepted_loopback_is_a_limitation_not_network_pass(tmp_path, monkeypatch):
    workspace = AuthoringWorkspace(tmp_path / 'repo', tmp_path / 'job')
    workspace.source.mkdir(parents=True)
    async def command(*args, **kwargs):
        result = successful_observations()
        result['network_error'] = None
        return 0, json.dumps(result).encode(), b''
    monkeypatch.setattr('sap_business_agents_platform.authoring_workspace.isolated_command', command)
    monkeypatch.setattr('sap_business_agents_platform.authoring_workspace.sandbox_overrides', lambda _: ['fixed'])
    with pytest.raises(AuthoringHarnessError):
        asyncio.run(sandbox_preflight(workspace))
    result = asyncio.run(sandbox_preflight(workspace, accept_loopback_access=True))
    assert result['status'] == 'passed_with_limitations'
    assert result['checks']['network_denied'] is False
    assert result['limitations'] == ['loopback_access_accepted']


@pytest.mark.parametrize('field,value', [
    ('outside_read_error', None), ('outside_write_error', None),
    ('network_error', {'errno': 10106, 'winerror': 10106})])
def test_loopback_acceptance_does_not_waive_other_failures(tmp_path, monkeypatch, field, value):
    workspace = AuthoringWorkspace(tmp_path / 'repo', tmp_path / 'job')
    workspace.source.mkdir(parents=True)
    async def command(*args, **kwargs):
        result = successful_observations()
        result[field] = value
        return 0, json.dumps(result).encode(), b''
    monkeypatch.setattr('sap_business_agents_platform.authoring_workspace.isolated_command', command)
    with pytest.raises(AuthoringHarnessError):
        asyncio.run(sandbox_preflight(workspace, accept_loopback_access=True))
