"""Evidence-based, read-only qualification decisions; never grants a PASS.

This is an audit projection, not another qualification store or execution path.
Only the existing verifier can establish current eligibility or receipt readiness.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from .runtime_validation import Registry, ValidationError, digest
from .runtime_validation_store import relative_file, verify_archive


def dependency_changes(before, after):
    """Compare full declared components, including removed files and graph edges."""
    changes = []
    for name in sorted(set(before.get('components', {})) | set(after.get('components', {}))):
        old = before.get('components', {}).get(name, {})
        new = after.get('components', {}).get(name, {})
        if old.get('requires') != new.get('requires'):
            changes.append({'component': name, 'kind': 'dependency_edges'})
        for file in sorted(set(old.get('files', {})) | set(new.get('files', {}))):
            a, b = old.get('files', {}).get(file), new.get('files', {}).get(file)
            if a != b:
                changes.append({'component': name, 'kind': 'file', 'file': file,
                    'source_digest': a, 'target_digest': b})
    for name in ('version', 'roots', 'budget_seconds'):
        if before.get(name) != after.get(name):
            changes.append({'kind': name})
    return changes


def recover_source_index(root, recorded_files, *, archives=(), git_commit=None, historical_sources=()):
    """Locate exactly bound original bytes, never fall back to the working tree.

    Archive references must pass their complete inventory verification. A Git
    reference is an explicit historical commit, not HEAD/current files. This
    recovers content only: it does not invent a historical checker or case result.
    Original authoring copies may recover individual exactly hashed files, but
    are never relabelled as a verified complete source archive.
    """
    root = Path(root).resolve()
    wanted = {}
    for name, expected in recorded_files.items():
        relative_file(root, name)
        if (not isinstance(expected, str) or len(expected) != 64
                or any(c not in '0123456789abcdef' for c in expected)):
            raise ValidationError('validation_original_source_digest_invalid')
        wanted[name] = expected
    recovered, rejected = {}, []
    for reference in archives:
        try:
            manifest = verify_archive(reference['directory'], reference['digest'])
        except (ValidationError, OSError, ValueError, KeyError):
            rejected.append({'digest': reference.get('digest'), 'reason': 'archive_invalid'})
            continue
        for name in sorted(wanted.keys() - recovered.keys()):
            if name not in manifest['files']:
                continue
            content = relative_file(Path(reference['directory']) / 'source', name).read_bytes()
            if hashlib.sha256(content.replace(b'\r\n', b'\n')).hexdigest() == wanted[name]:
                recovered[name] = {'kind': 'immutable_archive', 'archive_digest': reference['digest'],
                    'archive_directory': str(reference['directory']), 'file': name,
                    'original_normalized_digest': wanted[name], 'artifact_digest': manifest['files'][name]}
    for directory in historical_sources:
        directory = Path(directory).resolve()
        allowed = [root / '.local-data' / name for name in ('workbuddy-authoring', 'authoring-harness')]
        if directory.name != 'source' or not any(directory.is_relative_to(base) for base in allowed):
            raise ValidationError('validation_original_source_location_invalid')
        for name in sorted(wanted.keys() - recovered.keys()):
            path = relative_file(directory, name)
            if not path.is_file():
                continue
            content = path.read_bytes()
            if hashlib.sha256(content.replace(b'\r\n', b'\n')).hexdigest() == wanted[name]:
                recovered[name] = {'kind': 'historical_copy_content', 'source_directory': str(directory),
                    'file': name, 'original_normalized_digest': wanted[name],
                    'artifact_digest': hashlib.sha256(content).hexdigest(),
                    'complete_archive_proven': False}
    if git_commit is not None:
        if (not isinstance(git_commit, str) or len(git_commit) != 40
                or any(c not in '0123456789abcdef' for c in git_commit)):
            raise ValidationError('validation_original_commit_invalid')
        for name in sorted(wanted.keys() - recovered.keys()):
            try:
                content = subprocess.check_output(['git', 'show', f'{git_commit}:{name}'],
                    cwd=root, stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                continue
            if hashlib.sha256(content.replace(b'\r\n', b'\n')).hexdigest() == wanted[name]:
                recovered[name] = {'kind': 'historical_git_blob', 'commit': git_commit, 'file': name,
                    'original_normalized_digest': wanted[name],
                    'artifact_digest': hashlib.sha256(content).hexdigest()}
    return {'recovered': recovered, 'missing': sorted(wanted.keys() - recovered.keys()),
        'rejected_archives': rejected, 'complete': len(recovered) == len(wanted),
        'qualification_granted': False, 'working_tree_used': False}


def qualification_decisions(environment, state, release, snapshot, *, registry=None, receipts=(), include_previous=True):
    """Classify each required case using actual bindings, origins and changes.

    Unknown legacy provenance is not automatically called a model failure or a
    mandatory full rerun. Conversely, a changed execution dependency cannot be
    excused by recovering a report, or by a matching model/worker version.
    """
    from . import runtime_validation_qualification as q
    from . import workbuddy_qualification as old
    from .workbuddy_validation import case_bindings

    registry = registry or Registry()
    try:
        targets = case_bindings(snapshot, release, registry)
    except ValidationError as exc:
        if exc.code != 'validation_runtime_identity_missing':
            raise
        return {'version': 'sapba.validation.history-decisions/1', 'operations': {}, 'model': snapshot.get('model'),
            'actual_model': snapshot.get('actual_model'), 'environment_digest': release['environment_digest'],
            'cases': [{'case_id': c.id, 'case_digest': c.digest, 'operations': list(c.operations), 'mode': c.mode,
                'action': 'evidence_unresolved', 'historical_pass': False, 'current_record_id': None,
                'sources': [], 'ready_receipts': [], 'required_evidence': ['verified_model_identity'],
                'reasons': [exc.code]} for c in registry.entries.values() if c.operations],
            'reuse_candidates': [], 'historical_operations': [], 'read_only': True,
            'qualification_writes': 0, 'model_calls': 0, 'sap_calls': 0}
    manifests = old.dependency_manifests()
    operations, current = q.inspection(environment, state, release, snapshot, targets, registry)
    rows, candidates, historical = [], {}, set()
    verified_releases = {release['environment_digest']: release}
    for case in registry.entries.values():
        if not case.operations:
            continue
        target = targets[case.id]
        selected = current[case.id]
        row = {'case_id': case.id, 'case_digest': case.digest, 'operations': list(case.operations),
            'mode': case.mode, 'action': 'evidence_unresolved', 'historical_pass': False,
            'current_record_id': selected['id'] if selected else None, 'sources': [],
            'ready_receipts': [], 'required_evidence': [], 'reasons': []}
        if selected and not include_previous:
            # The same inspection has just checked this record's full origin.
            # Status does not need to reopen every older failed/PASS archive;
            # the explicit read-only audit still does so by default.
            row.update(action='retain_current', historical_pass=True,
                reasons=['current_provenance_verified'], source_audit='current_only')
            row['sources'] = [{'protocol': q.PROTOCOL, 'record_id': selected['id'],
                'origin_valid': True, 'kind': selected['kind']}]
            historical.update(case.operations)
            rows.append(row)
            continue
        for record in q.ledger(state).values():
            bound = record.get('binding', {})
            if (record.get('kind') != 'case_pass' or bound.get('case_id') != case.id
                    or bound.get('mode') != case.mode or bound.get('runtime') != target['runtime']):
                continue
            valid = bool(selected and selected['id'] == record['id']) or q.verify_pass(environment, record)
            row['historical_pass'] |= valid
            if valid:
                historical.update(case.operations)
            changed = sorted(k for k in set(bound.get('dependencies', {})) | set(target['dependencies'])
                if bound.get('dependencies', {}).get(k) != target['dependencies'].get(k))
            row['sources'].append({'protocol': q.PROTOCOL, 'record_id': record['id'],
                'origin_valid': valid, 'changed_components': changed,
                'same_case': bound.get('case_digest') == target['case_digest'],
                'same_execution': bound.get('execution_digest') == target['execution_digest']})
            if (valid and bound == target
                    and not q.blocks(state, target, release['environment_digest'], record['sequence'], environment=environment)
                    and record.get('environment_digest') != release['environment_digest']):
                candidates[(case.id, record['id'])] = {'case_id': case.id,
                    'operations': list(case.operations), 'mode': case.mode, 'source_record_id': record['id'],
                    'rule': 'identical_execution/1', 'status': 'bound_receipt_required',
                    'required_evidence': ['verified_target_source', 'target_startup'],
                    'qualification_granted': False}
        for record in state.get('qualification_records', {}).values():
            before = record.get('binding', {})
            operation = before.get('operation')
            if record.get('kind') != 'live_pass' or operation not in case.operations:
                continue
            expected = old.binding(snapshot, release, operation, case.mode, manifests[operation])
            if old.subject(before) != old.subject(expected):
                continue
            valid = old.verify_origin(environment, state, record, verified_releases=verified_releases)
            row['historical_pass'] |= valid
            if valid:
                historical.add(operation)
            changes = dependency_changes(record.get('dependency_manifest', {}), manifests[operation])
            row['sources'].append({'protocol': old.VERSION, 'record_id': record['id'],
                'origin_valid': valid, 'changes': changes,
                'same_execution': before.get('execution_digest') == expected['execution_digest'],
                'missing': [name for name in ('source_snapshot', 'checker_report') if not record.get(name)]
                    + ([] if before.get('case_id') else ['case_binding'])})
        for receipt in receipts:
            if receipt.target_binding != target or receipt.target_environment != release['environment_digest']:
                continue
            try:
                result = q.audit_reuse(environment, state, q.ledger(state).get(receipt.source_record_id, {}), receipt)
            except (ValidationError, OSError, KeyError, ValueError):
                continue
            entry = {'case_id': case.id, 'operations': list(case.operations), 'mode': case.mode, **result}
            candidates[(case.id, receipt.source_record_id)] = {**entry, 'status': 'reuse_ready',
                'qualification_granted': False}
            row['ready_receipts'].append(entry['receipt_digest'])
        if selected:
            row.update(action='retain_current', reasons=['current_provenance_verified'])
        elif row['ready_receipts']:
            row.update(action='reuse_ready', reasons=['existing_rule_and_receipt_verified'])
        else:
            proven = [s for s in row['sources'] if s['origin_valid']]
            # Every file in these manifests is a declared execution dependency.
            # Unknown/new/removed edges remain conservative, not cosmetic.
            changed = [s for s in proven if (not s.get('same_execution')
                or s.get('changes') or s.get('changed_components') or s.get('same_case') is False)]
            if proven and len(changed) == len(proven):
                row.update(action='live_retest_required', reasons=['execution_or_case_dependencies_changed'])
            elif proven:
                row['reasons'] = ['original_case_provenance_or_bound_reuse_evidence_required']
            else:
                row['reasons'] = ['no_verified_original_case_evidence']
            row['required_evidence'] = sorted({m for s in proven for m in s.get('missing', [])})
        rows.append(row)
    return {'version': 'sapba.validation.history-decisions/1', 'operations': operations,
        'source_audit': 'all_previous' if include_previous else 'current_and_missing', 'model': snapshot.get('model'),
        'actual_model': snapshot.get('actual_model'), 'environment_digest': release['environment_digest'],
        'cases': rows, 'reuse_candidates': list(candidates.values()), 'historical_operations': sorted(historical),
        'read_only': True, 'qualification_writes': 0, 'model_calls': 0, 'sap_calls': 0}
