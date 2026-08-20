from __future__ import annotations

import ast
import asyncio
import hashlib
import ipaddress
import json
import operator
import socket
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError


class ToolAdmissionError(RuntimeError):
    def __init__(self, message: str, *, code: str = "tool_not_admitted") -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class ToolCandidate:
    candidate_id: str
    name: str
    source: str
    version: str
    source_hash: str
    admission: str
    reason: str
    operations: list[dict[str, Any]] = field(default_factory=list)
    spec: dict[str, Any] | None = None
    active: bool = False

    def public_dict(self, *, include_operations: bool = True) -> dict[str, Any]:
        result = {
            "candidate_id": self.candidate_id,
            "name": self.name,
            "source": self.source,
            "version": self.version,
            "source_hash": self.source_hash,
            "admission": self.admission,
            "reason": self.reason,
            "active": self.active,
        }
        if include_operations:
            result["operations"] = self.operations
        return result

    def storage_dict(self) -> dict[str, Any]:
        # Operations are the normalized admitted contract. Do not persist the
        # complete untrusted manifest or its examples/descriptions.
        return self.public_dict()

    @classmethod
    def from_storage(cls, value: dict[str, Any]) -> "ToolCandidate":
        return cls(
            candidate_id=str(value.get("candidate_id") or ""),
            name=str(value.get("name") or ""),
            source=str(value.get("source") or ""),
            version=str(value.get("version") or "unknown"),
            source_hash=str(value.get("source_hash") or ""),
            admission=str(value.get("admission") or "discovery_only"),
            reason=str(value.get("reason") or ""),
            operations=[
                item for item in value.get("operations") or [] if isinstance(item, dict)
            ],
            spec=None,
            active=value.get("active") is True,
        )


