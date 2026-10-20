"""Lightweight WorkBuddy compatibility protocol; no SAP or native tools."""
import json
import secrets
from .workbuddy_environment import WorkBuddyError, digest
from .workbuddy_integration import protocol_digest
from .workbuddy_record_io import read_record

CONTRACT = "sapba.workbuddy.model-check/3"
SECONDS = 90
_MISSING = object()
TOOL = "compatibility_echo"
TOOL_SCHEMA = {"type": "object", "properties": {"challenge": {"type": "string"}},
               "required": ["challenge"], "additionalProperties": False}
TOOLS = [{"name": TOOL, "description": "Platform memory-only compatibility echo; no external I/O.",
          "inputSchema": TOOL_SCHEMA}]
SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "status": {"type": "string", "enum": ["ok"]},
    "challenge": {"type": "string"}, "receipt": {"type": "string"},
    "optional": {"type": "null", "enum": [None],
                 "description": "Use the unquoted JSON literal null. Never a string such as None, null or an empty string."},
    "count": {"type": "integer", "enum": [1]},
    "nested": {"type": "object", "additionalProperties": False,
               "properties": {"kind": {"type": "string", "enum": ["echo"]}}, "required": ["kind"]}},
    "required": ["status", "challenge", "receipt", "optional", "count", "nested"]}


def current(check):
    # Legacy checks remain legacy: never fabricate probe provenance/version.
    return check.get("probe_contract") != CONTRACT or check.get("model_protocol_digest") == protocol_digest()


def applicable(environment, check):
    if not current(check):
        return False
    if check.get("probe_contract") != CONTRACT:
        return True  # Existing check is read under its existing contract only.
    try:
        job = read_record(environment.root / "jobs" / (digest(check["source_job_id"]) + ".json"))
        metadata = job.get("result_metadata", {})
        return (check.get("check_digest") == digest({k: v for k, v in check.items() if k != "check_digest"})
            and check.get("proof", {}).get("tool_calls") == 1
            and check.get("schema_sha256") == digest(SCHEMA)
            and digest(job) == check.get("source_job_digest")
            and job.get("status") == "completed" and job.get("cleanup_complete") is True
            and job.get("operation") == "model_check" and job.get("runtime_model") == check.get("model_id")
            and metadata.get("structured_terminal") is True
            and metadata.get("output_sha256") == check.get("output_sha256")
            and metadata.get("actual_model") == check.get("actual_model")
            and metadata.get("identity_source") == check.get("model_identity_source"))
    except (KeyError, OSError, ValueError, TypeError):
        return False


def probe_scope(operation, payload, tool_handler):
    return (operation == "model_check" and payload.get("tools") == TOOLS
            and isinstance(getattr(tool_handler, "__self__", None), Challenge)
            and getattr(tool_handler, "__func__", None) is Challenge.handle
            and payload == tool_handler.__self__.payload(payload.get("cwd")))


class Challenge:
    def __init__(self):
        self.challenge = secrets.token_hex(16)
        self.receipt = secrets.token_hex(16)  # Not in the prompt: must actually call the tool.
        self.calls = 0
        self.attempts = []

    async def handle(self, name, arguments):
        self.attempts.append({"index": len(self.attempts) + 1, "tool_matches": name == TOOL,
            "arguments_match": arguments == {"challenge": self.challenge},
            "challenge_matches": isinstance(arguments, dict) and arguments.get("challenge") == self.challenge,
            "duplicate": self.calls != 0})
        if name != TOOL or arguments != {"challenge": self.challenge} or self.calls:
            raise WorkBuddyError("workbuddy_model_probe_tool_invalid")
        self.calls += 1
        return {"challenge": self.challenge, "receipt": self.receipt}

    def prompt(self):
        example = {"status": "ok", "challenge": self.challenge,
                   "receipt": "<copy receipt from compatibility_echo>",
                   "optional": None, "count": 1, "nested": {"kind": "echo"}}
        return (f"Call compatibility_echo exactly once with challenge={self.challenge}. "
                "Then submit the complete object through native StructuredOutput. Replace only the receipt "
                "placeholder below with the actual receipt returned by the tool. "
                "Object template: " + json.dumps(example) + "\n"
                'Include "optional": null using literal JSON null, never the string "null", '
                'the Python value None, the string "None", an empty string or an omitted field. '
                "If the formatter rejects the object, correct its types to match this JSON template; "
                "do not repeat the rejected object. "
                "No other tools, commands, file access or external services.")

    def payload(self, cwd):
        return {"prompt": self.prompt(), "system_prompt": "Platform compatibility only. Use only the supplied memory echo and native structured output. "
                    "Use JSON syntax: the null value is the unquoted token null, never Python None or a string.",
                "cwd": cwd, "tools": TOOLS, "output_schema": SCHEMA, "timeout_ms": SECONDS * 1000}

    def verify(self, value):
        expected = {"status": "ok", "challenge": self.challenge, "receipt": self.receipt,
                    "optional": None, "count": 1, "nested": {"kind": "echo"}}
        if self.calls != 1 or value != expected:
            raise WorkBuddyError("workbuddy_model_probe_echo_invalid")
        return {"tool_calls": self.calls, "challenge_digest": digest(self.challenge),
                "receipt_digest": digest(self.receipt)}

    def diagnostics(self, value=_MISSING):
        """Compare fixed probe fields without retaining prompt/tool/output text."""
        def summary(item):
            if item is _MISSING:
                return {"present": False}
            kind = ("null" if item is None else "boolean" if isinstance(item, bool)
                    else "object" if isinstance(item, dict) else "array" if isinstance(item, list)
                    else "string" if isinstance(item, str) else "number")
            result = {"present": True, "type": kind, "sha256": digest(item)}
            if isinstance(item, (str, list, dict)):
                result["length"] = len(item)
            return result

        expected = {"status": "ok", "challenge": self.challenge, "receipt": self.receipt,
                    "optional": None, "count": 1, "nested": {"kind": "echo"}}
        fields = []
        def compare(wanted, actual, path=""):
            if isinstance(wanted, dict):
                if not isinstance(actual, dict) or set(actual) - set(wanted):
                    fields.append({"path": path or "/", "matches": False,
                                   "expected": summary(wanted), "actual": summary(actual)})
                for key, item in wanted.items():
                    compare(item, actual.get(key, _MISSING) if isinstance(actual, dict) else _MISSING,
                            path + "/" + key)
            else:
                field = {"path": path, "matches": actual is not _MISSING and actual == wanted,
                         "expected": summary(wanted), "actual": summary(actual)}
                if path in {"/challenge", "/receipt"}:
                    field.update(matches_challenge=actual == self.challenge, matches_receipt=actual == self.receipt)
                fields.append(field)
        if value is not _MISSING:
            compare(expected, value)
        return {"contract": "sapba.workbuddy.model-check-diagnostics/1",
                "tool_calls": {"expected": 1, "attempted": len(self.attempts), "successful": self.calls,
                               "matches": self.calls == 1 and len(self.attempts) == 1, "attempts": list(self.attempts)},
                "output_received": value is not _MISSING,
                "output_matches": value is not _MISSING and value == expected,
                "mismatched_fields": [field["path"] for field in fields if not field["matches"]],
                "fields": fields}
