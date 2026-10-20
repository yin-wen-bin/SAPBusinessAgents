"""Selected-case binding parity; no SDK, model, SAP or qualification writes."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from sap_business_agents_platform import workbuddy_validation as bridge
from sap_business_agents_platform import workbuddy_qualification as legacy
from sap_business_agents_platform.runtime_validation import Registry, ValidationError, digest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def measured_binding(monkeypatch):
    registry = Registry()
    seen = []
    manifests = {op: {'components': {'example': {'requires': [], 'files': {'source.py': 'abc'}}}}
        for case in registry.entries.values() for op in case.operations}
    monkeypatch.setattr(legacy, 'dependency_manifests', lambda: manifests)
    monkeypatch.setattr(legacy, 'execution_components', lambda release: {'digest': 'execution'})
    monkeypatch.setattr(bridge, 'fixture_digest', lambda case: digest({'fixture': case.id}))
    def data(case):
        seen.append(case.id)
        return digest({'data': case.id})
    monkeypatch.setattr(bridge, 'data_digest', data)
    monkeypatch.setattr(bridge.v2, 'binding', lambda snapshot, case, components, execution: {
        'case': case.id, 'snapshot': snapshot, 'components': components, 'execution': execution})
    return registry, seen


def test_scoped_bindings_identical_to_full_projection_without_unrelated_data_reads(measured_binding):
    registry, seen = measured_binding
    snapshot = {'model': 'fixture-model'}
    before = deepcopy(snapshot)
    full = bridge.case_bindings(snapshot, {}, registry)
    assert seen == list(registry.entries)
    seen.clear()
    selected = registry.closure(['acceptance.campaign'])
    scoped = bridge.case_bindings(snapshot, {}, registry, case_ids=selected)
    assert scoped == {key: full[key] for key in selected}
    assert seen == selected
    assert not set(seen) & {'role.analysis', 'agent.create', 'workflow.compose', 'sample.scope'}
    assert snapshot == before


@pytest.mark.parametrize('case_ids', [('not-a-case',), 'acceptance.campaign'])
def test_unknown_or_untyped_selection_is_rejected(measured_binding, case_ids):
    registry, seen = measured_binding
    with pytest.raises(ValidationError, match='validation_dependency_invalid'):
        bridge.case_bindings({}, {}, registry, case_ids=case_ids)
    assert seen == []


def test_every_case_and_permission_mode_retains_original_binding(measured_binding):
    registry, _ = measured_binding
    full = bridge.case_bindings({}, {}, registry)
    for key in registry.entries:
        assert bridge.case_bindings({}, {}, registry, case_ids=[key]) == {key: full[key]}
    assert registry.entries['agent.explain'].mode == 'bounded'
    assert registry.entries['agent.revise'].mode == 'trusted_local'


@pytest.fixture
def scoped_adapter(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    import runtime_validation_cases as adapters
    from sap_business_agents_platform import runtime_validation_freeze as freeze
    from sap_business_agents_platform import runtime_validation_qualification as qualification
    selected = Registry().closure(['acceptance.campaign'])
    bound = {key: {'case': key} for key in selected}
    expected_bound = deepcopy(bound)
    flags = []
    adapter = adapters.WorkBuddyCases.__new__(adapters.WorkBuddyCases)
    adapter.root = tmp_path
    adapter.manifest = {'runtime': {'model': 'fixture-model'}, 'selected': ['acceptance.campaign'],
        'case_bindings': bound, 'source_snapshot': {'directory': str(tmp_path), 'digest': 'source'},
        'controls': {'control.json': 'control'}}
    adapter.manager = SimpleNamespace(environment=SimpleNamespace(state=lambda: {'enabled': False}))
    monkeypatch.setattr(adapters, 'verify_archive', lambda *a: flags.append('archive'))
    monkeypatch.setattr(adapters, 'process_binding', lambda *a: flags.append('loaded_code'))
    monkeypatch.setattr(adapters, 'read', lambda *a: {'files': {'source.py': 'source'}})
    monkeypatch.setattr(qualification, 'active', lambda state: True)
    monkeypatch.setattr(freeze, 'require_authentication_binding', lambda *a: flags.append('authentication'))
    def inspect(*a, **kw):
        assert kw == {'formal': True}
        flags.append('full_worker_integrity_and_saved_checks')
        return {}
    monkeypatch.setattr(bridge, 'inspect_saved', inspect)
    def bindings(snapshot, release, registry, *, case_ids):
        assert list(case_ids) == selected
        flags.append('selected_case_bindings')
        return deepcopy(expected_bound)
    monkeypatch.setattr(bridge, 'case_bindings', bindings)
    from sap_business_agents_platform import runtime_validation_inventory as inventory
    def sha(path):
        flags.append(Path(path).name)
        return 'control' if Path(path).name == 'control.json' else 'source'
    monkeypatch.setattr(inventory, 'sha', sha)
    return adapter, flags


def test_pre_tool_guard_keeps_auth_install_archive_controls_and_source_checks(scoped_adapter):
    adapter, flags = scoped_adapter
    for _ in range(2):
        adapter.check_binding(adapter.manifest['runtime'])
    for check in ('archive', 'loaded_code', 'authentication',
            'full_worker_integrity_and_saved_checks', 'selected_case_bindings', 'control.json', 'source.py'):
        assert flags.count(check) == 2


@pytest.mark.parametrize('changed', ['missing', 'extra', 'different_value'])
def test_frozen_case_requirements_cannot_be_removed_or_replaced(scoped_adapter, changed):
    adapter, _ = scoped_adapter
    if changed == 'missing':
        adapter.manifest['case_bindings'].pop('infrastructure.current')
    elif changed == 'extra':
        adapter.manifest['case_bindings']['role.analysis'] = {}
    else:
        adapter.manifest['case_bindings']['acceptance.campaign'] = {'wrong': True}
    with pytest.raises(ValidationError, match='validation_case_bindings_incomplete|validation_dependency_changed'):
        adapter.check_binding(adapter.manifest['runtime'])
