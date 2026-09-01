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
    "__import__", "vars",
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
payload = json.load(sys.stdin)
result = module.evaluate(payload)
if not isinstance(result, dict):
    raise TypeError("managed rule evaluate() must return an object")
encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
if len(encoded.encode("utf-8")) > 2_000_000:
    raise ValueError("managed rule output exceeds 2 MB")
sys.stdout.write(encoded)
"""


def execute_managed_rule(
    source: str,
    inputs: dict[str, Any],
    *,
    expected_digest: str,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    validate_managed_rule(source, expected_digest=expected_digest)
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
        completed = subprocess.run(
            [sys.executable, "-I", "-c", _RUNNER, str(temporary_path)],
            input=json.dumps(inputs, ensure_ascii=False, separators=(",", ":")),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=max(0.1, timeout_seconds),
            env=environment,
            check=False,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "unknown error"
            raise ManagedRuleError(
                f"Managed rule execution failed: {message}", code="managed_rule_execution_failed"
            )
        try:
            output = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ManagedRuleError(
                "Managed rule returned invalid JSON.", code="managed_rule_output_invalid"
            ) from exc
        if not isinstance(output, dict):
            raise ManagedRuleError(
                "Managed rule output must be an object.", code="managed_rule_output_invalid"
            )
        return output
    except subprocess.TimeoutExpired as exc:
        raise ManagedRuleError(
            "Managed rule exceeded its 10 second time limit.", code="managed_rule_timeout"
        ) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
