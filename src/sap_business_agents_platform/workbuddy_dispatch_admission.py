"""Host-only disk gates. Wire encoding and model/SAP contracts live elsewhere."""
import asyncio
import time


async def prepare(supervisor, *, snapshot, operation, mode, probe, validating, validation_scope, task_id, deadline):
    from .workbuddy_environment import WorkBuddyError
    from .workbuddy_site import assert_binding
    from .workbuddy_qualification import management_digest, binding as qualification_context, add_record
    from .runtime_policy import OPERATIONS

    def check():
        from .workbuddy_inspection import inspect
        # Maintenance lock must be acquired/released by the same worker thread.
        with supervisor.environment.maintenance():
            with inspect(supervisor.environment):
                current_site = assert_binding(supervisor.environment.state(), snapshot)
                release = (supervisor.environment.release(snapshot.get('environment_digest')) if probe
                    else supervisor.check_operation(snapshot, operation, mode))
                if release.get('service_site_contract') != '1':
                    raise WorkBuddyError('workbuddy_service_site_worker_update_required')
                host_digest = management_digest()
                # Only an explicitly authorized quality run needs business digests.
                # Ordinary admission does not scan 15 operations or past Campaigns.
                bound = qualification_context(snapshot, release, operation, mode) if (validating or validation_scope) and operation in OPERATIONS and snapshot.get('model') else None
                # A cancelled/expired check must never create a new legacy attempt.
                if task_id in supervisor.cancelled:
                    raise WorkBuddyError('workbuddy_cancelled')
                if time.monotonic() >= deadline:
                    raise WorkBuddyError('workbuddy_deadline_exceeded')
            # Complete the read-only fence before an intentional legacy journal
            # write, without releasing maintenance ownership or changing wire.
            if bound and validating and validation_scope is None and not supervisor.environment.state().get('qualification_protocol'):
                supervisor.environment.mutate(lambda state: add_record(state, {'kind':'failure',
                    'binding':bound, 'environment_digest':release['environment_digest'],
                    'job_id':task_id, 'failure_code':'workbuddy_qualification_validation_pending'}))
            return current_site, release, bound, host_digest

    result = await asyncio.to_thread(check)
    if task_id in supervisor.cancelled:
        raise WorkBuddyError('workbuddy_cancelled')
    state = supervisor.environment.state()
    assert_binding(state, snapshot)
    if snapshot.get('authentication_revision') is not None and snapshot['authentication_revision'] != state.get('authentication_revision', 0):
        raise WorkBuddyError('runtime_authentication_changed')
    if time.monotonic() >= deadline:
        raise WorkBuddyError('workbuddy_deadline_exceeded')
    return result
