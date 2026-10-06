"""SDK-free model identity rules; a usable route is not a concrete identity."""
import re


ROUTING_ALIASES = frozenset({
    "auto", "default", "workbuddy", "codebuddy", "default-model", "fast-model",
    "balanced-model", "primary-model", "deep-model",
})


def is_concrete_model(value):
    return (isinstance(value, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) is not None
            and value.lower() not in ROUTING_ALIASES)


def checked_identity(check, *, flag="identity_known"):
    # Old stored flags cannot certify an alias, or an identity whose origin was
    # never recorded. Project the effective result without rewriting history.
    return (check.get(flag) is True and is_concrete_model(check.get("actual_model"))
            and check.get("model_identity_source") == "assistant_message")
