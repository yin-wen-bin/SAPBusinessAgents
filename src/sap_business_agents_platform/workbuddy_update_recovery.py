"""Explicit recovery of a completed candidate probe. Never install or call models."""
from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from .sdk_update import failure
from .startup_control import atomic, file_lock
from .workbuddy_environment import WorkBuddyEnvironment, digest, file_hash
from .workbuddy_sdk_update import source_binding

SECONDS = 600


def recovery_offer(registry, record):
    parent = record.get('parent_operation_id') or record.get('operation_id')
    offer = {'available': False, 'parent_operation_id': parent,
             'target_version': record.get('target_version')}
    if record.get('parent_operation_id'):
        offer['operation_id'] = record['operation_id']
        if record.get('status')=='rolled_back' and record.get('cleanup_complete') is True:
            try:
                from .workbuddy_update_switch import load_anchor
                from .workbuddy_integration import manifest,INFRASTRUCTURE_FILES
                root=registry.repository_root/'.local-data/sdk-update-jobs/workbuddy'
                anchor=load_anchor(root,record)
                changed=digest(manifest(INFRASTRUCTURE_FILES))!=digest(anchor['target_certificate']['binding']['infrastructure'])
                if changed and source_binding(registry.workbuddy.environment.state())==record.get('source_binding'):
                    offer.update(available=True,retry_of=record['operation_id'])
            except Exception:
                offer['blocked_reason']='workbuddy_update_recovery_unavailable'
        return offer
    if record.get('status') != 'failed' or record.get('cleanup_complete') is not True or not record.get('candidate_environment_digest') or record.get('model_submitted') is not True:
        return offer
    try:
        if source_binding(registry.workbuddy.environment.state()) != record.get('source_binding'):
            offer['blocked_reason'] = 'sdk_update_binding_changed'
        else:
            offer['available'] = True
    except Exception:
        offer['blocked_reason'] = 'workbuddy_update_evidence_required'
    return offer


def read_bound(path, root):
    path = Path(path)
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise failure('workbuddy_update_evidence_required', 'recovery_verification')
    content = path.read_bytes()
    import hashlib
    return json.loads(content), {'path': str(path), 'sha256': hashlib.sha256(content).hexdigest()}


