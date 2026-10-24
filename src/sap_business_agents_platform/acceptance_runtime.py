"""Default selection for a NEW campaign; never rebind an accepted campaign."""
from .acceptance import canonical_hash
from .models import AgentAcceptanceCampaignRequest as LegacyAcceptanceCampaignRequest
from pydantic import Field


class AgentAcceptanceCampaignRequest(LegacyAcceptanceCampaignRequest):
    """Additive HTTP admission field, not a model/business execution schema."""
    expected_runtime_digest: str | None = Field(default=None, alias="expectedRuntimeDigest", pattern=r"^[0-9a-f]{64}$")


def resolve_acceptance_runtime(manager, model=None):
    from .agent_lifecycle import AgentLifecycleError

    selected = getattr(manager, "default_provider_id", None)
    if selected != "workbuddy":
        # Codex retains its existing selection, authentication and admission path.
        runtime = manager.runtime_snapshot()
        if model:
            raise AgentLifecycleError("runtimeModelId is only supported for WorkBuddy acceptance.",
                                      code="agent_acceptance_runtime_model_invalid")
        return runtime

    from .workbuddy_inspection import inspect
    # One synchronous, owner-bound inspection: repeated auth/site projections
    # share reads, and the existing content/consistency fence still runs on exit.
    # This scope never survives an await, queue wait or dispatch.
    with inspect(manager.workbuddy.environment):
        return _workbuddy_formal_snapshot(manager, model, selected)


def _workbuddy_formal_snapshot(manager, model, selected):
    from .agent_lifecycle import AgentLifecycleError

    revision = manager.configuration_revision
    authentication = manager.authentication_service
    if authentication is None:
        raise AgentLifecycleError("The runtime authentication binding is incomplete.",
                                  code="runtime_authentication_binding_invalid")
    # Capture the unified account/site binding BEFORE slow provider preparation.
    # Do not hold the authentication lock across disk checks or provider locks.
    initial = authentication.bind(selected, {})
    binding = initial.get("authentication_binding_revision")
    if not isinstance(binding, int) or isinstance(binding, bool) or binding < 0:
        raise AgentLifecycleError("The runtime authentication binding is incomplete.",
                                  code="runtime_authentication_binding_invalid")
    authentication.assert_binding(selected, initial)
    # None deliberately means the saved model, not a model-directory fallback.
    runtime = manager.workbuddy.runtime_snapshot(model, formal=True)
    if runtime.get("provider_id") != selected:
        raise AgentLifecycleError("The runtime provider binding is incomplete.",
                                  code="runtime_authentication_binding_invalid")
    with authentication.lock:
        if manager.default_provider_id != selected or manager.configuration_revision != revision:
            raise AgentLifecycleError("The default runtime changed during preparation.",
                                      code="agent_acceptance_runtime_changed")
        authentication.assert_binding(selected, initial)
        # Validate the provider's site against the captured/current context BEFORE
        # bind() merges that context. Provider auth revision is NOT this revision.
        authentication.assert_binding(selected, {
            **runtime, "authentication_binding_revision": initial["authentication_binding_revision"],
        })
        runtime = authentication.bind(selected, runtime)
        binding = runtime.get("authentication_binding_revision")
        if not isinstance(binding, int) or isinstance(binding, bool) or binding < 0:
            raise AgentLifecycleError("The runtime authentication binding is incomplete.",
                                      code="runtime_authentication_binding_invalid")
        authentication.assert_binding(selected, runtime)
    return runtime


def acceptance_runtime_digest(runtime):
    # selected_at is an observation, not a change to the selection/authorization.
    return canonical_hash({key: value for key, value in runtime.items() if key != "selected_at"}).removeprefix("sha256:")
