"""Durable switch anchors and cancellation-safe owned writers.

Timeout stops forward work, never the ownership of an already-started writer.
Recovery observations are read-only; an interrupted switch is never replayed.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path

from .sdk_update import failure
from .startup_control import atomic
from .workbuddy_environment import digest, file_hash


def save_immutable(path, value):
    path = Path(path)
    if path.exists():
        if json.loads(path.read_bytes()) != value:
            raise failure('sdk_update_binding_changed', 'reconciliation')
    else:
        atomic(path, value)
    return {'path': str(path), 'sha256': file_hash(path)}


def load_anchor(root, record):
    reference = record.get('rollback_anchor')
    if not isinstance(reference, dict):
        raise failure('workbuddy_update_anchor_required', 'reconciliation')
    expected = Path(root) / record['operation_id'] / 'rollback-anchor.json'
    path = Path(reference.get('path', '')).resolve()
    if path != expected.resolve() or not path.is_relative_to(Path(root).resolve()) or file_hash(path) != reference.get('sha256'):
        raise failure('workbuddy_update_anchor_invalid', 'reconciliation')
    value = json.loads(path.read_bytes())
    if value.get('operation_id') != record['operation_id'] or value.get('source_environment_digest') != record['source_environment_digest']:
        raise failure('workbuddy_update_anchor_invalid', 'reconciliation')
    return value


def observation(registry, root, record):
    """Classify actual durable state, without modifying or inferring success."""
    try:
        anchor = load_anchor(root, record)
        from .workbuddy_job import process_identity
        writer = record.get('switch_writer') or {}
        pid = writer.get('pid')
        identity = process_identity(pid) if pid else None
        active = bool(writer.get('state') != 'finished' and pid and (identity == 'unknown' or identity == writer.get('identity')))
        state = registry.workbuddy.environment.state()
        key = state.get('environment_digest')
        where = 'source' if key == anchor['source_environment_digest'] else 'target' if key == anchor['target_environment_digest'] else 'unexpected'
        service = getattr(registry, 'authentication_service', None)
        account = service._account('workbuddy') if service else None
        source = anchor['backend']['old_account']
        target = anchor.get('target_account')
        account_state = ('unavailable' if account is None else 'source' if account == source else
                         'target' if target is not None and account == target else 'changed')
        return {'environment': where, 'account': account_state,
                'writer_may_be_alive': active, 'requires_explicit_review': True}
    except Exception as exc:
        return {'environment': 'unconfirmed', 'requires_explicit_review': True,
                'error': getattr(exc, 'code', 'workbuddy_update_anchor_invalid')}


async def settle(awaitable, interrupted):
    """Drain an owned mutation before propagating cancellation to its caller."""
    task = asyncio.ensure_future(awaitable)
    cancelled = False
    notification_error = None
    while True:
        try:
            result = await asyncio.shield(task)
            if notification_error is not None:
                raise notification_error
            return result, cancelled
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            if not cancelled:
                cancelled = True
                try:
                    interrupted()
                except BaseException as exc:
                    notification_error = exc
            if task.done():
                result = task.result()
                if notification_error is not None:
                    raise notification_error
                return result, True


async def switch(owner, backend, receipt, audit):
    """Single writer, durable before effects; rollback only after it has settled."""
    from .workbuddy_job import process_identity
    backend.emit = owner.emit
    anchor = backend.anchor(receipt, audit)
    reference = await asyncio.to_thread(save_immutable,
        owner.root / owner.record['operation_id'] / 'rollback-anchor.json', anchor)
    owner.emit(rollback_anchor=reference, switch_intent=True, phase='switching')
    barrier = None
    cancelled = False
    succeeded = False

    def interrupted():
        owner.emit(status='needs_review', phase='reconciliation', cleanup_complete=False,
                   error={'code': 'sdk_update_outcome_unconfirmed', 'phase': 'reconciliation'})

    async def writer(phase, action):
        nonlocal cancelled
        owner.emit(phase=phase, switch_writer={'pid': os.getpid(), 'identity': process_identity(os.getpid()),
                                             'phase': phase, 'state': 'started'})
        try:
            result, was_cancelled = await settle(asyncio.to_thread(action), interrupted)
            cancelled = cancelled or was_cancelled
            return result
        finally:
            # settle never returns until the thread has finished, including cancellation.
            owner.emit(switch_writer={**owner.record['switch_writer'], 'state': 'finished'})

    try:
        barrier = await writer('switching', backend.enter_switch)
        owner.held_barrier = barrier
        if cancelled:
            raise asyncio.CancelledError
        await writer('environment_commit', lambda: backend.activate(receipt, audit))
        if cancelled:
            raise asyncio.CancelledError
        owner.emit(activated=True, phase='health_check')
        await writer('health_check', lambda: backend.health(receipt, audit))
        if cancelled:
            raise asyncio.CancelledError
        if not backend.cleanup_complete():
            raise failure('runtime_cleanup_incomplete', 'health_check')
        succeeded = True
    except BaseException as exc:
        failure_phase=owner.record.get('phase','switching')
        exc.update_failure_phase=failure_phase
        owner.emit(primary_failure={'code':getattr(exc,'code',type(exc).__name__),'phase':failure_phase})
        if barrier is not None:
            key = owner.registry.workbuddy.environment.state()['environment_digest']
            if key == receipt['environment_digest']:
                owner.emit(phase='rollback')
                _, rollback_cancelled = await settle(backend.rollback(owner.emit), interrupted)
                cancelled = cancelled or rollback_cancelled
                owner.emit(status='rolled_back', phase='rolled_back', outcome='old_environment_restored',
                           cleanup_complete=True, activated=False)
            elif key == anchor['source_environment_digest']:
                # A failed/aborted precommit has never published the candidate account.
                if owner.record.get('checkpoints', {}).get('account_publication') == 'completed':
                    backend.switch_safe = False
                    raise failure('sdk_update_outcome_unconfirmed', 'reconciliation')
                backend.switch_safe = True
                owner.emit(cleanup_complete=backend.cleanup_complete())
            else:
                backend.switch_safe = False
                raise failure('sdk_update_outcome_unconfirmed', 'reconciliation')
        raise
    finally:
        if barrier is not None and backend.switch_safe and backend.cleanup_complete():
            try:
                _, release_cancelled = await settle(asyncio.to_thread(backend.leave_switch, barrier), interrupted)
                owner.held_barrier = None
                if release_cancelled:
                    raise failure('sdk_update_outcome_unconfirmed','reconciliation')
            except BaseException:
                backend.switch_safe = False
                owner.emit(status='cleanup_pending',cleanup_complete=False,
                           error={'code':'runtime_cleanup_incomplete','phase':'cleanup'})
                raise
    if succeeded:
        owner.emit(status='completed',phase='completed',outcome='activated',activated=True,
                   cleanup_complete=True,error=None,
                   observed_versions={'sdk':receipt['sdk_version'],'cli':receipt['cli_version']})


def checkpoint(backend, name, state):
    emit = getattr(backend, 'emit', None)
    if emit is not None:
        values = copy.deepcopy(backend.record.get('checkpoints', {}))
        values[name] = state
        emit(checkpoints=values, phase=name)