def load_parent(registry, parent_id, target):
    if not isinstance(parent_id, str) or not re.fullmatch(r'update_[a-f0-9]{32}', parent_id):
        raise failure('sdk_update_plan_invalid', 'recovery_verification')
    root = registry.repository_root / '.local-data/sdk-update-jobs/workbuddy'
    try:
        parent, parent_ref = read_bound(root / (parent_id + '.json'), root)
        if (parent.get('operation_id') != parent_id or parent.get('parent_operation_id')
                or parent.get('status') != 'failed' or parent.get('cleanup_complete') is not True
                or parent.get('model_submitted') is not True or parent.get('target_version') != target):
            raise failure('workbuddy_update_recovery_unavailable', 'recovery_verification')
        transaction = registry.repository_root / '.local-data/sdk-update-cache/workbuddy' / ('candidate-' + parent_id)
        request, request_ref = read_bound(transaction / 'request.json', registry.repository_root)
        receipt, receipt_ref = read_bound(transaction / 'result.json', registry.repository_root)
        if receipt.get('environment_digest') != parent.get('candidate_environment_digest') or request.get('operation_id') != parent_id:
            raise failure('sdk_update_binding_changed', 'recovery_verification')
        updater = registry._workbuddy_updater()
        bundle = request['bundle']
        directory = Path(bundle['directory'])
        if directory.resolve() != updater._cache(target).resolve():
            raise failure('sdk_download_integrity_failed', 'recovery_verification')
        updater._verify_files(directory, bundle)
        updater._verify_cli(next(directory / key for key,value in bundle['files'].items()
                                 if value['name'] == updater.package), bundle)
        updater._verify_prepared_receipt(receipt, bundle, request, parent['source_binding'])
        candidate = WorkBuddyEnvironment(root / parent_id / 'candidate')
        candidate.release = lambda key=None: registry.workbuddy.environment.release(key or receipt['environment_digest'])
        state = candidate.state()
        model = parent['source_binding']['default_model_id']
        check = state['models'][model]
        submitted = state['model_check_requests'][parent_id]
        from .workbuddy_sdk_target_audit import target_model_origin
        from .workbuddy_site import matches
        if (state.get('environment_digest') != receipt['environment_digest']
                or state.get('default_model_id') != model
                or submitted.get('status') != 'completed' or submitted.get('result') != check
                or submitted.get('job_id') != 'model_check_' + digest(parent_id)
                or check.get('source_job_id') != submitted.get('job_id')
                or check.get('compatible') is not True or check.get('identity_known') is not True
                or check.get('environment_digest') != receipt['environment_digest']
                or check.get('authentication_revision') != parent['source_binding']['authentication_revision']
                or not matches(check, parent['source_binding'])
                or not target_model_origin(candidate, check, model)):
            raise failure('workbuddy_update_evidence_required', 'recovery_verification')
        job = candidate.root / 'jobs' / (digest(check['source_job_id']) + '.json')
        refs = {'parent': parent_ref, 'request': request_ref, 'receipt': receipt_ref,
                'candidate_state': {'path': str(candidate.state_path), 'sha256': file_hash(candidate.state_path)},
                'job': {'path': str(job), 'sha256': file_hash(job)}}
        return {'parent': parent, 'receipt': receipt, 'state': state, 'check': check,
                'candidate': candidate, 'references': refs, 'input_digest': digest(refs)}
    except (OSError, ValueError, KeyError, StopIteration, TypeError) as exc:
        raise failure('workbuddy_update_evidence_required', 'recovery_verification') from exc


def response(owner, definition, record):
    release = owner.registry.workbuddy.environment.release()
    public = {k: copy.deepcopy(v) for k,v in record.items() if k in {
        'operation_id','parent_operation_id','status','phase','started_at','updated_at','target_version',
        'before_version','candidate_environment_digest','error','outcome','cleanup_complete',
        'observed_versions','model_submitted','model_check_limit','recovery','checkpoints','reconciliation','retry_of','primary_failure'}}
    return {'provider_id': 'workbuddy', 'sdk_id': definition.sdk_id, 'operation_id': record['operation_id'],
            'current_version': release['sdk_version'], 'cli_version': release['cli_version'],
            'environment_digest': release['environment_digest'], 'update_status': public,
            'maintenance_kind': 'online_upgrade_recovery', 'restart_required': False,
            'activated': record.get('status') == 'completed'}


