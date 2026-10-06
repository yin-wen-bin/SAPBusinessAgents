"""Legacy WorkBuddy error/event encoding; checks come from the shared contract."""
from .runtime_diagnostics import output_diagnostic, schema_issues as _schema_issues, checked_output as _checked_output
from .workbuddy_compat import legacy
import re

def schema_issues(raw, schema):
    return [{**issue, "code": issue["code"].replace("runtime_", "workbuddy_", 1)}
            for issue in _schema_issues(raw, schema)]

checked_output = legacy(_checked_output)

_EVENTS = {
    "agent_runtime_tool_started", "agent_runtime_tool_finished", "agent_runtime_result_received",
    "agent_runtime_sdk_closing", "agent_runtime_sdk_closed",
}
_TOOLS = {"Bash", "Read", "Write", "Edit", "Glob", "Grep", "MultiEdit", "TodoWrite", "StructuredOutput", "unrecognized"}


def safe_progress(kind: str, data: dict) -> dict:
    if kind not in _EVENTS or data.get("provider_id") != "workbuddy":
        return {}
    public = {}
    name = data.get("tool")
    if isinstance(name, str) and name in _TOOLS:
        public["tool"] = name
    elif isinstance(name, str) and re.fullmatch(r"mcp__sapba__[a-z][a-z0-9_]{0,60}", name):
        public["tool"] = name
    for field in ("call_index", "elapsed_ms", "pending_tool_count"):
        value = data.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 3_600_000:
            public[field] = value
    for field in ("matched_call", "is_error", "platform_tool", "terminal_result_received"):
        value = data.get(field)
        if isinstance(value, bool) or field == "is_error" and value is None and field in data:
            public[field] = value
    return public
