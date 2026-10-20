"""Safe read-only history indexing and case/operation counts. Never grants PASS."""
from collections import defaultdict
from pathlib import Path

from .runtime_validation import Registry, ValidationError, digest
from .runtime_validation_inventory import inventory, read, sha


def audit_history(root, model=None):
    root = Path(root)
    result = inventory(root)
    state = read(root / '.local-data/runtimes/workbuddy/state.json')
    registry, reports, snapshots = Registry(), [], {}
    paths = set()
    for directory in ('workbuddy-validation', 'workbuddy-baseline', 'shared-sample-scope'):
        base = root / '.local-data' / directory
        paths.update(base.rglob('*report*.json'))
        paths.update(base.rglob('*summary*.json'))
    # Index only known metadata keys. Never include full model reports/evidence.
    def job_ids(value):
        found = set()
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {'job_id', 'task_id', 'request_id', 'operation_id', 'run_id'} and isinstance(child, str):
                    found.add(child)
                elif key in {'stages', 'jobs', 'owned_jobs', 'case_stages', 'result', 'case_result'}:
                    found.update(job_ids(child))
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, str):
                    found.add(child)
                else:
                    found.update(job_ids(child))
        return found
    def add_snapshot(snapshot, path):
        if isinstance(snapshot, dict) and snapshot.get('provider_id') == 'workbuddy':
            snapshots[digest(snapshot)] = {'model': snapshot.get('model'),
                'artifact': path.relative_to(root).as_posix(), 'sha256': sha(path)}
    for path in sorted(paths):
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                continue
            value = read(path)
            snapshot = value.get('runtime') if isinstance(value, dict) else None
            add_snapshot(snapshot, path)
            reports.append({'path': path.relative_to(root).as_posix(), 'sha256': sha(path), 'job_ids': sorted(job_ids(value))})
        except (OSError, ValueError):
            continue
    snapshot_paths = set((root / '.local-data/workbuddy-validation').rglob('runtime-snapshot.json'))
    snapshot_paths.update((root / '.local-data/workbuddy-validation').rglob('freeze.json'))
    for path in sorted(snapshot_paths):
        try:
            snapshot = read(path)
            add_snapshot(snapshot, path)
            add_snapshot(snapshot.get('runtime'), path)
            # Explicitly saved per-provider freeze bindings, not text search or
            # today's model defaults, recover older sample/query attribution.
            add_snapshot(snapshot.get('bindings', {}).get('workbuddy'), path)
        except (OSError, ValueError):
            continue
    by_operation, unattributed = defaultdict(list), []
    for key, value in state.get('capabilities', {}).items():
        if value.get('status') != 'validated':
            continue
        job_id = value.get('job_id')
        job_path = root / '.local-data/runtimes/workbuddy/jobs' / (digest(job_id) + '.json')
        job = read(job_path) if job_path.exists() else {}
        operation = job.get('operation') or key.rsplit(':', 2)[-2]
        recovered = snapshots.get(job.get('runtime_digest'), {})
        requested_model = job.get('runtime_model') or recovered.get('model')
        if model and requested_model is None:
            unattributed.append({'legacy_key': key, 'job_id': job_id, 'operation': operation,
                'reason': 'original_runtime_model_unknown'})
            continue
        if model and requested_model != model:
            continue
        by_operation[operation].append({'legacy_key': key, 'job_id': job_id,
            'model': requested_model, 'model_binding_source': recovered or None,
            'environment_digest': value.get('environment_digest'),
            'modes': value.get('permission_modes', []), 'checked_at': value.get('checked_at'),
            'owned_job': bool(job), 'cleanup_complete': job.get('cleanup_complete') is True,
            'candidate_reports': [p for p in reports if job_id in p['job_ids']],
            'runtime_binding_recovered': bool(recovered),
            'case_binding_present': bool(job.get('validation_case_binding')),
            'provenance_status': 'candidate_only; verify case/source/checker independently',
            'reason': 'Original case/checker/source archive must be proven; current files cannot fill historical gaps.'})
    matrix = []
    for case in registry.entries.values():
        if not case.operations:
            continue
        histories = [h for op in case.operations for h in by_operation[op] if case.mode in h['modes']]
        records = [r for r in result['records'] if r['kind'] == 'live_pass' and r['operation'] in case.operations
            and r['mode'] == case.mode and (not model or r['model'] == model)]
        matrix.append({'case_id': case.id, 'case_digest': case.digest, 'operations': list(case.operations), 'mode': case.mode,
            'historical_operation_pass': bool(histories or records), 'legacy_evidence': histories,
            'later_records': records, 'current_qualification': 'not_granted_by_audit',
            'audit': 'original_provenance_missing' if histories or records else 'no_original_pass',
            'next_action': 'recover_original_artifacts_or_authorize_targeted_retest' if histories or records else 'authorize_case_when_prerequisites_ready'})
    result.update(case_matrix=matrix, selected_model=model, unattributed_legacy=unattributed,
        historical_operations=sorted(op for op, rows in by_operation.items() if rows and registry.requirements(op)),
        planned_operations=len({op for c in registry.entries.values() for op in c.operations}),
        planned_business_cases=len(matrix), protocol_active=bool(state.get('qualification_protocol')),
        audit_rules='sapba.validation.audit/1', applied_receipts=0,
        limitation='Discovered reports are candidates, not proof of exact historical source/checker binding.')
    if state.get('qualification_protocol') and model:
        from .workbuddy_environment import WorkBuddyEnvironment, WorkBuddyError
        from .workbuddy_qualification import runtime_context
        from .runtime_validation_history import qualification_decisions
        try:
            environment = WorkBuddyEnvironment(root)
            decisions = qualification_decisions(environment, state, environment.release(), runtime_context(state, model))
            result['qualification_decisions'] = decisions
            by_case = {row['case_id']: row for row in decisions['cases']}
            for row in matrix:
                decision = by_case[row['case_id']]
                row.update(audit=decision['action'], next_action=decision['action'],
                    current_record_id=decision['current_record_id'], evidence_decision=decision)
                row['historical_operation_pass'] |= decision['historical_pass']
            result['historical_operations'] = sorted(set(result['historical_operations']) | set(decisions['historical_operations']))
        except (ValidationError, WorkBuddyError, OSError, ValueError, KeyError) as exc:
            result['decision_error'] = getattr(exc, 'code', 'validation_evidence_unavailable')
    return result


def summarize(registry, case_results):
    operations = {}
    for operation in sorted({op for c in registry.entries.values() for op in c.operations}):
        required = registry.requirements(operation)
        missing = [c.id for c in required if case_results.get(c.id, {}).get('status') != 'passed']
        operations[operation] = {'complete_this_batch': not missing, 'missing_cases': missing,
            'required_modes': sorted({c.mode for c in required})}
    return {'cases': case_results, 'operations': operations,
        'case_count': len(case_results), 'complete_operation_count': sum(v['complete_this_batch'] for v in operations.values()),
        'note': 'Batch results, historical PASS and production qualification are separate.'}
