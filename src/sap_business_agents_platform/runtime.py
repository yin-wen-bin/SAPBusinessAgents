from __future__ import annotations

import asyncio
import importlib.util
import subprocess
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
    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds

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

    def __init__(self, manager: SDKManager, providers: dict[str, Any]) -> None:
        self.manager = manager
        self.providers = dict(providers)
        self._pinned_provider: ContextVar[str | None] = ContextVar(
            "sapba_runtime_provider", default=None
        )

    @property
    def current_provider_id(self) -> str:
        return self._pinned_provider.get() or self.manager.default_provider_id

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
        return self.manager.runtime_snapshot(selected)

    @contextmanager
    def pin(self, provider_id: str | None) -> Iterator[None]:
        selected = provider_id or self.manager.default_provider_id
        self._provider(selected)
        token = self._pinned_provider.set(selected)
        try:
            yield
        finally:
            self._pinned_provider.reset(token)

    def supports(self, operation: str) -> bool:
        provider = self._provider(self.current_provider_id)
        return callable(getattr(provider, operation, None))

    def bind_events(self, sink: Any) -> Any:
        provider = self._provider(self.current_provider_id)
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
        provider = self._provider(self.current_provider_id)
        method = getattr(provider, "cancel", None)
        if callable(method):
            await method(thread_id)

    async def execute(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke(operation, *args, **kwargs)

    async def _invoke(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        provider = self._provider(self.current_provider_id)
        method = getattr(provider, operation, None)
        if not callable(method):
            raise RuntimeUnavailableError(
                f"The selected Agent Runtime does not support {operation}.",
                code="runtime_operation_unavailable",
            )
        return await method(*args, **kwargs)

    def _provider(self, provider_id: str) -> Any:
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
            "configuration_digest": "injected-planner",
            "capabilities": ["planning"],
            "selected_at": None,
        }

    @contextmanager
    def pin(self, provider_id: str | None) -> Iterator[None]:
        del provider_id
        yield

    def supports(self, operation: str) -> bool:
        return callable(getattr(self.planner, operation, None))

    @contextmanager
    def bind_events(self, sink: Any) -> Iterator[None]:
        del sink
        yield

    def __getattr__(self, name: str) -> Any:
        return getattr(self.planner, name)
