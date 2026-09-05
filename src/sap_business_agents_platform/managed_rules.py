from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


class ManagedRuleError(RuntimeError):
    def __init__(self, message: str, *, code: str = "managed_rule_invalid") -> None:
        super().__init__(message)
        self.code = code


ALLOWED_IMPORTS = {"calendar", "datetime", "decimal", "math", "re", "statistics", "typing"}
FORBIDDEN_CALLS = {
    "breakpoint", "compile", "delattr", "dir", "eval", "exec", "getattr", "globals",
    "hasattr", "help", "input", "locals", "open", "setattr", "super", "type",
    "__import__", "print", "vars",
}
FORBIDDEN_NODES = (
    ast.AsyncFunctionDef,
    ast.Await,
    ast.ClassDef,
    ast.Delete,
    ast.Global,
    ast.Nonlocal,
    ast.Raise,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    ast.Yield,
    ast.YieldFrom,
)


def source_digest(source: str) -> str:
    return "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest()


def validate_managed_rule(source: str, *, expected_digest: str | None = None) -> dict[str, Any]:
    if not source.strip():
        raise ManagedRuleError("Managed rule source is empty.")
    if len(source.encode("utf-8")) > 500_000:
        raise ManagedRuleError("Managed rule source exceeds 500 KB.")
    digest = source_digest(source)
    if expected_digest and digest != expected_digest:
        raise ManagedRuleError("Managed rule source digest does not match the manifest.")
    try:
        tree = ast.parse(source, filename="rules.py", mode="exec")
    except SyntaxError as exc:
        raise ManagedRuleError(f"Managed rule has invalid Python syntax: {exc.msg}.") from exc
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    if "evaluate" not in functions:
        raise ManagedRuleError("Managed rule must define evaluate(inputs).")
    for node in ast.walk(tree):
        if isinstance(node, FORBIDDEN_NODES):
            raise ManagedRuleError(f"Managed rule contains forbidden syntax: {type(node).__name__}.")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] not in ALLOWED_IMPORTS:
                    raise ManagedRuleError(f"Managed rule import is not allowed: {alias.name}.")
        if isinstance(node, ast.ImportFrom):
            module = str(node.module or "").split(".", 1)[0]
            if node.level or module not in ALLOWED_IMPORTS:
                raise ManagedRuleError(f"Managed rule import is not allowed: {node.module}.")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS:
                raise ManagedRuleError(f"Managed rule call is not allowed: {node.func.id}.")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ManagedRuleError("Managed rule cannot access dunder attributes.")
    return {"ok": True, "sha256": digest, "entrypoint": "evaluate"}


_RUNNER = r"""
import importlib.util
import json
import sys

path = sys.argv[1]
spec = importlib.util.spec_from_file_location("sapba_managed_rule", path)
if spec is None or spec.loader is None:
    raise RuntimeError("managed rule module could not be loaded")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
# ``-I`` intentionally ignores PYTHONIOENCODING, so Windows otherwise applies
# the active ANSI code page to stdin/stdout.  Use the binary streams and a
# fixed UTF-8 transport to preserve bilingual rule inputs and reports.
payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
result = module.evaluate(payload)
if not isinstance(result, dict):
    raise TypeError("managed rule evaluate() must return an object")
# The normal rule contract repeats its report in workflow_output. Transmit
# identical reports once, then restore the exact public object in the host.
# This is transport deduplication, not truncation or a higher stdout limit.
workflow = result.get("workflow_output")
reuse_report = (isinstance(workflow, dict) and isinstance(workflow.get("business_report"), dict)
                and result.get("business_report") == workflow["business_report"])
if reuse_report:
    result = {key: value for key, value in result.items() if key != "business_report"}
wire = {"version": 1, "reuse_report": reuse_report, "result": result}
encoded = json.dumps(wire, ensure_ascii=False, separators=(",", ":"))
if len(encoded.encode("utf-8")) > 2_000_000:
    raise ValueError("managed rule output exceeds 2 MB")
sys.stdout.buffer.write(encoded.encode("utf-8"))
"""


