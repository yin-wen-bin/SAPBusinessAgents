"""Offline provenance checks for an incomplete-result *authoring* scaffold.

This is not an execution/acceptance gate and never issues tool capabilities.
Both Providers use the same persisted Broker and platform lifecycle receipts.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .models import RunStatus


class PendingSourceError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def digest(value: Any) -> str:
    return 'sha256:' + hashlib.sha256(json.dumps(value, ensure_ascii=False,
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _terminal(store, run) -> None:
    state = store.get_harness_state(run.run_id)
    result = run.result
    if (not run.runtime or not run.runtime.configuration_digest or run.cancel_requested or state.get('cleanup_incomplete')
            or state.get('terminal_handoff_failure') or not result or not result.harness
            or result.harness.stop_reason != 'completed'
            or result.harness.diagnostics.get('cleanup_pending')
            or result.harness.diagnostics.get('terminal_handoff_failure')):
        raise PendingSourceError('source_terminal_or_cleanup_unconfirmed')
    # Appended by the shared coordinator only after native handoff, validation
    # and driver cleanup returned. A model's text/status is not this receipt.
    if not any(e.type == 'harness_completed' and e.data.get('stop_reason') == 'completed'
               for e in store.events_after(run.run_id)):
        raise PendingSourceError('source_terminal_or_cleanup_unconfirmed')
    from .harness import _validated_presentation_snapshot
    presentation = result.presentation
    verified = (_validated_presentation_snapshot(presentation.validation_ref,
        store.list_harness_tool_calls(run.run_id)) if presentation and presentation.validation_ref else None)
    if verified is None or verified.model_dump(mode='json') != presentation.model_dump(mode='json'):
        raise PendingSourceError('source_report_not_verified')


def audit(settings, store, session, iteration, run, execution_run, plan) -> dict[str, Any]:
    if (session.get('status') not in {'satisfied', 'draft_created'}
            or session.get('accepted_iteration') != session.get('current_iteration')
            or iteration.get('run_id') != run.run_id
            or not run.result or not iteration.get('result_digest')
            or session.get('accepted_result_digest') != iteration.get('result_digest')
            or digest(run.result.model_dump(mode='json')) != iteration.get('result_digest')):
        raise PendingSourceError('confirmed_source_required')
    for source in {run.run_id: run, execution_run.run_id: execution_run}.values():
        if source.status not in {RunStatus.completed, RunStatus.inconclusive}:
            raise PendingSourceError('query_not_successful')
        _terminal(store, source)
    if plan.get('kind') != 'sap_business_agents_harness':
        raise PendingSourceError('source_read_receipt_missing')
    result_refs = {str(e.get('evidence_ref')) for e in execution_run.result.evidence
                   if isinstance(e, dict) and e.get('evidence_ref')}
    calls = store.list_harness_tool_calls(execution_run.run_id)
    verified_refs = set()
    proofs = []
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', execution_run.run_id):
        raise PendingSourceError('source_evidence_path_invalid')
    directory = settings.data_root.resolve() / 'harness' / execution_run.run_id / 'evidence'
    if directory.resolve() != directory:
        raise PendingSourceError('source_evidence_path_invalid')
    for step in plan.get('steps') or []:
        if step.get('tool') == 'sap_read':
            from .runtime_query_contract import plans
            from .runtime_contract import RuntimeContractError
            try:
                plans(step.get('plan'))
            except RuntimeContractError as exc:
                raise PendingSourceError('invalid_execution_plan') from exc
        ref = step.get('source_evidence_ref')
        if not isinstance(ref, str) or not re.fullmatch(r'ev_[0-9a-f]{24}', ref) or ref not in result_refs:
            raise PendingSourceError('source_evidence_reference_invalid')
        matches = [c for c in calls if c.get('status') == 'completed' and c.get('evidence_ref') == ref
                   and (c.get('output') or {}).get('ok') is True
                   and (c.get('output') or {}).get('evidence_ref') == ref
                   and ((step.get('tool') == 'sap_read' and c.get('tool_name') == 'sap_query_execute'
                         and (c.get('safe_input') or {}).get('plan') == step.get('plan'))
                        or (step.get('tool') == 'skill' and c.get('tool_name') == 'sap_skill_execute'
                            and (c.get('safe_input') or {}).get('skill_id') == step.get('skill_id')
                            and (c.get('safe_input') or {}).get('input') == step.get('input')))]
        if not matches:
            raise PendingSourceError('source_read_receipt_missing')
        for suffix in ('.json', '.meta.json'):
            path = directory / (ref + suffix)
            if not path.is_file() or path.resolve().parent != directory or path.is_symlink():
                raise PendingSourceError('source_evidence_unavailable')
        try:
            raw = (directory / (ref + '.json')).read_bytes()
            meta = json.loads((directory / (ref + '.meta.json')).read_bytes())
        except (OSError, ValueError) as exc:
            raise PendingSourceError('source_evidence_unavailable') from exc
        if ref != 'ev_' + hashlib.sha256(raw).hexdigest()[:24]:
            raise PendingSourceError('source_evidence_digest_mismatch')
        if not isinstance(meta, dict):
            raise PendingSourceError('source_evidence_reference_invalid')
        expected_type = 'sap_live' if step['tool'] == 'sap_read' else 'sap_skill'
        if meta.get('source_type') != expected_type or meta.get('claim_scope') != 'customer_business_fact':
            raise PendingSourceError('source_evidence_reference_invalid')
        for call in matches:
            output = call['output']
            if (output.get('source_type', expected_type) != expected_type
                    or ('source_complete' in output and output['source_complete'] != meta.get('source_complete'))):
                raise PendingSourceError('source_evidence_reference_invalid')
        verified_refs.add(ref)
        proofs.append({'evidence_ref': ref, 'content_digest': 'sha256:' + hashlib.sha256(raw).hexdigest(),
                       'call_id': matches[0]['call_id'], 'source_complete': meta.get('source_complete') is True})
    if not verified_refs:
        raise PendingSourceError('source_read_receipt_missing')
    # Rejected aliases stay diagnostics, never become registered source refs.
    from .harness import _presentation_evidence_refs
    for source in {run.run_id: run, execution_run.run_id: execution_run}.values():
        if not set(_presentation_evidence_refs(source.result.presentation)).issubset(verified_refs):
            raise PendingSourceError('source_evidence_reference_invalid')
    gaps = list(dict.fromkeys(str(g) for g in run.result.completeness.missing_evidence))
    if not run.result.completeness.source_complete and 'source_incomplete' not in gaps:
        gaps.append('source_incomplete')
    if not run.result.completeness.business_complete and 'business_evidence_incomplete' not in gaps:
        gaps.append('business_evidence_incomplete')
    return {'version': '1.0', 'mode': 'needs_review', 'source_session_id': session['session_id'],
            'accepted_iteration': session['accepted_iteration'], 'result_digest': iteration['result_digest'],
            'source_run_id': run.run_id, 'execution_source_run_id': execution_run.run_id,
            'source_runtime_digest': digest(run.runtime.model_dump(mode='json')),
            'execution_runtime_digest': digest(execution_run.runtime.model_dump(mode='json')),
            'plan_digest': digest(plan), 'validation_ref': run.result.presentation.validation_ref,
            'source_complete': run.result.completeness.source_complete,
            'business_complete': run.result.completeness.business_complete,
            'evidence_refs': sorted(verified_refs), 'evidence_proofs': proofs, 'evidence_gaps': gaps,
            'todos': ['review_source_semantics', 'complete_business_output', 'implement_deterministic_rules',
                      'review_parameter_scope', 'run_trial', 'complete_formal_acceptance'],
            'qualification': 'NOT_TESTED', 'executable': False}
