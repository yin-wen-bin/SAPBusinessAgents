from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import subprocess
from importlib import metadata
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

from .sdk_manager import SDKDefinition, SDKManager


class RuntimeUnavailableError(RuntimeError):
    def __init__(self, message: str, *, code: str = "runtime_unavailable") -> None:
        super().__init__(message)
        self.code = code


class CodexRuntimeProbe:
    def __init__(self, *, timeout_seconds: float = 10.0, model_timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.model_timeout_seconds = model_timeout_seconds

    def client_version(self, definition: SDKDefinition) -> str | None:
        del definition
        try:
            return metadata.version("openai-codex-cli-bin")
        except metadata.PackageNotFoundError:
            return None

    async def list_models(self, definition: SDKDefinition) -> dict[str, Any]:
        del definition
        try:
            from openai_codex import AsyncCodex
        except ImportError as exc:
            raise RuntimeUnavailableError(
                "The Codex Python SDK is not installed.", code="sdk_not_installed"
            ) from exc
        async with AsyncCodex() as codex:
            response = await asyncio.wait_for(
                codex.models(include_hidden=False), timeout=self.model_timeout_seconds
            )
        return {
            "models": [item.model_dump(mode="json") for item in response.data],
            "next_cursor": response.next_cursor,
        }

    async def check_model(
        self, definition: SDKDefinition, model_id: str, workspace: Path
    ) -> dict[str, Any]:
        del definition
        try:
            from openai_codex import ApprovalMode, AsyncCodex, Sandbox
        except ImportError as exc:
            raise RuntimeUnavailableError(
                "The Codex Python SDK is not installed.", code="sdk_not_installed"
            ) from exc

        async def execute() -> None:
            async with AsyncCodex() as codex:
                thread = await codex.thread_start(
                    cwd=str(workspace),
                    ephemeral=True,
                    model=model_id,
                    sandbox=Sandbox.read_only,
                    approval_mode=ApprovalMode.deny_all,
                    developer_instructions=(
                        "This is a compatibility probe. Do not use tools, inspect files, "
                        "or access any external system. Return only the requested JSON."
                    ),
                )
                result = await thread.run(
                    'Return exactly {"status":"ok"}.',
                    output_schema={
                        "type": "object",
                        "properties": {"status": {"type": "string", "const": "ok"}},
                        "required": ["status"],
                        "additionalProperties": False,
                    },
                )
                payload = json.loads(result.final_response or "{}")
                if payload != {"status": "ok"}:
                    raise RuntimeUnavailableError(
                        "The model probe returned an invalid response.",
                        code="runtime_model_probe_invalid_response",
                    )

        try:
            await asyncio.wait_for(execute(), timeout=self.model_timeout_seconds)
        except TimeoutError as exc:
            raise RuntimeUnavailableError(
                "The model compatibility check timed out.",
                code="runtime_model_probe_timeout",
            ) from exc
        except Exception as exc:
            if isinstance(exc, RuntimeUnavailableError):
                raise
            message = str(exc) or type(exc).__name__
            lowered = message.lower()
            if "requires a newer version" in lowered or "upgrade" in lowered:
                code = "runtime_model_incompatible"
            elif any(value in lowered for value in ("not available", "not found", "permission", "access")):
                code = "runtime_model_access_unavailable"
            elif any(value in lowered for value in ("login", "authentication", "unauthorized")):
                code = "runtime_model_authentication_failed"
            else:
                code = "runtime_model_probe_failed"
            raise RuntimeUnavailableError(message, code=code) from exc
        return {"compatible": True, "status": "compatible", "error": None}

    async def check_authentication(self, definition: SDKDefinition) -> dict[str, Any]:
        del definition
        installed = importlib.util.find_spec("openai_codex") is not None
        if not installed:
            return {
                "authenticated": False,
                "status": "sdk_not_installed",
                "error": {
                    "code": "sdk_not_installed",
                    "message": "The Codex Python SDK is not installed.",
                },
            }
        try:
            from codex_cli_bin import bundled_codex_path

            process = await asyncio.create_subprocess_exec(
                str(bundled_codex_path()),
                "login",
                "status",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(
                    process.communicate(), timeout=self.timeout_seconds
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                raise RuntimeUnavailableError(
                    "Codex login status check timed out.",
                    code="codex_authentication_check_timeout",
                )
            authenticated = process.returncode == 0
        except Exception as exc:
            return {
                "authenticated": False,
                "status": "failed",
                "error": {
                    "code": str(
                        getattr(exc, "code", "codex_authentication_check_failed")
                    ),
                    "message": str(exc) or type(exc).__name__,
                },
            }
        return {
            "authenticated": authenticated,
            "status": "existing_login" if authenticated else "login_required",
            "error": (
                None
                if authenticated
                else {
                    "code": "codex_existing_login_unavailable",
                    "message": "Codex is not logged in on this Windows account.",
                }
            ),
        }


class WorkBuddyRuntimeProbe:
    def __init__(self, repository_root: Path, *, timeout_seconds: float = 10.0) -> None:
        self.repository_root = repository_root.resolve()
        self.timeout_seconds = timeout_seconds

    async def check_authentication(self, definition: SDKDefinition) -> dict[str, Any]:
        del definition
        try:
            from codebuddy_agent_sdk import authenticate
        except ImportError:
            return {
                "authenticated": False,
                "status": "sdk_not_installed",
                "error": {
                    "code": "sdk_not_installed",
                    "message": "The WorkBuddy Python SDK is not installed.",
                },
            }
        flow = None
        try:
            flow = await asyncio.wait_for(
                authenticate(timeout=self.timeout_seconds),
                timeout=self.timeout_seconds,
            )
            if flow.auth_url:
                await flow.cancel()
                return {
                    "authenticated": False,
                    "status": "login_required",
                    "error": {
                        "code": "workbuddy_existing_login_unavailable",
                        "message": "WorkBuddy requires an interactive CodeBuddy login.",
                    },
                }
            result = await asyncio.wait_for(
                flow.wait(timeout=self.timeout_seconds),
                timeout=self.timeout_seconds,
            )
            if not getattr(getattr(result, "userinfo", None), "user_id", ""):
                raise RuntimeUnavailableError(
                    "WorkBuddy authentication returned no user identity.",
                    code="workbuddy_existing_login_unavailable",
                )
        except Exception as exc:
            if flow is not None:
                try:
                    await flow.cancel()
                except Exception:
                    pass
            return {
                "authenticated": False,
                "status": "failed",
                "error": {
                    "code": str(
                        getattr(exc, "code", "workbuddy_existing_login_unavailable")
                    ),
                    "message": str(exc) or type(exc).__name__,
                },
            }
        return {
            "authenticated": True,
            "status": "existing_login",
            "error": None,
        }


class RuntimeRouter:
    """Select one bounded planner and authoring runtime.

    The selected provider is normally the persisted global default. Run execution
    pins a provider so a later settings change cannot move an in-flight task.
    """

    def __init__(
        self,
        manager: SDKManager,
        providers: dict[str, Any],
        *,
        provider_factories: dict[str, Any] | None = None,
    ) -> None:
        self.manager = manager
        self.providers = dict(providers)
        self.provider_factories = dict(provider_factories or {})
        self._provider_cache: dict[tuple[str, str | None], Any] = {}
        self._pinned_binding: ContextVar[tuple[str, str | None] | None] = ContextVar(
            "sapba_runtime_binding", default=None
        )

    @property
    def current_provider_id(self) -> str:
        pinned = self._pinned_binding.get()
        selected = pinned[0] if pinned else self.manager.default_provider_id
        if not selected:
            raise RuntimeUnavailableError(
                "No default Agent Runtime is configured.",
                code="runtime_default_not_configured",
            )
        return selected

    @property
    def current_model_id(self) -> str | None:
        pinned = self._pinned_binding.get()
        if pinned:
            return pinned[1]
        snapshot = self.manager.runtime_snapshot(self.current_provider_id)
        model = snapshot.get("model")
        return str(model) if model else None

    def snapshot(self, provider_id: str | None = None) -> dict[str, Any]:
        selected = provider_id or self.current_provider_id
        item = next(
            (
                runtime
                for runtime in self.manager.list()
                if runtime.get("provider_id") == selected
            ),
            None,
        )
        if not isinstance(item, dict) or item.get("selectable") is not True:
            blockers = item.get("blockers") if isinstance(item, dict) else []
            raise RuntimeUnavailableError(
                "The selected Agent Runtime is not ready for new tasks"
                + (f": {', '.join(str(value) for value in blockers)}" if blockers else "."),
                code="runtime_not_selectable",
            )
        snapshot = self.manager.runtime_snapshot(selected)
        model = snapshot.get("model")
        self._provider(selected, str(model) if model else None)
        snapshot["configuration_digest"] = hashlib.sha256(
            json.dumps(
                {
                    "sdk_configuration_digest": snapshot["configuration_digest"],
                    "model": snapshot["model"],
                    "model_catalog_digest": snapshot.get("model_catalog_digest"),
                    "model_check_digest": snapshot.get("model_check_digest"),
                    "runtime_configuration_revision": snapshot.get(
                        "runtime_configuration_revision"
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return snapshot

    @contextmanager
    def pin(self, provider_id: str | None, model_id: str | None = None) -> Iterator[None]:
        selected = provider_id or self.manager.default_provider_id
        if not selected:
            raise RuntimeUnavailableError(
                "No default Agent Runtime is configured.",
                code="runtime_default_not_configured",
            )
        if model_id is None:
            model = self.manager.runtime_snapshot(selected).get("model")
            model_id = str(model) if model else None
        self._provider(selected, model_id)
        token = self._pinned_binding.set((selected, model_id))
        try:
            yield
        finally:
            self._pinned_binding.reset(token)

    def supports(self, operation: str) -> bool:
        provider = self._provider(self.current_provider_id, self.current_model_id)
        return callable(getattr(provider, operation, None))

    def bind_events(self, sink: Any) -> Any:
        provider = self._provider(self.current_provider_id, self.current_model_id)
        method = getattr(provider, "bind_events", None)
        return method(sink) if callable(method) else nullcontext()

    async def plan(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("plan", *args, **kwargs)

    async def ground_plan(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("ground_plan", *args, **kwargs)

    async def summarize(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("summarize", *args, **kwargs)

    async def author_draft(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("author_draft", *args, **kwargs)

    async def review_agent_feedback(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("review_agent_feedback", *args, **kwargs)

    async def compose_workflow(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("compose_workflow", *args, **kwargs)

    async def review_workflow(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("review_workflow", *args, **kwargs)

    async def repair_workflow(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("repair_workflow", *args, **kwargs)

    async def review_workflow_feedback(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("review_workflow_feedback", *args, **kwargs)

    async def resume_workflow_composition(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("review_workflow_feedback", *args, **kwargs)

    async def request_additional_input(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("plan", *args, **kwargs)

    async def review_free_query_feedback(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("review_free_query_feedback", *args, **kwargs)

    async def resume_free_query_session(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("plan", *args, **kwargs)

    async def revise_free_query_presentation(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("revise_free_query_presentation", *args, **kwargs)

    async def analyze_role_matching(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("analyze_role_matching", *args, **kwargs)

    async def review_role_matching_feedback(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("review_role_matching_feedback", *args, **kwargs)

    async def cancel(self, thread_id: str | None = None) -> None:
        provider = self._provider(self.current_provider_id, self.current_model_id)
        method = getattr(provider, "cancel", None)
        if callable(method):
            await method(thread_id)

    async def execute(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke(operation, *args, **kwargs)

    async def _invoke(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        provider = self._provider(self.current_provider_id, self.current_model_id)
        method = getattr(provider, operation, None)
        if not callable(method):
            raise RuntimeUnavailableError(
                f"The selected Agent Runtime does not support {operation}.",
                code="runtime_operation_unavailable",
            )
        return await method(*args, **kwargs)

    def _provider(self, provider_id: str, model_id: str | None = None) -> Any:
        factory = self.provider_factories.get(provider_id)
        if callable(factory):
            key = (provider_id, model_id)
            if key not in self._provider_cache:
                self._provider_cache[key] = factory(model_id)
            return self._provider_cache[key]
        provider = self.providers.get(provider_id)
        if provider is None:
            raise RuntimeUnavailableError(
                f"Agent Runtime provider is unavailable: {provider_id}",
                code="runtime_provider_unavailable",
            )
        return provider


class StaticRuntimeRouter:
    """Compatibility wrapper used by tests and explicit planner injection."""

    def __init__(self, planner: Any) -> None:
        self.planner = planner
        self.current_provider_id = "codex"

    def snapshot(self, provider_id: str | None = None) -> dict[str, Any]:
        del provider_id
        return {
            "provider_id": "codex",
            "sdk_id": "codex-python-sdk",
            "version": None,
            "model": getattr(self.planner, "model", None),
            "configuration_digest": "injected-planner",
            "capabilities": ["planning"],
            "selected_at": None,
        }

    @contextmanager
    def pin(self, provider_id: str | None, model_id: str | None = None) -> Iterator[None]:
        del provider_id, model_id
        yield

    def supports(self, operation: str) -> bool:
        return callable(getattr(self.planner, operation, None))

    @contextmanager
    def bind_events(self, sink: Any) -> Iterator[None]:
        del sink
        yield

    def __getattr__(self, name: str) -> Any:
        return getattr(self.planner, name)
