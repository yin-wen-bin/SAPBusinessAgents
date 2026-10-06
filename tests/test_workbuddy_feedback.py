"""Independent WorkBuddy draft-feedback contract tests; never starts a real SDK."""
import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner, WorkBuddyRuntimeError
from tests.workbuddy_fakes import inline_worker


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
            message = ResultMessage(responses.pop(0))
            if "json-schema" in self.options.extra_args:
                message.structured_output = json.loads(message.result)
            yield message
        async def interrupt(self):
            self.interrupted = True
    monkeypatch.setitem(sys.modules, "codebuddy_agent_sdk", SimpleNamespace(
        CodeBuddyAgentOptions=Options, CodeBuddySDKClient=Client, TextBlock=TextBlock,
        AssistantMessage=AssistantMessage, ResultMessage=ResultMessage, PermissionResultDeny=Denied,
        PermissionResultAllow=Denied, ToolUseBlock=type("ToolUse", (), {}), ToolResultBlock=type("ToolResult", (), {})))
    inline_worker(monkeypatch)
    return captured


def response(action="reply", **extra):
    return {"action": action, "summary": {"zh": "已解释", "en": "Explained"}, "required_changes": [],
            **{key: "" for key in ("manifest_json", "readme", "rules_source", "files_json", "edits_json", "clarification_json")}, **extra}


PACKAGE = {"manifest": {"title": {"zh": "原名称", "en": "Before"}}, "readme": "Before", "rules": None, "files": {}}


@pytest.fixture
def transport_candidate_checker(monkeypatch):
    # These native-transport fixtures deliberately use incomplete packages.
    # Candidate rejection/repair is tested with the real checker separately.
    from sap_business_agents_platform.authoring_harness import CheckOutcome
    monkeypatch.setattr("sap_business_agents_platform.authoring_harness.check_candidate",
        lambda candidate, original: CheckOutcome("passed"))


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
    assert clients[0].options.tools == clients[0].options.allowed_tools == ["StructuredOutput"]
    assert clients[0].options.permission_mode == "plan"
    assert clients[0].options.setting_sources == []
    from sap_business_agents_platform.workbuddy_prompts import AGENT_FEEDBACK_OUTPUT_SCHEMA
    assert clients[0].options.extra_args["print"] is None
    assert clients[0].options.extra_args["strict-mcp-config"] is None
    assert json.loads(clients[0].options.extra_args["json-schema"]) == AGENT_FEEDBACK_OUTPUT_SCHEMA
    assert clients[0].closed and "synthetic-secret" not in clients[0].prompt
    assert events[0][0] == "agent_runtime_turn_started" and events[-1][0] == "agent_runtime_turn_completed"
    assert planner.feedback_capabilities() == {"stream_events": True, "resume": False, "steer": False, "image_input": False}
    if action == "clarify":
        assert result["clarification"]["answer_mode"] == "confirm"
    if action == "revise_agent":
        assert result["edits"][0]["path"] == "/manifest/title/zh"


def test_existing_login_probe_uses_locked_cli_and_never_returns_credentials(monkeypatch):
    from sap_business_agents_platform.workbuddy_worker import execute
    sdk_fixture(monkeypatch, [])
    captured = []
    class Transport:
        def __init__(self, *, options):
            self.options, self.closed = options, False
            captured.append(self)
        async def connect(self):
            pass
        async def write(self, message):
            assert json.loads(message)["request"]["subtype"] == "initialize"
        async def read_messages(self):
            yield {"type": "control_response", "response": {"request_id": "sapba_auth_init", "subtype": "success",
                "response": {"account": {"userId": "synthetic-user", "token": "synthetic-secret"}}}}
        async def close(self):
            self.closed = True
    monkeypatch.setitem(sys.modules, "codebuddy_agent_sdk.transport", SimpleNamespace(SubprocessTransport=Transport))
    events = []
    result = asyncio.run(execute({"operation": "authentication", "payload": {}, "cli_path": "locked-cli"},
        lambda kind, **data: events.append(data)))
    assert result == {"authenticated": True, "status": "existing_login"}
    assert captured[0].options.codebuddy_code_path == "locked-cli" and captured[0].closed
    assert captured[0].options.tools == captured[0].options.setting_sources == []
    assert captured[0].options.persist_session is False
    assert "synthetic-secret" not in json.dumps([result, events])


