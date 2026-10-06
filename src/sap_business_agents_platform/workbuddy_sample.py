"""WorkBuddy sample loop reuses the exact platform sample scope/evidence checks."""
import json
import time

from .sample_discovery import SampleDiscoveryContext, sample_output_schema, SAMPLE_SECONDS, SAMPLE_QUERY_SECONDS
from .mcp_server import _SAP_TOOLS


def sample_prompt(context, snapshot):
    # Business instructions come from the same validated sample context.
    # Only the SDK model identity/plan wire encoding differs.
    from .runtime_query_contract import PLAN_HELP
    return (SampleDiscoveryContext.prompt(context, runtime_model=snapshot["model"]) + "\n" + PLAN_HELP
        + "\nFrozen runtime binding: " + json.dumps({"model": snapshot["model"], "revision": context.revision}))

async def discover(service, manager, run_id, manifest, supplied, *, revision, started, selected_fields):
    """Compatibility entry; all lifecycle and evidence checks are shared."""
    from .runtime_sample import discover as shared_discover
    from .workbuddy_driver import WorkBuddySampleDriver
    binding = manager.bound_snapshot(service.store.get_run(run_id).runtime.model_dump(mode="json"))
    manager.supervisor.check_operation(binding, "sample_discovery")
    return await shared_discover(service, run_id, manifest, supplied, revision=revision,
        model=binding["model"], started=started, selected_fields=selected_fields,
        driver=WorkBuddySampleDriver(manager), binding=binding)