async def submit(owner, definition, parent_id, target, *, retry_of=None):
    if not definition.update_enabled:
        raise failure('sdk_update_disabled', 'recovery_verification')
    if not isinstance(target, str) or not target:
        raise failure('sdk_update_target_required', 'recovery_verification')
    root = owner.root
    root.mkdir(parents=True, exist_ok=True)
    with file_lock(root / 'submission.lock', wait=0):
        if not re.fullmatch(r'update_[a-f0-9]{32}', parent_id):
            raise failure('sdk_update_plan_invalid', 'recovery_verification')
        parent, parent_ref = read_bound(root / (parent_id + '.json'), root)
        if parent.get('target_version') != target:
            raise failure('sdk_update_binding_changed', 'recovery_verification')
        key = digest([parent_id, parent_ref['sha256'], parent.get('candidate_environment_digest')])
        prior=None;prior_ref=None
        if retry_of is not None:
            if not isinstance(retry_of,str) or not re.fullmatch(r'recovery_[a-f0-9]{32}',retry_of):
                raise failure('sdk_update_plan_invalid','recovery_verification')
            prior,prior_ref=read_bound(root/(retry_of+'.json'),root)
            if (prior.get('operation_id')!=retry_of or prior.get('parent_operation_id')!=parent_id
                    or prior.get('candidate_environment_digest')!=parent.get('candidate_environment_digest')
                    or prior.get('status')!='rolled_back' or prior.get('cleanup_complete') is not True):
                raise failure('workbuddy_update_recovery_unavailable','recovery_verification')
            key=digest([key,retry_of,prior_ref['sha256']])
        operation_id = 'recovery_' + key[:32]
        path = root / (operation_id + '.json')
        # Deterministic ID survives lost responses and replacement of current.json.
        if path.exists():
            existing = json.loads(path.read_bytes())
            if existing.get('recovery_key') != key:
                raise failure('sdk_update_binding_changed', 'recovery_verification')
            if existing.get('status') == 'running' and not (owner.task is not None and not owner.task.done() and (owner.record or {}).get('operation_id')==operation_id):
                existing.update(status='needs_review',cleanup_complete=False,
                    error={'code':'sdk_update_outcome_unconfirmed','phase':existing.get('phase','reconciliation')})
                atomic(path,existing)
                if existing.get('switch_intent'):
                    owner.registry.authentication_service.sdk_maintenance_active.add('workbuddy')
            return response(owner, definition, existing)
        if owner.task is not None and not owner.task.done() or (owner.record or {}).get('status') in {'running','needs_review','cleanup_pending'}:
            raise failure('sdk_update_busy', 'recovery_verification')
        if prior is not None and not recovery_offer(owner.registry,prior)['available']:
            raise failure('workbuddy_update_recovery_unavailable','recovery_verification')
        if not recovery_offer(owner.registry,parent)['available']:
            raise failure('workbuddy_update_recovery_unavailable','recovery_verification')
        if source_binding(owner.registry.workbuddy.environment.state()) != parent['source_binding']:
            raise failure('sdk_update_binding_changed', 'recovery_verification')
        now = time.time()
        owner.record = {'version':1, 'operation_id':operation_id, 'parent_operation_id':parent_id,
            'parent_digest':parent_ref['sha256'], 'recovery_key':key, 'status':'running',
            'retry_of':retry_of,'retry_reference':prior_ref,
            'phase':'recovery_verification', 'started_at':datetime.fromtimestamp(now,timezone.utc).isoformat(),
            'deadline_at':now+SECONDS, 'before_version':parent['before_version'], 'target_version':target,
            'source_binding':parent['source_binding'], 'source_environment_digest':parent['source_environment_digest'],
            'candidate_environment_digest':parent['candidate_environment_digest'],
            'model_submitted':True, 'model_check_limit':0, 'cleanup_complete':True,
            'input_digest':None, 'input_references':{},
            'recovery':{'available':False,'parent_operation_id':parent_id,'target_version':target,'operation_id':operation_id}}
        owner.emit()
        owner.task = asyncio.create_task(run(owner), name=operation_id)
        return response(owner, definition, owner.record)