def test_workbuddy_explain_rejects_definition_writes(tmp_path, monkeypatch):
    sdk_fixture(monkeypatch, [response("revise_agent", edits_json='[{"op":"replace","path":"/readme","value":"After"}]')])
    planner = WorkBuddyPlanner(tmp_path, "bound-model")
    with pytest.raises(WorkBuddyRuntimeError) as failure:
        asyncio.run(planner.review_agent_feedback(feedback="Explain", locale="en", package=PACKAGE, intent="explain", operation_id="explain"))
    assert failure.value.code == "agent_explanation_write_rejected"


def test_trusted_local_revision_enables_native_tools_only_after_explicit_policy(tmp_path, monkeypatch, transport_candidate_checker):
    from sap_business_agents_platform.authoring_workspace import AuthoringWorkspace
    def prepare(self, package, **_):
        self.source.mkdir(parents=True)
    monkeypatch.setattr(AuthoringWorkspace, "prepare", prepare)
    clients = sdk_fixture(monkeypatch, [response("revise_agent", edits_json='[{"op":"replace","path":"/readme","value":"After"}]')])
    planner = WorkBuddyPlanner(tmp_path, "bound-model")
    result = asyncio.run(planner.review_agent_feedback(feedback="Revise", locale="en", package=PACKAGE,
        operation_id="trusted", tool_policy={"mode": "trusted_local"}, intent="revise"))
    assert result["package"]["readme"] == "After"
    assert result["harness"]["checks"][0]["status"] == "passed"
    assert clients[0].options.tools is None
    assert clients[0].options.permission_mode == "bypassPermissions"
    assert clients[0].options.resume is None
    async def check():
        assert isinstance(await clients[0].options.can_use_tool("Bash", {}, None), type(await clients[0].options.can_use_tool("Read", {}, None)))
        denied = await clients[0].options.can_use_tool("WebFetch", {}, None)
        assert denied.message == "tool_not_enabled_for_task"
    asyncio.run(check())


def test_trusted_feedback_uses_frozen_package_files_not_duplicate_bodies(tmp_path, monkeypatch, transport_candidate_checker):
    from sap_business_agents_platform.authoring_workspace import AuthoringWorkspace
    prepared = []
    def prepare(self, candidate, **_):
        self.source.mkdir(parents=True)
        self.write_package(candidate)
        prepared.append(self)
    monkeypatch.setattr(AuthoringWorkspace, "prepare", prepare)
    package = {**PACKAGE, "rules": "WORKSPACE_ONLY_RULE_SENTINEL",
               "readme": "WORKSPACE_ONLY_README_SENTINEL"}
    clients = sdk_fixture(monkeypatch, [response("revise_agent", edits_json='[{"op":"replace","path":"/readme","value":"After"}]')])
    planner = WorkBuddyPlanner(tmp_path, "bound-model")
    result = asyncio.run(planner.review_agent_feedback(feedback="Revise the README", locale="en", package=package,
        history=[{"pending_clarification": "Keep the saved decision"}], intent="revise",
        tool_policy={"mode": "trusted_local"}))
    assert result["package"]["readme"] == "After"
    assert result["package"]["rules"] == package["rules"]
    assert (prepared[0].agent / "rules.py").read_text(encoding="utf-8") == package["rules"]
    assert (prepared[0].agent / "README.md").read_text(encoding="utf-8") == package["readme"]
    assert package["rules"] not in clients[0].prompt and package["readme"] not in clients[0].prompt
    assert "agent-package/manifest.json" in clients[0].prompt
    assert "Keep the saved decision" in clients[0].prompt
    assert clients[0].options.tools is None  # Context projection does not remove native abilities.


def test_trusted_context_projection_keeps_original_size_gate(tmp_path, monkeypatch):
    clients = sdk_fixture(monkeypatch, [])
    planner = WorkBuddyPlanner(tmp_path, "bound-model")
    with pytest.raises(WorkBuddyRuntimeError) as failure:
        asyncio.run(planner.review_agent_feedback(feedback="Revise", locale="en",
            package={**PACKAGE, "readme": "x" * 300_001}, intent="revise", tool_policy={"mode": "trusted_local"}))
    assert failure.value.code == "agent_authoring_context_too_large"
    assert not clients


