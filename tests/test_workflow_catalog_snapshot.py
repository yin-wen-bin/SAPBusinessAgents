from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform import workflows
from sap_business_agents_platform.workflows import WorkflowError, WorkflowRepository


@pytest.fixture
def repository(tmp_path):
    original = Path(__file__).resolve().parents[1]
    for name in ('agents', 'workflows'):
        shutil.copytree(original / name, tmp_path / name)
    return WorkflowRepository(tmp_path / 'workflows', AgentRepository(tmp_path / 'agents'))


def first_workflow(repository):
    path = sorted(repository.root.glob('*/*/workflow.json'))[0]
    return path, json.loads(path.read_text(encoding='utf-8'))


def test_catalog_preserves_all_metadata_and_validates_each_package_once(repository, monkeypatch):
    expected = repository._catalog('all')
    calls = []
    original = workflows.validate_workflow

    def validate(*args, **kwargs):
        calls.append(kwargs['source'])
        return original(*args, **kwargs)

    monkeypatch.setattr(workflows, 'validate_workflow', validate)
    assert repository.catalog('all') == expected
    assert len(calls) == len(set(calls)) == len(list(repository.root.glob('*/*/workflow.json')))


def test_next_catalog_observes_changed_content_with_preserved_timestamp(repository):
    path, payload = first_workflow(repository)
    previous = {row['id']: row for row in repository.catalog('all')}
    stat = path.stat()
    payload['title']['zh'] = '目录更新后的业务名称'
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    current = {row['id']: row for row in repository.catalog('all')}
    assert previous[payload['id']]['title']['zh'] != current[payload['id']]['title']['zh']
    assert current[payload['id']]['title']['zh'] == '目录更新后的业务名称'


def test_agent_pin_drift_is_rejected_by_next_catalog_and_real_run_gate(repository):
    repository.catalog('all')
    _, payload = first_workflow(repository)
    node = payload['nodes'][0]
    path = next(path for path in repository.agents.agents_root.glob('*/*/agent.json')
                if path.parent.name == node['agentId'])
    agent = json.loads(path.read_text(encoding='utf-8'))
    if agent['version'] != node['agentVersion']:
        path = path.parent / 'versions' / node['agentVersion'] / 'agent.json'
        agent = json.loads(path.read_text(encoding='utf-8'))
    agent['execution']['inputSchema']['description'] = 'Changed package contract after the first read'
    path.write_text(json.dumps(agent, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(WorkflowError) as catalog_error:
        repository.catalog('all')
    assert catalog_error.value.code == 'agent_version_mismatch'
    with pytest.raises(WorkflowError) as execution_error:
        repository.get_runnable(payload['id'])
    assert execution_error.value.code == 'agent_version_mismatch'


def test_changes_during_catalog_read_fail_closed(repository, monkeypatch):
    original = WorkflowRepository._load
    changed = False

    def load(self, path, **kwargs):
        nonlocal changed
        result = original(self, path, **kwargs)
        if not changed:
            changed = True
            payload = json.loads(path.read_text(encoding='utf-8'))
            payload['title']['zh'] = '并发更新'
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        return result

    monkeypatch.setattr(WorkflowRepository, '_load', load)
    with pytest.raises(WorkflowError) as error:
        repository.catalog('all')
    assert error.value.code == 'workflow_catalog_source_changed'


def test_new_business_history_keeps_deletion_blocked(repository):
    path, payload = first_workflow(repository)
    publication = path.parent / 'publication.json'
    state = json.loads(publication.read_text(encoding='utf-8')) if publication.exists() else {}
    state['state'] = 'inactive'
    publication.write_text(json.dumps(state), encoding='utf-8')

    class Store:
        count = 0

        def workflow_business_run_count(self, workflow_id):
            return self.count if workflow_id == payload['id'] else 0

        def list_workflow_drafts(self):
            return []

    store = Store()
    repository.store = store
    before = {row['id']: row for row in repository.catalog('all')}[payload['id']]
    assert before['lifecycle']['business_run_count'] == 0
    store.count = 1
    after = {row['id']: row for row in repository.catalog('all')}[payload['id']]
    assert after['lifecycle']['business_run_count'] == 1
    assert after['management']['can_delete'] is False
    assert 'workflow_has_business_runs' in after['management']['blockers']
    assert 'workflow_has_business_runs' in repository.delete_blockers(payload['id'])
