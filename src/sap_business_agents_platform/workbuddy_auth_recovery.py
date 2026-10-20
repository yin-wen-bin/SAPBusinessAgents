"""Explicit same-account recovery receipts; no login, model calls or PASS writes.

Only audited original positive identity + a fresh official positive identity may
bridge a mistaken negative-check revision. Queued/in-flight snapshots remain
strict; new authoring requests explicitly resolve the applicable receipt.
"""
import json
from .workbuddy_environment import digest, WorkBuddyError

CONTRACT = 'sapba.workbuddy.authentication-recovery/1'


def _document(environment, reference):
    from .workbuddy_integration import _artifact_bytes
    return json.loads(_artifact_bytes(environment, reference))


def verified_model_check_origin(environment, check, model):
    """The /3 protocol deliberately excludes host management digests.

    Validate the immutable native-probe job and every account/site/environment
    binding, instead of applying the obsolete pre-/3 management-field check.
    Legacy checks without this native provenance cannot be recovered here.
    """
    from .workbuddy_model_protocol import CONTRACT as PROBE, applicable
    from .workbuddy_record_io import read_record
    from .workbuddy_site import matches, configuration
    try:
        if check.get('probe_contract') != PROBE or check.get('model_id') != model or not applicable(environment, check):
            return False
        job=read_record(environment.root/'jobs'/(digest(check['source_job_id'])+'.json'))
        return (digest(job)==check['source_job_digest']
            and job.get('environment_digest')==check.get('environment_digest')
            and job.get('authentication_revision',0)==check.get('authentication_revision',0)
            and matches(job,configuration(check)))
    except (KeyError,OSError,ValueError,TypeError,WorkBuddyError):
        return False


def applicable_receipt(environment, state, *, model, source_revision, check_digest):
    from .workbuddy_site import matches, configuration
    from .workbuddy_model_protocol import applicable
    if not state.get('authenticated') or not state.get('authentication_identity_digest'):
        return None
    for identifier, receipt in state.get('authentication_recoveries', {}).items():
        try:
            if (receipt.get('contract') != CONTRACT or receipt.get('id') != identifier
                    or digest({k:v for k,v in receipt.items() if k!='id'}) != identifier
                    or receipt.get('source_revision') != source_revision
                    or receipt.get('target_revision') != state.get('authentication_revision')
                    or receipt.get('environment_digest') != state.get('environment_digest')
                    or receipt.get('identity_digest') != state['authentication_identity_digest']
                    or not matches(receipt, configuration(state))):
                continue
            original=_document(environment, receipt['original_identity'])
            current=_document(environment, receipt['current_identity'])
            if (original.get('authenticated') is not True or current.get('authenticated') is not True
                    or current.get('cleanup_complete') is not True
                    or original.get('identity_digest') != receipt['identity_digest']
                    or current.get('identity_digest') != receipt['identity_digest']):
                continue
            check=_document(environment, receipt['model_checks'][model])
            if (check.get('model_id') != model or check.get('check_digest') != check_digest
                    or check.get('authentication_revision',0) != source_revision
                    or check.get('environment_digest') != state['environment_digest']
                    or check.get('compatible') is not True or not matches(check, configuration(state))
                    or not applicable(environment, check) or not verified_model_check_origin(environment,check,model)):
                continue
            return receipt
        except (KeyError, OSError, ValueError, TypeError, WorkBuddyError):
            continue
    return None


def model_check_matches(environment, state, check):
    receipt=applicable_receipt(environment,state,model=check.get('model_id'),
        source_revision=check.get('authentication_revision',0),check_digest=check.get('check_digest'))
    if receipt is None:
        return False
    return check == _document(environment, receipt['model_checks'][check['model_id']])


def historical_model_check(environment, state, model):
    for check in reversed(state.get('model_check_history', [])):
        if isinstance(check,dict) and check.get('model_id')==model and model_check_matches(environment,state,check):
            return check  # Original probe contract/digest/job remain unmodified.
    return None
