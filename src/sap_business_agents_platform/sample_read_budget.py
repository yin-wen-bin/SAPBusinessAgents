"""Task-local accounting at the actual embedded OData GET boundary."""
from contextlib import contextmanager
from contextvars import ContextVar

_active = ContextVar("sample_odata_budget", default=None)


@contextmanager
def bind(context):
    token = _active.set({"context": context, "reserved": True})
    try:
        yield
    finally:
        _active.reset(token)


def before_get():
    value = _active.get()
    if value is None:
        return
    from .sample_discovery import SampleDiscoveryError
    context = value["context"]
    if context.closed or not context.remaining(external=True):
        raise SampleDiscoveryError("sample_finalization_only")
    if value["reserved"]:
        value["reserved"] = False
    elif context.read_count >= context.max_reads:
        raise SampleDiscoveryError("sample_read_budget_exceeded")
    else:
        context.read_count += 1
    context.business_get_count += 1
