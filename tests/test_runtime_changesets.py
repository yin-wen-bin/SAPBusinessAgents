import json
import subprocess
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.authoring_harness import content_digest
from sap_business_agents_platform.authoring_workspace import AuthoringWorkspace
from sap_business_agents_platform.runtime_changesets import RuntimeChangeSets
from sap_business_agents_platform.runtime_execution import RuntimeExecutionError


def proposal():
    changes = [{'path': 'src/example.py', 'before': 'original', 'after': 'candidate'}]
    return SimpleNamespace(base_commit='abc', base_files={'src/example.py': 'original'}, platform_changes=lambda: changes)


def test_pending_proposal_is_immutable_and_cannot_self_approve(tmp_path):
    service = RuntimeChangeSets(tmp_path)
    record = service.create(proposal(), source_id='run_example')
    assert record == service.get(record['change_set_id'])
    assert record['can_apply'] is False
    with pytest.raises(RuntimeExecutionError, match='integration_verification_required'):
        service.require_applicable(record['change_set_id'], revision=1, digest=record['digest'])
    assert record == service.get(record['change_set_id'])


def test_confirmation_conflict_and_tampering_fail_closed(tmp_path):
    service = RuntimeChangeSets(tmp_path)
    record = service.create(proposal(), source_id='operation')
    with pytest.raises(RuntimeExecutionError, match='conflict'):
        service.require_applicable(record['change_set_id'], revision=2, digest=record['digest'])
    path = tmp_path / (record['change_set_id'] + '.json')
    altered = {**record, 'can_apply': True}
    path.write_text(json.dumps(altered))
    with pytest.raises(RuntimeExecutionError, match='digest_mismatch'):
        service.get(record['change_set_id'])


def test_changeset_path_and_private_key_rejected(tmp_path):
    service = RuntimeChangeSets(tmp_path)
    with pytest.raises(RuntimeExecutionError, match='not_found'):
        service.get('../outside')
    workspace = proposal()
    workspace.platform_changes = lambda: [{'path': 'src/example.py', 'before': '', 'after': '-----BEGIN PRIVATE KEY-----'}]
    with pytest.raises(RuntimeExecutionError, match='sensitive_content'):
        service.create(workspace, source_id='run')


def test_current_source_snapshot_preserves_uncommitted_base(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    def git(*args):
        subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True)
    git('init')
    (repo / 'README.md').write_text('HEAD')
    git('add', '.')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'initial')
    (repo / 'README.md').write_text('user-uncommitted')
    (repo / 'src').mkdir()
    (repo / 'src/new.py').write_text('new-untracked')
    (repo / '.env').write_text('SECRET=must-not-copy')
    workspace = AuthoringWorkspace(repo, tmp_path / 'job')
    workspace.prepare(None, current_source=True)
    assert workspace.base_files == {'README.md': 'user-uncommitted', 'src/new.py': 'new-untracked'}
    assert workspace.base_digest == content_digest(workspace.base_files)
    assert workspace.platform_changes() == []
    assert not (workspace.source / '.env').exists()
    (workspace.source / 'src/new.py').write_text('SDK edit')
    assert workspace.platform_changes() == [{'path': 'src/new.py', 'before': 'new-untracked', 'after': 'SDK edit'}]
    assert (repo / 'src/new.py').read_text() == 'new-untracked'


def test_repair_package_replacement_removes_stale_files(tmp_path):
    workspace = AuthoringWorkspace(tmp_path, tmp_path / 'job')
    workspace.source.mkdir(parents=True)
    workspace.write_package({'manifest': {}, 'rules': 'old', 'files': {'old.md': 'old'}})
    workspace.write_package({'manifest': {}, 'rules': None, 'files': {'new.md': 'new'}})
    assert workspace.read_package() == {'manifest': {}, 'readme': '', 'rules': None, 'files': {'new.md': 'new'}}


def test_build_cache_is_not_a_platform_patch(tmp_path):
    workspace = AuthoringWorkspace(tmp_path, tmp_path / 'job')
    (workspace.source / 'node_modules').mkdir(parents=True)
    (workspace.source / 'node_modules/cache').write_bytes(b'x' * (3 * 1024 * 1024))
    assert workspace.platform_changes() == []


def test_unapplied_dependency_disables_publishability_even_with_acceptance():
    from sap_business_agents_platform.agent_lifecycle import AgentLifecycleService
    service = SimpleNamespace(
        _formal_acceptance=lambda *_: {'verdict': 'PASS'},
        _platform_changes_pending=lambda _: True,
        technical_identity=lambda _: {'kind': 'new_agent', 'confirmed': True},
        store=SimpleNamespace(get_agent_operation=lambda _: None),
    )
    result = AgentLifecycleService._validation_summary(service, {'draft_id': 'draft-example', 'status': 'draft'}, {})
    assert result['publishability'] == {'can_publish': False,
        'blockers': ['runtime_changeset_integration_verification_required']}
