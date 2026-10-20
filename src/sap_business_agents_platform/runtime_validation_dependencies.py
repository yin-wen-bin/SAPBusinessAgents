"""Reconstruct an archived case's complete declared dependency closure.

The current declarations are accepted for a source only when the recomputed
whole-component digests exactly match its original binding. Otherwise topology
or missing historical evidence is unknown, never inferred from today's code.
"""
import hashlib
from pathlib import Path

from .runtime_validation import ValidationError, digest
from .runtime_validation_store import relative_file, verify_archive


def archived_scope(case, reference, expected):
    from . import workbuddy_qualification as old
    from .workbuddy_validation import FIXTURES, fixture_digest, data_digest, data_files

    manifest = verify_archive(reference['directory'], reference['digest'])
    source = Path(reference['directory']) / 'source'
    package = source / 'src/sap_business_agents_platform'
    paths, components = set(), {}

    def component(name, stack=()):
        if name in stack or name not in old.COMPONENTS:
            raise ValidationError('validation_dependency_registry_invalid')
        if name in components:
            return
        requires, files = old.COMPONENTS[name]
        values = {}
        for name_in_component in files:
            if name_in_component in {'workbuddy_qualification.py', 'runtime_validation_qualification.py'}:
                continue
            name_in_archive = 'src/sap_business_agents_platform/' + name_in_component
            content = relative_file(source, name_in_archive).read_bytes()
            values[name_in_component] = hashlib.sha256(content.replace(b'\r\n', b'\n')).hexdigest()
            paths.add(name_in_archive)
        components[name] = digest({'requires': list(requires), 'files': values})
        for parent in requires:
            component(parent, (*stack, name))

    try:
        for operation in case.operations:
            for root in ('platform', 'workbuddy_host', *old.ROOTS[operation]):
                component(root)
        components['case_fixture'] = fixture_digest(case, source / 'scripts')
        components['case_data'] = data_digest(case, source)
        new_checker = package / 'runtime_validation_checker.py'
        names = ('runtime_validation.py', 'runtime_validation_checker.py' if new_checker.is_file()
            else 'runtime_validation_qualification.py')
        components['case_checker'] = digest({name: (package / name).read_bytes().hex() for name in names})
        paths.update('src/sap_business_agents_platform/' + name for name in names)
        paths.update('scripts/' + name for name in (*FIXTURES[case.fixture], 'runtime_validation_cases.py',
            'runtime_validation_fixture_policy.py', 'workbuddy_live_cases/live_guard.py', 'workbuddy_validation_auth.py'))
        paths.update(p.relative_to(source).as_posix() for p in data_files(case, source))
    except (OSError, KeyError, ValueError) as exc:
        raise ValidationError('validation_archived_dependencies_unproven') from exc
    if components != expected or not paths.issubset(manifest['files']):
        raise ValidationError('validation_archived_dependencies_mismatch')
    return {'dependencies': components, 'files': sorted(paths), 'archive_digest': reference['digest']}


def operation_equivalence(source, receipt):
    """Both actual closed inventories must agree with their immutable bindings."""
    from .runtime_validation import ValidationCase
    from .runtime_validation_checker_release import compatibility

    before, after = source['binding'], receipt.target_binding
    case = ValidationCase(**source['case_definition'])
    if not compatibility(before, after, source['source_snapshot']):
        raise ValidationError('validation_operation_dependencies_changed')
    original = archived_scope(case, source['source_snapshot'], before['dependencies'])
    target = archived_scope(case, receipt.target_snapshot, after['dependencies'])
    a = verify_archive(source['source_snapshot']['directory'], source['source_snapshot']['digest'])['files']
    b = verify_archive(receipt.target_snapshot['directory'], receipt.target_snapshot['digest'])['files']
    changes = sorted(name for name in set(a) | set(b) if a.get(name) != b.get(name))
    extraction = before['dependencies'].get('case_checker') != after['dependencies'].get('case_checker')
    mapped = {'src/sap_business_agents_platform/runtime_validation_qualification.py',
        'src/sap_business_agents_platform/runtime_validation_checker.py'} if extraction else set()
    relevant = set(original['files']) | set(target['files'])
    if set(changes) & (relevant - mapped):
        raise ValidationError('validation_operation_dependency_bytes_changed')
    return {'source_scope': original, 'target_scope': target, 'changed_files': changes,
        'outside_operation_scope': sorted(set(changes) - relevant),
        'checker_mapping': 'checker_extraction/1' if extraction else None}
