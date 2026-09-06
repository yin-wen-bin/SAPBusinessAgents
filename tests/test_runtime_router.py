from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sap_business_agents_platform.runtime import RuntimeRouter, WorkBuddyRuntimeProbe
from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner


class FakeManager:
    def __init__(self) -> None:
        self.default_provider_id = "codex"
        self.models = {"codex": "codex-model", "workbuddy": "workbuddy-model"}

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "provider_id": provider_id,
                "selectable": True,
                "blockers": [],
            }
            for provider_id in ("codex", "workbuddy")
        ]

    def runtime_snapshot(self, provider_id: str) -> dict[str, Any]:
        return {
            "provider_id": provider_id,
            "sdk_id": f"{provider_id}-sdk",
            "version": "1.0.0",
            "model": self.models[provider_id],
            "model_catalog_digest": "catalog",
            "model_check_digest": "check",
            "runtime_configuration_revision": 7,
            "configuration_digest": provider_id,
            "capabilities": ["planning"],
            "selected_at": "2026-08-29T00:00:00Z",
        }


class FakePlanner:
    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id
        self.calls: list[str] = []

    async def plan(self, query: str, *_args: Any, **_kwargs: Any) -> str:
        self.calls.append(query)
        return self.provider_id


def test_runtime_router_pins_existing_task_when_default_changes() -> None:
    manager = FakeManager()
    codex = FakePlanner("codex")
    workbuddy = FakePlanner("workbuddy")
    router = RuntimeRouter(manager, {"codex": codex, "workbuddy": workbuddy})

    existing = router.snapshot()
    assert existing["model"] == "codex-model"
    assert existing["configuration_digest"] == hashlib.sha256(
        json.dumps(
            {
                "sdk_configuration_digest": "codex",
                "model": "codex-model",
                "model_catalog_digest": "catalog",
                "model_check_digest": "check",
                "runtime_configuration_revision": 7,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    manager.default_provider_id = "workbuddy"

    with router.pin(existing["provider_id"], existing["model"]):
        assert asyncio.run(router.plan("existing", {}, {}, [])) == "codex"
    assert asyncio.run(router.plan("new", {}, {}, [])) == "workbuddy"
    assert codex.calls == ["existing"]
    assert workbuddy.calls == ["new"]


def test_runtime_router_uses_isolated_provider_instances_per_model() -> None:
    manager = FakeManager()
    created: dict[str, FakePlanner] = {}

    def factory(model: str | None) -> FakePlanner:
        planner = FakePlanner(f"codex:{model}")
        created[str(model)] = planner
        return planner

    router = RuntimeRouter(manager, {}, provider_factories={"codex": factory})
    existing = router.snapshot()
    manager.models["codex"] = "new-model"

    with router.pin(existing["provider_id"], existing["model"]):
        assert asyncio.run(router.plan("existing", {}, {}, [])) == "codex:codex-model"
    assert asyncio.run(router.plan("new", {}, {}, [])) == "codex:new-model"
    assert created["codex-model"].calls == ["existing"]
    assert created["new-model"].calls == ["new"]


def test_workbuddy_adapter_denies_builtin_tools_and_resumes_session(
    tmp_path: Path, monkeypatch: Any
) -> None:
    captured: dict[str, Any] = {}
    response = json.dumps(
        {
            "intent": "read one SAP entity",
            "needs_clarification": False,
            "clarification_question": "",
            "plan_json": json.dumps(
                {
                    "service_name": "API_FIXTURE_SRV",
                    "odata_version": "2.0",
                    "entity_set": "A_Fixture",
                    "method": "GET",
                }
            ),
        }
    )

    class CodeBuddyAgentOptions:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    class TextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    class AssistantMessage:
        def __init__(self, content: list[Any]) -> None:
            self.content = content

    class ResultMessage:
        def __init__(self) -> None:
            self.session_id = "workbuddy-session"
            self.is_error = False
            self.errors: list[str] = []
            self.result = response
            self.structured_output = None

    class PermissionResultDeny:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class CodeBuddySDKClient:
        def __init__(self, *, options: Any) -> None:
            self.options = options

        async def __aenter__(self) -> "CodeBuddySDKClient":
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def query(self, prompt: str) -> None:
            captured["prompt"] = prompt

        async def receive_response(self):
            yield AssistantMessage([TextBlock(response)])
            yield ResultMessage()

        async def interrupt(self) -> None:
            return None

    fake_module = SimpleNamespace(
        AssistantMessage=AssistantMessage,
        CodeBuddyAgentOptions=CodeBuddyAgentOptions,
        CodeBuddySDKClient=CodeBuddySDKClient,
        PermissionResultDeny=PermissionResultDeny,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
    )
    monkeypatch.setitem(sys.modules, "codebuddy_agent_sdk", fake_module)

    planner = WorkBuddyPlanner(tmp_path)
    events: list[tuple[str, dict[str, Any]]] = []
    with planner.bind_events(lambda event_type, data: events.append((event_type, data))):
        decision = asyncio.run(
            planner.plan(
                "fixture",
                {"data": {"items": []}},
                {"data": {}},
                [],
                thread_id="prior-session",
            )
        )

    assert decision.thread_id == "workbuddy-session"
    assert decision.plan is not None
    assert decision.plan["method"] == "GET"
    assert captured["tools"] == []
    assert captured["allowed_tools"] == []
    assert captured["permission_mode"] == "plan"
    assert captured["setting_sources"] == []
    assert captured["resume"] == "prior-session"
    assert {"Bash", "Write", "Edit", "WebFetch", "Agent", "Skill"}.issubset(
        set(captured["disallowed_tools"])
    )
    assert callable(captured["can_use_tool"])
    assert "Return exactly one JSON object" in captured["prompt"]
    assert [event_type for event_type, _data in events] == [
        "agent_runtime_turn_started",
        "agent_runtime_response_received",
        "agent_runtime_turn_completed",
    ]
    assert all(data["provider_id"] == "workbuddy" for _event, data in events)


def test_workbuddy_authentication_probe_rejects_login_flow(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cancelled = False

    class LoginFlow:
        auth_url = "https://login.invalid/opaque"

        async def cancel(self) -> None:
            nonlocal cancelled
            cancelled = True

    async def authenticate(**_kwargs: Any) -> LoginFlow:
        return LoginFlow()

    monkeypatch.setitem(
        sys.modules,
        "codebuddy_agent_sdk",
        SimpleNamespace(authenticate=authenticate),
    )
    probe = WorkBuddyRuntimeProbe(tmp_path)
    result = asyncio.run(probe.check_authentication(None))  # type: ignore[arg-type]

    assert result["authenticated"] is False
    assert result["status"] == "login_required"
    assert result["error"]["code"] == "workbuddy_existing_login_unavailable"
    assert cancelled is True