async def verify_reused(owner, backend, inputs):
    from .workbuddy_online_update import CandidateSession
    from .workbuddy_online_evidence import infrastructure, certificate, copy_job, copy_evidence
    from .workbuddy_sdk_target_audit import TargetAdmissionAudit, target_model_origin
    receipt = inputs['receipt']
    session = CandidateSession(owner.registry, owner.record, receipt['environment_digest'], initial_state=inputs['state'])
    backend.session = session
    session.evidence_id = owner.record['operation_id']
    check = copy.deepcopy(inputs['check'])
    await owner.registry._await_update(asyncio.to_thread(copy_job, inputs['candidate'], session.environment, check,
        reference={'path':'jobs/'+digest(check['source_job_id'])+'.json','sha256':inputs['references']['job']['sha256']}))
    owner.emit(phase='authentication_verification')
    await session.authenticate()
    account = session.authentication._account('workbuddy')
    if account['revision'] != backend.old_account['revision'] or account.get('confirmed_binding') != backend.old_account.get('confirmed_binding'):
        raise failure('sdk_update_binding_changed', 'authentication_verification')
    if not target_model_origin(session.environment, check, backend.old_state['default_model_id']):
        raise failure('workbuddy_update_evidence_required', 'recovery_verification')
    owner.emit(phase='infrastructure_validation')
    report = await owner.registry._await_update(asyncio.to_thread(infrastructure, owner.registry, owner.record, owner.emit))
    owner.check_binding()
    if session.authentication.view('workbuddy').get('authentication_context_status') != 'current':
        owner.emit(phase='authentication_verification')
        await session.authenticate()
    owner.emit(phase='target_audit')
    cert = await asyncio.to_thread(certificate, session, backend.old_certificate, report,
                                  operations=backend.old_operations, supports=backend.old_certificates)
    session.environment.mutate(lambda s:s.setdefault('sdk_integration_records',{}).__setitem__(cert['id'],cert))
    snapshot = session.authentication.bind('workbuddy', session.manager.runtime_snapshot(require_enabled=False))
    audit = TargetAdmissionAudit(session.authentication, cert, check, snapshot)
    audit(owner.registry.workbuddy.environment.release(receipt['environment_digest']), backend.old_state)
    owner.emit(phase='evidence_publication')
    await owner.registry._await_update(asyncio.to_thread(copy_evidence, session.environment,
        owner.registry.workbuddy.environment, cert, check))
    return audit


async def run(owner):
    from .workbuddy_online_update import UpdateBackend
    from .workbuddy_update_switch import switch
    backend = guard = None
    try:
        async with asyncio.timeout(max(0, owner.record['deadline_at']-time.time())):
            registry = owner.registry
            guard = registry.authentication_service.sdk_maintenance('workbuddy')
            await guard.__aenter__()
            owner.held_guard = guard
            owner.check_binding()
            if owner.record.get('retry_reference'):
                ref=owner.record['retry_reference']
                if file_hash(Path(ref['path']))!=ref['sha256']:
                    raise failure('sdk_update_binding_changed','recovery_verification')
            fresh = await registry._await_update(asyncio.to_thread(load_parent, registry, owner.record['parent_operation_id'], owner.record['target_version']))
            if fresh['references']['parent']['sha256'] != owner.record['parent_digest']:
                raise failure('sdk_update_binding_changed', 'recovery_verification')
            owner.emit(input_digest=fresh['input_digest'],input_references=fresh['references'])
            owner.check_binding()
            backend = UpdateBackend(registry, owner.record)
            await asyncio.to_thread(backend.preflight)
            audit = await verify_reused(owner, backend, fresh)
            owner.check_binding()
            await switch(owner, backend, fresh['receipt'], audit)
    except BaseException as exc:
        phase = getattr(exc,'update_failure_phase',owner.record['phase'])
        clean = False
        try:
            clean = (backend is None or backend.cleanup_complete()) and owner.record.get('cleanup_complete') is not False
        except Exception:
            pass
        status = ('rolled_back' if owner.record.get('status') == 'rolled_back' else
                  'cleanup_pending' if not clean else 'needs_review' if backend and not backend.switch_safe
                  or isinstance(exc,(asyncio.CancelledError,TimeoutError)) else 'failed')
        code = getattr(exc,'code','sdk_update_deadline_exceeded' if isinstance(exc,TimeoutError) else
                       'sdk_update_interrupted' if isinstance(exc,asyncio.CancelledError) else 'sdk_update_failed')
        owner.emit(status=status, cleanup_complete=clean, error={'code':code,'phase':phase})
    finally:
        if backend:
            try:
                await backend.close()
            except BaseException:
                owner.emit(status='cleanup_pending',cleanup_complete=False,
                           error={'code':'runtime_cleanup_incomplete','phase':'cleanup'})
        if owner.held_guard is not None and owner.record.get('cleanup_complete') is True and (backend is None or backend.switch_safe):
            await guard.__aexit__(None,None,None)
            owner.held_guard = None
