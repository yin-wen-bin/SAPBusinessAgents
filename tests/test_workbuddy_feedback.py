"""Independent WorkBuddy draft-feedback contract tests; never starts a real SDK."""
import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner, WorkBuddyRuntimeError


def sdk_fixture(monkeypatch, responses, *, delayed=False):
    captured = []
    class Options:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    class TextBlock:
        def __init__(self, text):
            self.text = text
    class AssistantMessage:
        pass
    class ResultMessage:
        def __init__(self, response):
            self.session_id = "workbuddy-owned-session"
            self.is_error = False
            self.errors = []
            self.result = json.dumps(response)
            self.structured_output = None
    class Denied:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    class Client:
        def __init__(self, *, options):
            self.options, self.prompt, self.closed, self.interrupted = options, "", False, False
            captured.append(self)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            self.closed = True
        async def query(self, prompt):
            self.prompt = prompt
        async def receive_response(self):
            if delayed:
                await asyncio.Event().wait()
            yield ResultMessage(responses.pop(0))
        async def interrupt(self):
            self.interrupted = True
    monkeypatch.setitem(sys.modules, "codebuddy_agent_sdk", SimpleNamespace(
        CodeBuddyAgentOptions=Options, CodeBuddySDKClient=Client, TextBlock=TextBlock,
        AssistantMessage=AssistantMessage, ResultMessage=ResultMessage, PermissionResultDeny=Denied))
    return captured


def response(action="reply", **extra):
    return {"action": action, "summary": {"zh": "已解释", "en": "Explained"}, "required_changes": [],
            **{key: "" for key in ("manifest_json", "readme", "rules_source", "files_json", "edits_json", "clarification_json")}, **extra}


PACKAGE = {"manifest": {"title": {"zh": "原名称", "en": "Before"}}, "readme": "Before", "rules": None, "files": {}}


@pytest.mark.parametrize("action", ["reply", "clarify", "revise_agent"])
def test_workbuddy_feedback_is_tool_free_new_session_and_bound_model(tmp_path, monkeypatch, action):
    extra = {"edits_json": '[{"op":"replace","path":"/manifest/title/zh","value":"新名称"}]'} if action == "revise_agent" else {}
    if action == "clarify":
        extra["clarification_json"] = json.dumps({"question": {"zh": "是否保留？", "en": "Keep it?"}, "answer_mode": "confirm", "options": []})
    clients = sdk_fixture(monkeypatch, [response(action, **extra)])
    planner = WorkBuddyPlanner(tmp_path, "bound-workbuddy-model")
    events = []
    async def scenario():
        with planner.bind_events(lambda kind, value: events.append((kind, value))):
            result = await planner.review_agent_feedback(feedback="password=synthetic-secret 请解释", locale="zh", package=PACKAGE,
                history=[{"kind": "platform_context", "pending_clarifications": []}], thread_id="codex-thread-must-not-reuse", operation_id="owned-feedback")
        assert await planner.abort_agent_feedback("owned-feedback") is True
        denied = await clients[0].options.can_use_tool("Bash", {}, None)
        assert denied.interrupt is False
        return result
    result = asyncio.run(scenario())
    assert result["action"] == action
    assert clients[0].options.resume is None
    assert clients[0].options.model == "bound-workbuddy-model"
    assert clients[0].options.cwd != tmp_path
    assert clients[0].options.tools == clients[0].options.allowed_tools == []
    assert clients[0].options.permission_mode == "plan"
    assert clients[0].options.setting_sources == []
    assert clients[0].closed and "synthetic-secret" not in clients[0].prompt
    assert events[0][0] == "agent_runtime_turn_started" and events[-1][0] == "agent_runtime_turn_completed"
    assert planner.feedback_capabilities() == {"stream_events": True, "resume": False, "steer": False, "image_input": False}
    if action == "clarify":
        assert result["clarification"]["answer_mode"] == "confirm"
    if action == "revise_agent":
        assert result["edits"][0]["path"] == "/manifest/title/zh"


def test_workbuddy_explain_rejects_definition_writes(tmp_path, monkeypatch):
    sdk_fixture(monkeypatch, [response("revise_agent", edits_json='[{"op":"replace","path":"/readme","value":"After"}]')])
    planner = WorkBuddyPlanner(tmp_path, "bound-model")
    with pytest.raises(WorkBuddyRuntimeError) as failure:
        asyncio.run(planner.review_agent_feedback(feedback="Explain", locale="en", package=PACKAGE, intent="explain", operation_id="explain"))
    assert failure.value.code == "agent_explanation_write_rejected"


def test_workbuddy_schema_repair_keeps_system_guard_and_only_its_own_session(tmp_path, monkeypatch):
    clients = sdk_fixture(monkeypatch, [{"invalid": True}, response()])
    planner = WorkBuddyPlanner(tmp_path, "bound-model")
    result = asyncio.run(planner.review_agent_feedback(feedback="Explain", locale="en", package=PACKAGE, operation_id="repair"))
    assert result["action"] == "reply"
    assert clients[0].options.resume is None
    assert clients[1].options.resume == "workbuddy-owned-session"
    assert clients[1].options.system_prompt == clients[0].options.system_prompt
    assert all(client.closed for client in clients)


def test_workbuddy_unsupported_images_are_not_silently_discarded(tmp_path):
    planner = WorkBuddyPlanner(tmp_path, "bound-model")
    with pytest.raises(WorkBuddyRuntimeError) as failure:
        asyncio.run(planner.review_agent_feedback(feedback="Screenshot", locale="en", package=PACKAGE, image_inputs=["data:image/png;base64,AA=="]))
    assert failure.value.code == "agent_feedback_image_unsupported"


def test_workbuddy_cancel_is_owned_not_global(tmp_path, monkeypatch):
    clients = sdk_fixture(monkeypatch, [], delayed=True)
    planner = WorkBuddyPlanner(tmp_path, "bound-model")
    async def scenario():
        tasks = [asyncio.create_task(planner.review_agent_feedback(feedback="Explain", locale="en", package=PACKAGE, operation_id=key))
                 for key in ("one", "two")]
        for _ in range(30):
            if len(clients) == 2 and all(client.prompt for client in clients):
                break
            await asyncio.sleep(0)
        tasks[0].cancel()
        await asyncio.gather(tasks[0], return_exceptions=True)
        assert await planner.abort_agent_feedback("one") is True
        assert not clients[1].closed and not clients[1].interrupted
        assert await planner.abort_agent_feedback("unknown") is False
        tasks[1].cancel()
        await asyncio.gather(tasks[1], return_exceptions=True)
        assert clients[1].closed
    asyncio.run(scenario())
