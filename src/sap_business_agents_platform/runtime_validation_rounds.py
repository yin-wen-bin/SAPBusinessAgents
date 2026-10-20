"""Atomic goal round accounting around the existing batch runner, not a runner.

Offline audits do not call this module. A claim precedes live dispatch and is
never refunded after a failed launch. Existing batch/job journals own recovery.
"""
from __future__ import annotations

import time
from pathlib import Path

from .runtime_validation import Authorization, Registry, ValidationError, digest
from .runtime_validation_store import atomic, locked, read


class RoundLedger:
    def __init__(self, goal_file):
        self.path = Path(goal_file).resolve()

    @staticmethod
    def _state(value):
        claims = value.get('round_claims')
        count, limit = value.get('live_validation_rounds_used'), value.get('max_live_validation_rounds')
        if (not isinstance(claims, list) or type(count) is not int or type(limit) is not int
                or count != len(claims) or not 0 <= count <= limit or limit < 1
                or len({c.get('request_id') for c in claims}) != len(claims)):
            raise ValidationError('validation_round_ledger_invalid')
        return claims, count, limit

    def claim(self, manifest, batch, *, request_id, repair_basis):
        """One named frozen batch, all actual dependent business cases explicit."""
        from .runtime_validation_freeze import verify_manifest
        verify_manifest(manifest)
        registry = Registry()
        ids = registry.closure(manifest['selected'])
        business = [registry.entries[k] for k in ids if registry.entries[k].operations]
        if not business or not request_id or not repair_basis:
            raise ValidationError('validation_round_scope_required')
        if any(c.layer == 'acceptance' for c in business) and len(business) != 1:
            raise ValidationError('validation_campaign_requires_separate_round')
        for case in business:
            grant = Authorization(**manifest.get('authorizations', {}).get(case.id, {}))
            grant.check(case, manifest['runtime'], manifest['targets'][case.id], manifest['parameters'][case.id])
        batch = Path(batch).resolve()
        if not batch.is_relative_to(self.path.parent) or batch == self.path.parent:
            raise ValidationError('validation_round_batch_outside_goal')
        body = {'request_id': request_id, 'batch': str(batch), 'manifest_digest': manifest['manifest_digest'],
            'source_digest': manifest['source_snapshot']['digest'], 'runtime_digest': digest(manifest['runtime']),
            'case_ids': [c.id for c in business], 'repair_basis': repair_basis}
        fingerprint = digest(body)
        with locked(self.path.with_suffix('.rounds.lock')):
            goal = read(self.path)
            claims, count, limit = self._state(goal)
            current = next((c for c in claims if c['request_id'] == request_id), None)
            if current:
                if current['fingerprint'] != fingerprint:
                    raise ValidationError('validation_round_request_conflict')
                return False, current
            runtime = manifest['runtime']
            if runtime.get('provider_id') != goal.get('provider'):
                raise ValidationError('validation_round_runtime_changed')
            if any(runtime.get(k) != goal.get(k) for k in ('model', 'service_site', 'environment_digest')):
                raise ValidationError('validation_round_runtime_changed')
            if any(goal.get(k) is not None and runtime.get(field) != goal[k]
                    for k, field in (('sdk_version', 'version'), ('cli_version', 'cli_version'))):
                raise ValidationError('validation_round_runtime_changed')
            if count >= limit:
                raise ValidationError('validation_round_limit_reached')
            # An unresolved prior reservation must be reconciled, not silently
            # replaced merely because its report has not arrived yet.
            if any(c.get('status') not in {'finished', 'launch_failed_confirmed'} for c in claims):
                raise ValidationError('validation_prior_round_reconciliation_required')
            claim = {**body, 'fingerprint': fingerprint, 'number': count + 1,
                'claimed_at': time.time(), 'status': 'claimed'}
            claims.append(claim)
            goal['live_validation_rounds_used'] = count + 1
            atomic(self.path, goal)
            return True, claim

    def finish(self, request_id):
        """Inspect existing terminal artifacts; never launch/relaunch a job."""
        from .runtime_validation_inventory import sha
        from .runtime_validation_freeze import verify_manifest
        with locked(self.path.with_suffix('.rounds.lock')):
            goal = read(self.path)
            claims, _, _ = self._state(goal)
            claim = next((c for c in claims if c['request_id'] == request_id), None)
            if claim is None:
                raise ValidationError('validation_round_not_claimed')
            batch = Path(claim['batch'])
            manifest = read(batch / 'manifest.json')
            verify_manifest(manifest)
            if manifest.get('manifest_digest') != claim['manifest_digest']:
                raise ValidationError('validation_round_binding_changed')
            cleanup = read(batch / 'process-cleanup.json')
            process = read(batch / 'process.json')
            report = read(batch / 'report.json')
            if (cleanup.get('cleanup_complete') is not True or process.get('pid') != cleanup.get('pid')
                    or process.get('source_digest') != claim['source_digest']
                    or cleanup.get('source_digest') != claim['source_digest']
                    or not process.get('start_id') or not isinstance(report, dict)
                    or any(k not in report for k in claim['case_ids'])
                    or any(report[k].get('status') not in {'passed', 'failed', 'blocked', 'cancelled', 'interrupted'}
                        for k in claim['case_ids'])):
                raise ValidationError('validation_round_cleanup_or_result_unproven')
            outcome = {name: sha(batch / name) for name in ('report.json', 'process.json', 'process-cleanup.json')}
            if claim.get('status') == 'finished' and claim.get('outcome') != outcome:
                raise ValidationError('validation_round_outcome_changed')
            claim.update(status='finished', outcome=outcome)
            atomic(self.path, goal)
            return claim
