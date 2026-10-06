"""Legacy public error vocabulary; all contract algorithms remain SDK-free."""
from functools import wraps
from .runtime_contract import RuntimeContractError
from .workbuddy_environment import WorkBuddyError


def translate(exc):
    error = WorkBuddyError(exc.code.replace("runtime_", "workbuddy_", 1))
    error.detail = {**exc.detail, "validation_issues": [
        {**issue, "code": str(issue.get("code", exc.code)).replace("runtime_", "workbuddy_", 1)}
        for issue in exc.detail.get("validation_issues", [])]}
    return error


def legacy(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except RuntimeContractError as exc:
            raise translate(exc) from None
    return call
