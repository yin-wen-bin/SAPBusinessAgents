"""Offline worker protocol fixture. No SDK, authentication, network or SAP."""
import json
import os
import subprocess
import sys
import time

request = json.loads(sys.stdin.readline())
binding = {key: request[key] for key in ("protocol", "task_id", "attempt_id", "operation", "environment_digest", "runtime_digest", "deadline")}
scenario = request["payload"].get("scenario", "success")
def emit(kind, **data):
    print(json.dumps({"kind": kind, "task_id": request["task_id"], "attempt_id": request["attempt_id"], **data}), flush=True)
if scenario == "bad_handshake":
    binding["environment_digest"] = "0" * 64
print(json.dumps({"kind": "hello", **binding}), flush=True)
if scenario == "child":
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(300)"], creationflags=0x08000000 if os.name == "nt" else 0)
    emit("event", event="child_started", data={"pid": child.pid})
    time.sleep(300)
elif scenario == "hang":
    time.sleep(300)
elif scenario == "error_close_hang":
    emit("error", code="workbuddy_upstream_empty_stream", diagnostic={
        "failure_code": "workbuddy_upstream_empty_stream", "failure_source": "error_message",
        "message_count": 4, "terminal_result_received": False,
        "raw_error": "password=private-value"})
    time.sleep(300)
elif scenario == "large_frame":
    print("x" * (1024 * 1024 + 1), flush=True)
elif scenario == "large_output":
    for _ in range(10):
        emit("event", event="progress", data={"padding": "x" * 900000})
elif scenario == "tool":
    emit("tool_call", tool="approved_fixture", arguments={"value": 1}, call_id="one")
    response = json.loads(sys.stdin.readline())
    emit("result", result={"text": json.dumps(response["result"]), "actual_model": "fixture-model"})
elif scenario == "bad_attempt":
    print(json.dumps({"kind": "result", "task_id": request["task_id"], "attempt_id": "foreign", "result": {}}), flush=True)
elif scenario.startswith("schema_check"):
    emit("schema_check", call_id="c" * 32, phase="not_a_phase" if scenario == "schema_check_bad_phase" else "native_candidate",
         value={"answer": "private-business-value"},
         schema={"type": "object"})  # A worker cannot replace the frozen schema.
    response = json.loads(sys.stdin.readline())
    emit("result", result={"text": json.dumps(response), "actual_model": "fixture-model"})
else:
    if scenario == "delayed":
        time.sleep(0.2)
    emit("result", result={"text": "ok", "actual_model": "fixture-model",
                           "model_identity_source": "assistant_message", "env": sorted(os.environ)})
