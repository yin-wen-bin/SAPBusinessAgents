"""Explicit selection for future authoring only; never restores task authority."""
from .acceptance import canonical_hash

FIELDS = ('provider_id','sdk_id','version','cli_version','model','actual_model','reasoning_effort',
          'environment_digest','authentication_revision','authentication_binding_revision',
          'service_site','service_site_revision','service_site_digest','model_check_digest','sdk_integration_id')

def selection_digest(snapshot):
    return canonical_hash({'contract':'sapba.authoring-selection/1',
        'binding':{key:snapshot.get(key) for key in FIELDS}}).removeprefix('sha256:')

def explicit_selection(runtime, expected):
    # snapshot() retains the Provider's existing authentication, SDK, model and
    # installation gates. No login, probing, fallback model or SDK call here.
    try:
        snapshot = runtime.snapshot()
    except Exception as exc:
        from .agent_lifecycle import AgentLifecycleError
        code=str(getattr(exc,'code','agent_runtime_binding_missing'))
        if not code.startswith(('runtime_','workbuddy_','agent_')):code='agent_runtime_binding_missing'
        raise AgentLifecycleError('The current default binding did not pass admission.',code=code) from exc
    if not isinstance(snapshot,dict) or not snapshot.get('provider_id') or not snapshot.get('model'):
        from .agent_lifecycle import AgentLifecycleError
        raise AgentLifecycleError('The current default binding is unavailable.',code='agent_runtime_binding_missing')
    if selection_digest(snapshot) != expected:
        from .agent_lifecycle import AgentLifecycleError
        raise AgentLifecycleError('The default binding changed; review it again.',code='agent_authoring_runtime_changed')
    return snapshot