@pytest.mark.parametrize("invalid", [None, "platform", "identity", "manifest_type", "link", "timeout"])
def test_native_workspace_candidate_requires_terminal_reply_and_scope(tmp_path, monkeypatch, invalid, transport_candidate_checker):
    from sap_business_agents_platform.authoring_workspace import AuthoringWorkspace
    from sap_business_agents_platform.authoring_harness import AuthoringHarnessError
    prepared = []
    package = {**PACKAGE, "manifest": {"slug": "sample-agent", "module": "MM", "version": "0.1.0",
        "validation": {"verdict": "NOT_TESTED"}, "title": PACKAGE["manifest"]["title"]}}
    def prepare(self, candidate, **_):
        self.source.mkdir(parents=True)
        self.base_files = {}
        self.write_package(candidate)
        prepared.append(self)
    monkeypatch.setattr(AuthoringWorkspace, "prepare", prepare)
    planner = WorkBuddyPlanner(tmp_path, "bound-model")
    async def turn(*_args, **_kwargs):
        workspace = prepared[-1]
        (workspace.agent / "README.md").write_text("After", encoding="utf-8")
        if invalid == "platform":
            (workspace.source / "unexpected.py").write_text("changed", encoding="utf-8")
        elif invalid == "identity":
            manifest = json.loads((workspace.agent / "manifest.json").read_text(encoding="utf-8"))
            manifest["slug"] = "different-agent"
            (workspace.agent / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        elif invalid == "manifest_type":
            (workspace.agent / "manifest.json").write_text("[]", encoding="utf-8")
        elif invalid == "link":
            raise AuthoringHarnessError("agent_harness_source_link_rejected")
        elif invalid == "timeout":
            raise WorkBuddyRuntimeError("No terminal reply.", code="workbuddy_deadline_exceeded")
        return response("revise_agent"), "native-session"
    monkeypatch.setattr(planner, "_structured_turn", turn)
    async def run():
        return await planner.review_agent_feedback(feedback="Edit the isolated README", locale="en",
            package=package, intent="revise", tool_policy={"mode": "trusted_local"})
    if invalid:
        with pytest.raises((WorkBuddyRuntimeError, AuthoringHarnessError)):
            asyncio.run(run())
        assert package["readme"] == "Before"
    else:
        result = asyncio.run(run())
        assert result["package"]["readme"] == "After"
        assert result["package"]["manifest"] == package["manifest"]
        assert package["readme"] == "Before"


def test_empty_native_candidate_is_not_accepted_without_trusted_workspace(tmp_path, monkeypatch):
    clients = sdk_fixture(monkeypatch, [response("revise_agent")])
    planner = WorkBuddyPlanner(tmp_path, "bound-model")
    with pytest.raises(WorkBuddyRuntimeError) as failure:
        asyncio.run(planner.review_agent_feedback(feedback="Revise", locale="en", package=PACKAGE))
    assert failure.value.code == "runtime_agent_feedback_invalid"
    assert clients[0].options.tools == ["StructuredOutput"]


def test_trusted_native_candidate_ignores_generated_test_cache_only(tmp_path, monkeypatch, transport_candidate_checker):
    import py_compile
    from sap_business_agents_platform.authoring_workspace import AuthoringWorkspace
    package = {"manifest": {"slug": "test-agent", "module": "Common", "version": "0.1.0", "validation": {}},
        "readme": "Before", "rules": "pass\n", "files": {"tests/test_rules.py": "assert True\n"}}
    prepared = []
    def prepare(self, value, **_):
        self.source.mkdir(parents=True)
        self.write_package(value)
        prepared.append(self)
    monkeypatch.setattr(AuthoringWorkspace, "prepare", prepare)
    planner = WorkBuddyPlanner(tmp_path, "bound-model")
    async def turn(*_, **kwargs):
        assert kwargs["native_schema"] is True
        workspace = prepared[0]
        py_compile.compile(str(workspace.agent / "files/tests/test_rules.py"), doraise=True)
        cache = workspace.agent / "files/.pytest_cache/v/cache/nodeids"
        cache.parent.mkdir(parents=True)
        cache.write_text("[]", encoding="utf-8")
        (workspace.agent / "README.md").write_text("After", encoding="utf-8")
        return response("revise_agent"), "native-session"
    monkeypatch.setattr(planner, "_structured_turn", turn)
    result = asyncio.run(planner.review_agent_feedback(feedback="Only update README", locale="en",
        package=package, intent="revise", tool_policy={"mode": "trusted_local"}))
    assert result["package"] == {**package, "readme": "After"}
    assert list((prepared[0].agent / "files/tests/__pycache__").glob("*.pyc"))
    assert package["readme"] == "Before"


def test_workbuddy_schema_repair_keeps_system_guard_and_only_its_own_session(tmp_path, monkeypatch):
    # Other WorkBuddy operations retain their existing prompt-only repair path.
    from sap_business_agents_platform.workbuddy_prompts import AGENT_FEEDBACK_OUTPUT_SCHEMA
    clients = sdk_fixture(monkeypatch, [{"invalid": True}, response()])
    planner = WorkBuddyPlanner(tmp_path, "bound-model")
    result, _ = asyncio.run(planner._structured_turn("Explain", AGENT_FEEDBACK_OUTPUT_SCHEMA,
        thread_id=None, system_prompt="No tools or production changes."))
    assert result["action"] == "reply"
    assert clients[0].options.resume is None
    assert clients[1].options.resume is None  # Continuation uses platform context, not unverified SDK resume.
    assert "Previous response" in clients[1].prompt
    assert clients[1].options.system_prompt == clients[0].options.system_prompt
    assert all(client.closed for client in clients)
    assert all("json-schema" not in client.options.extra_args for client in clients)


@pytest.mark.parametrize("intent,mode", [("explain", None), ("revise", "trusted_local")])
def test_native_feedback_invalid_terminal_is_not_replayed(tmp_path, monkeypatch, intent, mode):
    from sap_business_agents_platform.authoring_workspace import AuthoringWorkspace
    monkeypatch.setattr(AuthoringWorkspace, "prepare", lambda self, *_, **__: self.source.mkdir(parents=True))
    clients = sdk_fixture(monkeypatch, [{"invalid": True}, response()])
    planner = WorkBuddyPlanner(tmp_path, "bound-model")
    with pytest.raises(WorkBuddyRuntimeError) as failure:
        asyncio.run(planner.review_agent_feedback(feedback="Explain or revise", locale="en", package=PACKAGE,
            intent=intent, tool_policy={"mode": mode} if mode else None))
    assert failure.value.code == "workbuddy_structured_output_invalid"
    assert len(clients) == 1 and clients[0].closed
    assert PACKAGE["readme"] == "Before"


def test_native_feedback_rejects_legacy_worker_output(tmp_path, monkeypatch):
    sdk_fixture(monkeypatch, [])
    planner = WorkBuddyPlanner(tmp_path, "bound-model")
    calls = []
    async def run(**kwargs):
        calls.append(kwargs)
        return {"text": json.dumps(response())}
    monkeypatch.setattr(planner.supervisor, "run", run)
    with pytest.raises(WorkBuddyRuntimeError) as failure:
        asyncio.run(planner.review_agent_feedback(feedback="Explain", locale="en", package=PACKAGE, intent="explain"))
    assert failure.value.code == "workbuddy_structured_output_missing"
    assert len(calls) == 1 and calls[0]["payload"]["output_schema"]["type"] == "object"


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


@pytest.mark.parametrize("intent,action", [("explain", "explain"), ("revise", "revise")])
def test_workbuddy_workflow_v2_preserves_canonical_bindings_and_history(tmp_path, monkeypatch, intent, action):
    from sap_business_agents_platform.authoring_workspace import AuthoringWorkspace
    monkeypatch.setattr(AuthoringWorkspace, "prepare", lambda self, *_, **__: self.source.mkdir(parents=True))
    workflow = {"id": "test", "version": "1.0.0", "steps": [{"agentId": "fixed", "agentVersion": "0.1.0", "agentDigest": "frozen"}]}
    result = {"action": action, "answer": "Done", "question": "", "workflow_json": json.dumps(workflow) if action == "revise" else "null"}
    clients = sdk_fixture(monkeypatch, [result])
    planner = WorkBuddyPlanner(tmp_path, "bound-model", runtime_snapshot={"provider_id": "workbuddy", "model": "bound-model"})
    cleanup = {}
    output = asyncio.run(planner.author_workflow_v2(workflow=workflow, message="Explain", intent=intent,
        execution_mode="full_access", history=[{"message": "Keep saved pins"}], catalog={}, references=[],
        emit=lambda *_: None, cleanup_state=cleanup, request_id="owned-workflow-v2"))
    assert output["workflow"] == (workflow if intent == "revise" else None)
    assert cleanup["complete"] is True
    assert clients[0].options.resume is None and clients[0].options.setting_sources == []
    assert clients[0].options.permission_mode == "bypassPermissions"
    from sap_business_agents_platform.workbuddy_authoring import OUTPUT_SCHEMA
    assert json.loads(clients[0].options.extra_args["json-schema"]) == OUTPUT_SCHEMA
    assert clients[0].options.allowed_tools == ["StructuredOutput"]
    assert "Keep saved pins" in clients[0].prompt


def test_workbuddy_workflow_restricted_mode_rejects_before_sdk_start(tmp_path, monkeypatch):
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
    clients = sdk_fixture(monkeypatch, [])
    planner = WorkBuddyPlanner(tmp_path, "bound-model")
    with pytest.raises(WorkBuddyError, match="restricted_unverified"):
        asyncio.run(planner.author_workflow_v2(workflow={}, message="Explain", intent="explain", execution_mode="restricted",
            history=[], catalog={}, references=[], emit=lambda *_: None, cleanup_state={}))
    assert not clients