class ToolAdmissionGateway:
    """Run-scoped discovery and execution for enforceably read-only tools.

    The prototype admits a built-in expression calculator and credential-free
    OpenAPI operations that are HTTPS GET/HEAD only. Unknown MCP servers and
    authenticated or write-capable APIs remain discoverable but cannot execute.
    """

    def __init__(self, *, timeout_seconds: float = 20.0, max_bytes: int = 1_000_000) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self._candidates: dict[str, dict[str, ToolCandidate]] = {}

    async def search(
        self,
        run_id: str,
        *,
        query: str,
        capability: str = "",
        manifest_url: str | None = None,
    ) -> dict[str, Any]:
        candidates = self._candidates.setdefault(run_id, {})
        builtin = ToolCandidate(
            candidate_id="builtin.safe-compute.v1",
            name="Safe expression and JSON compute",
            source="sapbusinessagents_builtin",
            version="1.0",
            source_hash="sha256:" + hashlib.sha256(b"safe-compute-v1").hexdigest(),
            admission="admitted",
            reason="Pure expression evaluation with an AST allowlist and no I/O.",
            operations=[{"operation_id": "evaluate", "method": "LOCAL", "read_only": True}],
        )
        candidates[builtin.candidate_id] = builtin

        discovered: list[ToolCandidate] = [builtin]
        if manifest_url:
            discovered.append(await self._discover_openapi(manifest_url))
            candidates[discovered[-1].candidate_id] = discovered[-1]
        discovered.extend(self._discover_configured_mcp(query, capability))
        discovered.extend(self._discover_local_assets(query, capability))
        for item in discovered:
            candidates.setdefault(item.candidate_id, item)

        needle = f"{query} {capability}".casefold().strip()
        if needle:
            tokens = [part for part in needle.replace("/", " ").split() if part]
            selected = [
                item
                for item in discovered
                if not tokens
                or any(
                    token in json.dumps(item.public_dict(), ensure_ascii=False).casefold()
                    for token in tokens
                )
            ]
            if not selected and manifest_url:
                selected = discovered
        else:
            selected = discovered
        return {
            "ok": True,
            "source_type": "external_tool",
            "claim_scope": "diagnostic",
            "candidates": [item.public_dict(include_operations=False) for item in selected[:50]],
        }

    def inspect(self, run_id: str, candidate_id: str) -> dict[str, Any]:
        candidate = self._get(run_id, candidate_id)
        return {"ok": True, "candidate": candidate.public_dict()}

    def activate(self, run_id: str, candidate_id: str) -> dict[str, Any]:
        candidate = self._get(run_id, candidate_id)
        if candidate.admission != "admitted":
            raise ToolAdmissionError(candidate.reason)
        candidate.active = True
        return {"ok": True, "candidate": candidate.public_dict()}

    async def execute(
        self,
        run_id: str,
        *,
        candidate_id: str,
        operation_id: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        candidate = self._get(run_id, candidate_id)
        if not candidate.active or candidate.admission != "admitted":
            raise ToolAdmissionError("The candidate has not passed admission and activation.")
        if candidate.candidate_id == "builtin.safe-compute.v1":
            language = str(parameters.get("language") or "").strip().casefold()
            code = str(parameters.get("code") or "").strip()
            inputs = parameters.get("inputs") or {}
            if language != "python" or not code or not isinstance(inputs, dict):
                raise ToolAdmissionError(
                    "safe_compute requires language=python, expression code, and object inputs.",
                    code="safe_compute_input_invalid",
                )
            value = _safe_evaluate(code, inputs)
            return {
                "ok": True,
                "source_type": "external_tool",
                "claim_scope": "diagnostic",
                "candidate_id": candidate_id,
                "operation_id": "evaluate",
                "result": value,
            }
        return await self._execute_openapi(candidate, operation_id, parameters)

    def counts(self, run_id: str) -> tuple[int, int]:
        items = list(self._candidates.get(run_id, {}).values())
        return len(items), sum(item.active for item in items)

    def snapshot(self, run_id: str) -> list[dict[str, Any]]:
        return [
            item.storage_dict()
            for item in sorted(
                self._candidates.get(run_id, {}).values(), key=lambda candidate: candidate.candidate_id
            )
        ]

    def restore(self, run_id: str, records: list[dict[str, Any]]) -> None:
        candidates = self._candidates.setdefault(run_id, {})
        for record in records:
            candidate = ToolCandidate.from_storage(record)
            if candidate.candidate_id and candidate.source_hash.startswith("sha256:"):
                candidates.setdefault(candidate.candidate_id, candidate)

    def _get(self, run_id: str, candidate_id: str) -> ToolCandidate:
        candidate = self._candidates.get(run_id, {}).get(candidate_id)
        if candidate is None:
            raise ToolAdmissionError("Unknown run-scoped tool candidate.", code="tool_candidate_unknown")
        return candidate

    async def _discover_openapi(self, manifest_url: str) -> ToolCandidate:
        await _require_public_https(manifest_url)
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
            response = await client.get(manifest_url, headers={"Accept": "application/json"})
        if response.is_redirect:
            raise ToolAdmissionError("Tool manifests may not redirect.")
        response.raise_for_status()
        if len(response.content) > self.max_bytes:
            raise ToolAdmissionError("Tool manifest exceeds the bounded size limit.")
        try:
            spec = response.json()
        except ValueError as exc:
            raise ToolAdmissionError("Tool manifest is not JSON OpenAPI.") from exc
        if not isinstance(spec, dict):
            raise ToolAdmissionError("Tool manifest must be an object.")
        digest = hashlib.sha256(
            json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        operations, reason = await _admit_openapi(spec, manifest_url)
        admitted = bool(operations) and reason == ""
        candidate_id = f"openapi.{digest[:24]}"
        return ToolCandidate(
            candidate_id=candidate_id,
            name=str((spec.get("info") or {}).get("title") or "Public OpenAPI tool"),
            source=manifest_url,
            version=str((spec.get("info") or {}).get("version") or "unknown"),
            source_hash=f"sha256:{digest}",
            admission="admitted" if admitted else "discovery_only",
            reason=reason or "Credential-free HTTPS GET/HEAD operations passed admission.",
            operations=operations,
            spec=spec,
        )

    async def _execute_openapi(
        self,
        candidate: ToolCandidate,
        operation_id: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        operation = next(
            (item for item in candidate.operations if item["operation_id"] == operation_id), None
        )
        if operation is None:
            raise ToolAdmissionError("The requested operation is not admitted.")
        if _contains_sensitive(parameters):
            raise ToolAdmissionError("External tool input contains sensitive or oversized data.")
        path = operation["path"]
        query: dict[str, Any] = {}
        declared = {item["name"]: item for item in operation.get("parameters", [])}
        missing_required = [
            name
            for name, descriptor in declared.items()
            if descriptor.get("required") and name not in parameters
        ]
        if missing_required:
            raise ToolAdmissionError(
                "Required external tool parameters are missing: " + ", ".join(missing_required),
                code="external_tool_input_invalid",
            )
        for name, value in parameters.items():
            descriptor = declared.get(name)
            if descriptor is None:
                raise ToolAdmissionError(f"Undeclared external tool parameter: {name}")
            try:
                Draft202012Validator(descriptor["schema"]).validate(value)
            except (SchemaError, ValidationError) as exc:
                raise ToolAdmissionError(
                    f"External tool parameter failed its admitted schema: {name}",
                    code="external_tool_input_invalid",
                ) from exc
            if descriptor["in"] == "path":
                path = path.replace("{" + name + "}", quote(str(value), safe=""))
            elif descriptor["in"] == "query":
                query[name] = value
        if "{" in path or "}" in path:
            raise ToolAdmissionError("Required external tool path parameter is missing.")
        url = urljoin(operation["server"].rstrip("/") + "/", path.lstrip("/"))
        await _require_public_https(url)
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
            response = await client.request(
                operation["method"], url, params=query, headers={"Accept": "application/json"}
            )
        if response.is_redirect:
            raise ToolAdmissionError("External tool responses may not redirect.")
        if len(response.content) > self.max_bytes:
            raise ToolAdmissionError("External tool response exceeds the bounded size limit.")
        response.raise_for_status()
        if operation["method"] == "HEAD":
            result: Any = None
        else:
            try:
                result = response.json()
            except ValueError as exc:
                raise ToolAdmissionError(
                    "External tool response was not declared JSON.",
                    code="external_tool_output_invalid",
                ) from exc
        try:
            Draft202012Validator(operation["output_schema"]).validate(result)
        except (SchemaError, ValidationError) as exc:
            raise ToolAdmissionError(
                "External tool response failed its admitted output schema.",
                code="external_tool_output_invalid",
            ) from exc
        return {
            "ok": True,
            "source_type": "external_tool",
            "claim_scope": "diagnostic",
            "candidate_id": candidate.candidate_id,
            "operation_id": operation_id,
            "source_hash": candidate.source_hash,
            "result": result,
        }

    @staticmethod
    def _discover_configured_mcp(query: str, capability: str) -> list[ToolCandidate]:
        config_path = Path.home() / ".codex" / "config.toml"
        if not config_path.is_file():
            return []
        try:
            payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return []
        names = sorted((payload.get("mcp_servers") or {}).keys())
        needle = f"{query} {capability}".casefold()
        result: list[ToolCandidate] = []
        for name in names:
            if needle and name.casefold() not in needle and "mcp" not in needle:
                continue
            digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
            result.append(
                ToolCandidate(
                    candidate_id=f"configured-mcp.{digest[:16]}",
                    name=name,
                    source="local_codex_configuration",
                    version="unknown",
                    source_hash=f"sha256:{digest}",
                    admission="discovery_only",
                    reason=(
                        "Configured MCP behavior cannot be proven read-only; it is intentionally "
                        "not inherited by the Harness."
                    ),
                )
            )
        return result

    @staticmethod
    def _discover_local_assets(query: str, capability: str) -> list[ToolCandidate]:
        """Discover installed Skills/Plugins without exposing paths or loading code."""

        needle_tokens = {
            item
            for item in f"{query} {capability}".casefold().replace("/", " ").split()
            if len(item) >= 2
        }
        records: list[tuple[str, str, bytes]] = []
        skill_root = Path.home() / ".codex" / "skills"
        if skill_root.is_dir():
            for path in sorted(skill_root.glob("*/SKILL.md"))[:200]:
                try:
                    content = path.read_bytes()
                except OSError:
                    continue
                if len(content) <= 256_000:
                    records.append(("local_codex_skill", path.parent.name, content))
        plugin_root = Path.home() / ".codex" / "plugins" / "cache"
        if plugin_root.is_dir():
            for path in sorted(plugin_root.glob("**/.codex-plugin/plugin.json"))[:200]:
                try:
                    content = path.read_bytes()
                except OSError:
                    continue
                if len(content) <= 256_000:
                    records.append(("installed_codex_plugin", path.parent.parent.name, content))
        result: list[ToolCandidate] = []
        for source, fallback_name, content in records:
            digest = hashlib.sha256(content).hexdigest()
            text = content.decode("utf-8", errors="replace")
            name = fallback_name
            version = "unknown"
            description = ""
            if source == "installed_codex_plugin":
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    payload = {}
                if isinstance(payload, dict):
                    name = str(payload.get("name") or payload.get("display_name") or name)
                    version = str(payload.get("version") or version)
                    description = str(payload.get("description") or "")
            else:
                for line in text.splitlines()[:80]:
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip().strip('"\'') or name
                    elif line.startswith("description:"):
                        description = line.split(":", 1)[1].strip().strip('"\'')
            searchable = f"{name} {description}".casefold()
            if needle_tokens and not any(token in searchable for token in needle_tokens):
                continue
            result.append(
                ToolCandidate(
                    candidate_id=f"{source}.{digest[:20]}",
                    name=name,
                    source=source,
                    version=version,
                    source_hash=f"sha256:{digest}",
                    admission="discovery_only",
                    reason=(
                        "Installed asset metadata is discoverable, but its runtime behavior and "
                        "credential boundary are not admitted for automatic execution."
                    ),
                )
            )
        return result[:100]


async def _admit_openapi(
    spec: dict[str, Any], manifest_url: str
) -> tuple[list[dict[str, Any]], str]:
    if not (str(spec.get("openapi") or "").startswith("3.") or str(spec.get("swagger")) == "2.0"):
        return [], "Only OpenAPI 3.x or Swagger 2.0 JSON manifests are supported."
    if spec.get("security"):
        return [], "Authenticated OpenAPI tools are discovery-only in the prototype."
    servers = spec.get("servers") or []
    if servers:
        server = str((servers[0] or {}).get("url") or "")
    else:
        parsed = urlsplit(manifest_url)
        server = f"{parsed.scheme}://{parsed.netloc}"
    try:
        await _require_public_https(server)
    except ToolAdmissionError as exc:
        return [], str(exc)
    operations: list[dict[str, Any]] = []
    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict) or not str(path).startswith("/"):
            continue
        common_parameters = path_item.get("parameters") or []
        for method in ("get", "head"):
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            if operation.get("security") or operation.get("requestBody"):
                continue
            parameters: list[dict[str, str]] = []
            valid = True
            for parameter in [*common_parameters, *(operation.get("parameters") or [])]:
                if not isinstance(parameter, dict) or "$ref" in parameter:
                    valid = False
                    break
                location = str(parameter.get("in") or "")
                name = str(parameter.get("name") or "")
                if location not in {"path", "query"} or not name:
                    valid = False
                    break
                schema = parameter.get("schema")
                if not _admissible_json_schema(schema):
                    valid = False
                    break
                parameters.append(
                    {
                        "name": name,
                        "in": location,
                        "required": bool(parameter.get("required")),
                        "schema": schema,
                    }
                )
            if not valid:
                continue
            output_schema = _operation_output_schema(operation, method)
            if output_schema is None:
                continue
            operation_id = str(operation.get("operationId") or f"{method}_{path}")
            operations.append(
                {
                    "operation_id": operation_id,
                    "method": method.upper(),
                    "server": server,
                    "path": path,
                    "parameters": parameters,
                    "output_schema": output_schema,
                    "read_only": True,
                }
            )
    if not operations:
        return [], "No enforceable credential-free HTTPS GET/HEAD operation was found."
    return operations[:100], ""


def _operation_output_schema(operation: dict[str, Any], method: str) -> dict[str, Any] | None:
    if method == "head":
        return {"type": "null"}
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return None
    descriptor: Any = None
    for code, candidate in responses.items():
        if str(code).isdigit() and 200 <= int(code) < 300 and isinstance(candidate, dict):
            descriptor = candidate
            break
    if not isinstance(descriptor, dict):
        return None
    content = descriptor.get("content")
    if isinstance(content, dict):
        media = content.get("application/json")
        schema = media.get("schema") if isinstance(media, dict) else None
    else:
        schema = descriptor.get("schema")
    return schema if _admissible_json_schema(schema) else None


def _admissible_json_schema(value: Any) -> bool:
    if not isinstance(value, dict) or _schema_contains_ref(value):
        return False
    try:
        Draft202012Validator.check_schema(value)
    except SchemaError:
        return False
    return True


def _schema_contains_ref(value: Any) -> bool:
    if isinstance(value, dict):
        return "$ref" in value or any(_schema_contains_ref(child) for child in value.values())
    if isinstance(value, list):
        return any(_schema_contains_ref(child) for child in value)
    return False


async def _require_public_https(url: str) -> None:
    parsed = urlsplit(str(url))
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ToolAdmissionError("External tools must use credential-free HTTPS URLs.")
    if parsed.port not in {None, 443}:
        raise ToolAdmissionError("External tools must use the standard HTTPS port.")

    def resolve() -> list[str]:
        return list(
            dict.fromkeys(
                item[4][0]
                for item in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
            )
        )

    try:
        addresses = await asyncio.to_thread(resolve)
    except OSError as exc:
        raise ToolAdmissionError("External tool host could not be resolved.") from exc
    if not addresses:
        raise ToolAdmissionError("External tool host resolved to no addresses.")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ToolAdmissionError("External tools may not access local or private networks.")


_SENSITIVE_KEYS = {
    "authorization",
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "sap_url",
    "sap_client",
    "username",
}


def _contains_sensitive(value: Any, *, depth: int = 0) -> bool:
    if depth > 8:
        return True
    if isinstance(value, dict):
        if len(value) > 100:
            return True
        return any(
            str(key).casefold() in _SENSITIVE_KEYS or _contains_sensitive(child, depth=depth + 1)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return len(value) > 500 or any(_contains_sensitive(item, depth=depth + 1) for item in value)
    if isinstance(value, str):
        lowered = value.casefold()
        return len(value) > 20_000 or "authorization:" in lowered or "sap/opu/" in lowered
    return False


_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Not: operator.not_}
_COMPARE = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}
_CALLS = {
    "abs": abs,
    "len": len,
    "max": max,
    "min": min,
    "round": round,
    "sorted": sorted,
    "sum": sum,
}


def _safe_evaluate(expression: str, inputs: dict[str, Any]) -> Any:
    if len(expression) > 4_000 or _contains_sensitive(inputs):
        raise ToolAdmissionError("Safe compute input exceeds its bounded contract.")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolAdmissionError("Safe compute expression is invalid.") from exc
    value = _eval_node(tree.body, inputs)
    json.dumps(value, allow_nan=False)
    return value


def _eval_node(node: ast.AST, inputs: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in inputs:
        return inputs[node.id]
    if isinstance(node, ast.List):
        return [_eval_node(item, inputs) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(item, inputs) for item in node.elts)
    if isinstance(node, ast.Dict):
        return {
            _eval_node(key, inputs): _eval_node(value, inputs)
            for key, value in zip(node.keys, node.values, strict=True)
        }
    if isinstance(node, ast.Subscript):
        return _eval_node(node.value, inputs)[_eval_node(node.slice, inputs)]
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left = _eval_node(node.left, inputs)
        right = _eval_node(node.right, inputs)
        if isinstance(node.op, ast.Pow) and abs(float(right)) > 10:
            raise ToolAdmissionError("Safe compute exponent is too large.")
        return _BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval_node(node.operand, inputs))
    if isinstance(node, ast.BoolOp):
        values = [_eval_node(item, inputs) for item in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, inputs)
        for operation, comparator in zip(node.ops, node.comparators, strict=True):
            right = _eval_node(comparator, inputs)
            function = _COMPARE.get(type(operation))
            if function is None or not function(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _eval_node(node.body if _eval_node(node.test, inputs) else node.orelse, inputs)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _CALLS:
        args = [_eval_node(item, inputs) for item in node.args]
        kwargs = {item.arg: _eval_node(item.value, inputs) for item in node.keywords if item.arg}
        return _CALLS[node.func.id](*args, **kwargs)
    raise ToolAdmissionError(
        f"Safe compute rejected expression node: {type(node).__name__}",
        code="safe_compute_expression_rejected",
    )
