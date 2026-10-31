"""Task-local cleanup facts, never Runtime qualification or business evidence."""
import hashlib
import json


def binding_digest(record):
    value = record.runtime.model_dump(mode='json') if record.runtime else None
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def record_cleanup_confirmation(store, run_id, provider_id, cleanup_state):
    record = store.get_run(run_id)
    provider = record.runtime.provider_id if record.runtime else 'codex'
    if cleanup_state.get('complete') is not True or provider_id != provider:
        return False
    state = store.get_harness_state(run_id)
    store.update_harness_state(run_id, {'cleanup_confirmation': {
        'run_id': run_id, 'provider_id': provider_id, 'binding_digest': binding_digest(record),
        'active_turn_id': state.get('active_turn_id'), 'complete': True,
    }})
    return True


def cleanup_confirmed(record, state):
    proof = state.get('cleanup_confirmation') or {}
    return bool(proof.get('complete') is True and proof.get('run_id') == record.run_id
                and proof.get('provider_id') == (record.runtime.provider_id if record.runtime else 'codex')
                and proof.get('binding_digest') == binding_digest(record)
                and proof.get('active_turn_id') == state.get('active_turn_id'))