class _WindowsRuleJob:
    """Bound a managed-rule process and all descendants to one Windows Job."""

    def __init__(self, process: subprocess.Popen[bytes], memory_bytes: int) -> None:
        self._handle: int | None = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "Could not create managed-rule Job Object")
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = 0x00000100 | 0x00002000
        limits.ProcessMemoryLimit = memory_bytes
        try:
            if not kernel32.SetInformationJobObject(
                handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            ):
                raise OSError(
                    ctypes.get_last_error(), "Could not configure managed-rule Job Object"
                )
            if not kernel32.AssignProcessToJobObject(handle, process._handle):
                raise OSError(
                    ctypes.get_last_error(), "Could not isolate managed-rule process"
                )
        except Exception:
            kernel32.CloseHandle(handle)
            raise
        self._handle = int(handle)
        self._close_handle = kernel32.CloseHandle

    def close(self) -> None:
        if self._handle is not None:
            self._close_handle(self._handle)
            self._handle = None


def execute_managed_rule(
    source: str,
    inputs: dict[str, Any],
    *,
    expected_digest: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    validate_managed_rule(source, expected_digest=expected_digest)
    encoded_input = json.dumps(inputs, ensure_ascii=False, separators=(",", ":"))
    if len(encoded_input.encode("utf-8")) > 50 * 1024 * 1024:
        raise ManagedRuleError(
            "Managed rule input exceeds 50 MB.", code="managed_rule_input_too_large"
        )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", encoding="utf-8", delete=False
        ) as handle:
            handle.write(source)
            temporary_path = Path(handle.name)
        environment = {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "WINDIR": os.environ.get("WINDIR", ""),
        }
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", _RUNNER, str(temporary_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        try:
            job = _WindowsRuleJob(process, 512 * 1024 * 1024)
        except Exception as exc:
            process.kill()
            process.wait()
            raise ManagedRuleError(
                "Managed rule resource isolation could not be established.",
                code="managed_rule_isolation_failed",
            ) from exc
        try:
            stdout, stderr = process.communicate(
                input=encoded_input.encode("utf-8"), timeout=max(0.1, timeout_seconds)
            )
        except subprocess.TimeoutExpired as exc:
            # Closing a kill-on-close Job terminates the full child process tree.
            job.close()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise ManagedRuleError(
                "Managed rule exceeded its 30 second time limit.",
                code="managed_rule_timeout",
            ) from exc
        finally:
            job.close()
        if process.returncode != 0:
            # Child exceptions may interpolate evidence values (for example a
            # KeyError). Never forward arbitrary stderr into a public run.
            message = ("Managed rule output exceeds 2 MB." if stderr.strip().endswith(
                b"ValueError: managed rule output exceeds 2 MB") else "Managed rule execution failed.")
            raise ManagedRuleError(
                message, code="managed_rule_execution_failed"
            )
        try:
            wire = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ManagedRuleError(
                "Managed rule returned invalid JSON.", code="managed_rule_output_invalid"
            ) from exc
        if (not isinstance(wire, dict) or set(wire) != {"version", "reuse_report", "result"}
                or wire["version"] != 1 or type(wire["reuse_report"]) is not bool
                or not isinstance(wire["result"], dict)):
            raise ManagedRuleError(
                "Managed rule transport contract is invalid.", code="managed_rule_output_invalid"
            )
        output = wire["result"]
        if wire["reuse_report"]:
            workflow = output.get("workflow_output")
            if ("business_report" in output or not isinstance(workflow, dict)
                    or not isinstance(workflow.get("business_report"), dict)):
                raise ManagedRuleError("Managed rule report reference is invalid.", code="managed_rule_output_invalid")
            output["business_report"] = workflow["business_report"]
        return output
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
