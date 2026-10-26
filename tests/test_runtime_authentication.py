"""Offline account operations: no actual login/logout, SDK probes or SAP."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from sap_business_agents_platform.runtime_authentication import RuntimeAuthenticationService, error
from sap_business_agents_platform.runtime_authentication_adapters import safe_auth_url
from sap_business_agents_platform.runtime_authentication_adapters import OfficialAuthenticationAdapter
from sap_business_agents_platform.runtime import RuntimeRouter
from sap_business_agents_platform.sdk_manager import SDKManager
from sap_business_agents_platform.database import RunStore

ROOT = Path(__file__).resolve().parents[1]


class Adapter:
    account_url = "https://chatgpt.com/"
    usage_url = None
    configuration_url = "https://learn.chatgpt.com/docs/auth"
    cleanup_complete = True
    def __init__(self):
        self.identity = "user@example.test"
        self.calls = []
        self.wait = False
        self.fail = False
        self.method = "browser"
    def capabilities(self):
        return dict(check=True, login=True, logout=True, switch_account=True)
    async def check(self):
        self.calls.append("check")
        if self.fail:
            raise error("runtime_authentication_provider_failed")
        return dict(authenticated=True, identity=self.identity, method=self.method, source="official_cli")
    async def execute(self, action, event):
        self.calls.append(action)
        if self.wait:
            event("waiting", {"auth_url": "https://auth.openai.com/login?private-state=123"})
            await asyncio.Event().wait()
        return dict(authenticated=action != "logout", identity=self.identity if action != "logout" else None,
                    method="browser", source="official_cli")


def service(tmp_path, **kwargs):
    adapter = Adapter()
    return RuntimeAuthenticationService(tmp_path, {"codex": adapter, "workbuddy": Adapter()}, **kwargs), adapter


def test_view_is_read_only_masked_and_quota_unknown(tmp_path):
    auth, adapter = service(tmp_path)
    assert auth.view("codex")["status"] == "not_checked"
    assert adapter.calls == []
    asyncio.run(auth.check("codex"))
    assert auth.view("codex")["identity"] == "us***@example.test"
    assert auth.view("codex")["usage_status"] == "not_checked"
    assert "user@example.test" not in auth.path.read_text()


def test_workbuddy_enable_and_default_model_cannot_bypass_authentication_operation(tmp_path):
    async def scenario():
        auth, _ = service(tmp_path)
        adapter = auth.adapters["workbuddy"]
        adapter.wait = True
        calls = []
        async def default_model(model):
            calls.append(("model", model))
        manager = SDKManager.__new__(SDKManager)
        manager.authentication_service = auth
        manager.workbuddy = SimpleNamespace(set_enabled=lambda enabled: calls.append(("enable", enabled)),
                                           set_default_model=default_model)
        operation = await auth.start("workbuddy", "login", "guard-workbuddy")
        with pytest.raises(Exception) as enable_error:
            manager.set_enabled("workbuddy", True)
        assert enable_error.value.code == "runtime_authentication_operation_active"
        with pytest.raises(Exception) as model_error:
            await manager.set_default_model("workbuddy", "test-model")
        assert model_error.value.code == "runtime_authentication_operation_active"
        assert calls == []
        await auth.cancel("workbuddy", operation["operation_id"])
        await auth.close()
    asyncio.run(scenario())


def test_account_change_not_failed_check_invalidates_binding(tmp_path):
    changed = []
    auth, adapter = service(tmp_path, changed=lambda p, a: changed.append(a["changed"]))
    asyncio.run(auth.check("codex"))
    original = auth.bind("codex", {})
    adapter.fail = True
    asyncio.run(auth.check("codex"))
    assert auth.view("codex")["status"] == "check_failed"
    adapter.fail = False
    asyncio.run(auth.check("codex"))
    assert auth.bind("codex", {}) == original
    adapter.identity = "us-other@example.test"  # Same masked label, different real identity.
    asyncio.run(auth.check("codex"))
    with pytest.raises(Exception, match="Authentication") as caught:
        auth.assert_binding("codex", original)
    assert caught.value.code == "runtime_authentication_binding_changed"
    assert changed == [False, False, False, True]


def test_unchecked_legacy_still_works_but_signed_out_cannot_dispatch(tmp_path):
    async def scenario():
        auth, adapter = service(tmp_path)
        async with auth.use("codex", auth.bind("codex", {})): pass
        op = await auth.start("codex", "logout", "request", confirmed=True)
        await auth.tasks[op["operation_id"]]
        with pytest.raises(Exception) as blocked:
            async with auth.use("codex", auth.bind("codex", {})): pass
        assert blocked.value.code == "runtime_authentication_check_required"
        # Installation is allowed, but still mutually exclusive with auth.
        async with auth.use("codex", auth.bind("codex", {}), require_authenticated=False): pass
    asyncio.run(scenario())


@pytest.mark.parametrize("revision", [False, "0", -1])
def test_authentication_binding_is_an_integer_not_a_coerced_value(tmp_path, revision):
    auth, _ = service(tmp_path)
    with pytest.raises(Exception) as caught:
        auth.assert_binding("codex", {"authentication_binding_revision": revision})
    assert caught.value.code == "runtime_authentication_binding_invalid"


def test_confirm_idempotency_and_provider_independence(tmp_path):
    async def scenario():
        auth, adapter = service(tmp_path)
        with pytest.raises(Exception) as denied:
            await auth.start("codex", "logout", "request")
        assert denied.value.code == "runtime_authentication_confirmation_required"
        op = await auth.start("codex", "logout", "request", confirmed=True)
        assert op == await auth.start("codex", "logout", "request", confirmed=True)
        with pytest.raises(Exception) as conflict:
            await auth.start("codex", "login", "request")
        assert conflict.value.code == "runtime_authentication_request_conflict"
        await auth.tasks[op["operation_id"]]
        assert auth.operation("codex", op["operation_id"])["status"] == "completed"
        assert auth.view("workbuddy")["revision"] == 0
        assert adapter.calls == ["logout"]
        with pytest.raises(Exception):
            auth.operation("workbuddy", op["operation_id"])
    asyncio.run(scenario())


def test_waiting_url_memory_only_cancel_and_no_late_success(tmp_path):
    async def scenario():
        auth, adapter = service(tmp_path)
        adapter.wait = True
        op = await auth.start("codex", "login", "request")
        await asyncio.sleep(0)
        shown = auth.operation("codex", op["operation_id"])
        assert shown["status"] == "waiting_authentication"
        assert "private-state" in shown["auth_url"]
        assert "private-state" not in auth.path.read_text()
        with pytest.raises(Exception):
            auth.bind("codex", {})
        cancelled = await auth.cancel("codex", op["operation_id"])
        assert cancelled["status"] == "cancelled"
        assert "auth_url" not in cancelled
        assert auth.view("codex")["authenticated"] is None
        restored = RuntimeAuthenticationService(tmp_path, {"codex": adapter})
        assert restored.operation("codex", op["operation_id"])["status"] == "cancelled"
    asyncio.run(scenario())


def test_cancelling_queued_operation_does_not_change_account(tmp_path):
    async def scenario():
        auth, adapter = service(tmp_path)
        await auth.check("codex")
        op = await auth.start("codex", "logout", "request", confirmed=True)
        outcome = await auth.cancel("codex", op["operation_id"])
        assert outcome["status"] == "cancelled" and outcome["cleanup_complete"]
        assert auth.view("codex")["authenticated"] is True
        assert auth.view("codex")["revision"] == 0
        assert adapter.calls == ["check"]
    asyncio.run(scenario())


def test_interruption_between_auth_commit_and_model_invalidation_is_rechecked(tmp_path):
    attempts = []
    def projection(provider, account):
        attempts.append(account["changed"])
        if len(attempts) == 1:
            raise error("runtime_authentication_projection_failed")
    auth, adapter = service(tmp_path, changed=projection)
    async def scenario():
        op = await auth.start("codex", "logout", "request", confirmed=True)
        await auth.tasks[op["operation_id"]]
        assert auth.view("codex")["status"] == "check_failed"
        restored = RuntimeAuthenticationService(tmp_path, {"codex": adapter}, changed=projection)
        with pytest.raises(Exception):
            restored.assert_binding("codex", restored.bind("codex", {}))
        await restored.check("codex")
        assert restored.view("codex")["status"] == "authenticated"
        assert attempts[-1] is True
    asyncio.run(scenario())


def test_business_lease_and_database_blocker_prevent_mutation(tmp_path):
    async def scenario():
        auth, adapter = service(tmp_path)
        async with auth.use("codex", auth.bind("codex", {})):
            with pytest.raises(Exception) as denied:
                await auth.start("codex", "login", "request")
            assert denied.value.code == "runtime_authentication_busy"
        assert not adapter.calls
    asyncio.run(scenario())
    store = RunStore(tmp_path / "test.db")
    assert store.runtime_authentication_blockers("codex") == []
    with store._connect() as connection:
        connection.execute("INSERT INTO runs(run_id,mode,status,input_json,created_at,runtime_json) VALUES(?,?,?,?,?,?)",
            ("test", "free_query", "queued", "{}", "today", '{"provider_id":"codex"}'))
    assert store.runtime_authentication_blockers("codex")[0]["task_id"] == "test"
    assert store.runtime_authentication_blockers("workbuddy") == []


def test_cleanup_pending_blocks_until_owned_pid_no_longer_exists(tmp_path, monkeypatch):
    auth, adapter = service(tmp_path)
    auth.state["operations"]["auth_test"] = dict(operation_id="auth_test", provider_id="codex", request_id="request",
        fingerprint="x", status="running", pid=123, process_identity="start", created_at="today")
    auth._save()
    monkeypatch.setattr("sap_business_agents_platform.runtime_authentication.process_identity", lambda pid: "start")
    restored = RuntimeAuthenticationService(tmp_path, {"codex": adapter})
    assert restored.operation("codex", "auth_test")["status"] == "cleanup_pending"
    monkeypatch.setattr("sap_business_agents_platform.runtime_authentication.process_identity", lambda pid: None)
    asyncio.run(restored.check("codex"))
    assert restored.operation("codex", "auth_test")["status"] == "interrupted"
    assert restored.view("codex")["authenticated"] is True


def test_external_configuration_cannot_be_logged_out(tmp_path):
    auth, adapter = service(tmp_path)
    adapter.method = "external_key"
    asyncio.run(auth.check("codex"))
    assert not auth.view("codex")["capabilities"]["logout"]
    with pytest.raises(Exception) as rejected:
        asyncio.run(auth.start("codex", "logout", "request", confirmed=True))
    assert rejected.value.code == "runtime_authentication_external_configuration"


@pytest.mark.parametrize("url", ["javascript:evil", "http://auth.openai.com/", "https://auth.openai.com.evil.test/",
    "https://user:secret@auth.openai.com/", "https://auth.openai.com/login\nsecret", "https://auth.openai.com:9999/", "https://[broken"])
def test_login_link_allowlist(url):
    assert not safe_auth_url("codex", url)
    assert safe_auth_url("codex", "https://auth.openai.com/oauth?state=memory-only")


def load_helper():
    spec = importlib.util.spec_from_file_location("runtime_auth_helper", ROOT / "scripts/runtime-authentication-helper.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def test_codex_adapter_uses_official_protocol_not_auth_files(monkeypatch):
    calls = []
    class Codex:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): calls.append("close")
        async def logout(self): calls.append("logout")
        async def account(self): return SimpleNamespace(account=None)
    monkeypatch.setitem(sys.modules, "openai_codex", SimpleNamespace(AsyncCodex=Codex))
    helper = load_helper()
    events = []
    monkeypatch.setattr(helper, "emit", lambda *v: events.append(v))
    assert asyncio.run(helper.codex("logout"))["authenticated"] is False
    assert calls == ["logout", "close"]
    assert events == [("signed_out", {})]


def test_operation_timeout_does_not_claim_logout_or_success(tmp_path, monkeypatch):
    monkeypatch.setattr("sap_business_agents_platform.runtime_authentication.AUTH_OPERATION_SECONDS", .01)
    async def scenario():
        auth, adapter = service(tmp_path)
        adapter.wait = True
        op = await auth.start("codex", "login", "request")
        await auth.tasks[op["operation_id"]]
        outcome = auth.operation("codex", op["operation_id"])
        assert outcome["status"] == "failed"
        assert outcome["failure_code"] == "runtime_authentication_timeout"
    asyncio.run(scenario())


def test_router_preserves_auth_revision_without_changing_sdk_parameters(tmp_path):
    async def scenario():
        auth, adapter = service(tmp_path)
        manager = SimpleNamespace(authentication_service=auth, default_provider_id="codex")
        manager.runtime_snapshot = lambda p: {"model": "same-model", "reasoning_effort": "max"}
        calls = []
        class Planner:
            async def author_draft(self, *args, **kwargs):
                calls.append((args, kwargs)); return "same-output"
        router = RuntimeRouter(manager, {"codex": Planner()})
        old = auth.bind("codex", {"provider_id": "codex", "model": "same-model", "reasoning_effort": "max"})
        with router.pin_snapshot(old):
            assert await router.author_draft("unchanged", key="value") == "same-output"
        op = await auth.start("codex", "logout", "request", confirmed=True)
        await auth.tasks[op["operation_id"]]
        with pytest.raises(Exception):
            with router.pin_snapshot(old): pass
        assert calls == [(("unchanged",), {"key": "value"})]
    asyncio.run(scenario())


@pytest.mark.parametrize("contents", ["broken", "[]", "null"])
def test_damaged_auth_state_blocks_changes_without_resetting_it(tmp_path, contents):
    path = tmp_path / "authentication.json"
    path.write_text(contents, encoding="utf-8")
    auth, adapter = service(tmp_path)
    assert auth.view("codex")["failure_code"] == "runtime_authentication_state_invalid"
    assert not auth.view("codex")["capabilities"]["login"]
    assert path.read_text() == contents
    with pytest.raises(Exception):
        asyncio.run(auth.start("codex", "login", "request"))
    assert not adapter.calls


def test_router_rejects_result_after_confirmed_external_account_change(tmp_path):
    async def scenario():
        auth, adapter = service(tmp_path)
        await auth.check("codex")
        manager = SimpleNamespace(authentication_service=auth, default_provider_id="codex")
        manager.runtime_snapshot = lambda p: {"model": "same-model", "reasoning_effort": "max"}
        class Planner:
            async def author_draft(self):
                adapter.identity = "other@example.test"
                await auth.check("codex")
                return {"candidate": "must-not-apply"}
        router = RuntimeRouter(manager, {"codex": Planner()})
        old = auth.bind("codex", {"provider_id": "codex", "model": "same-model", "reasoning_effort": "max"})
        with router.pin_snapshot(old), pytest.raises(Exception) as failure:
            await router.author_draft()
        assert failure.value.code == "runtime_authentication_binding_changed"
    asyncio.run(scenario())


def test_harness_rejects_result_after_confirmed_external_account_change(tmp_path, monkeypatch):
    import sap_business_agents_platform.runtime_harness as harness
    async def scenario():
        auth, adapter = service(tmp_path)
        await auth.check("codex")
        snapshot = auth.bind("codex", {})
        async def execute(*args):
            adapter.identity = "different@example.test"
            await auth.check("codex")
            return "must-not-qualify"
        monkeypatch.setattr(harness, "_reserved_run", execute)
        controller = SimpleNamespace(authentication_service=auth,
            store=SimpleNamespace(get_run=lambda run_id: SimpleNamespace(runtime=SimpleNamespace(model_dump=lambda **kw: snapshot))))
        with pytest.raises(Exception) as failure:
            await harness.run(controller, SimpleNamespace(provider_id="codex"), "isolated", "test")
        assert failure.value.code == "runtime_authentication_binding_changed"
    asyncio.run(scenario())


@pytest.mark.parametrize("provider", ["codex", "workbuddy"])
@pytest.mark.parametrize("external", [False, True])
def test_owned_adapter_handshake_environment_redaction_and_official_link(tmp_path, monkeypatch, provider, external):
    import sap_business_agents_platform.runtime_authentication_adapters as module
    calls, events = [], []
    release = {"python_path": "isolated-python", "cli_path": "locked-cli", "environment_digest": "original"}
    from sap_business_agents_platform.workbuddy_site import make_configuration
    context = make_configuration("international", 1)
    wb = SimpleNamespace(environment=SimpleNamespace(release=lambda: dict(release), state=lambda: context))
    class Job:
        def attach(self, pid): calls.append(("attach", pid))
        def terminate(self): calls.append(("terminate",))
        def empty(self): return True
        def close(self): calls.append(("close",))
    class Input:
        def write(self, value): calls.append(("stdin", value))
        async def drain(self): pass
    frames = [json.dumps({"kind": "waiting", "value": {"auth_url": "https://auth.openai.com/login" if provider == "codex" else "https://www.codebuddy.ai/login"}}).encode() + b"\n",
              json.dumps({"kind": "result", "value": {"authenticated": True, "method": "browser", "source": "official_cli"}}).encode() + b"\n"]
    class Output:
        async def readline(self): return frames.pop(0) if frames else b""
    class Process:
        pid = 12345
        stdin, stdout, returncode = Input(), Output(), None
        async def wait(self): self.returncode = 0; return 0
    async def spawn(*command, **options): calls.append(("spawn", command, options)); return Process()
    monkeypatch.setattr(module, "WindowsJob", Job)
    monkeypatch.setattr(module, "process_identity", lambda pid: "created")
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(module.webbrowser, "open", lambda url: True)
    environment = {"PATH": "safe-path", "CODEX_HOME": "original-cache", "SAP_PASSWORD": "secret", "MAIL_PASSWORD": "secret"}
    key_name = "OPENAI_API_KEY" if provider == "codex" else "CODEBUDDY_API_KEY"
    if external:
        environment[key_name] = "synthetic-provider-key"
    monkeypatch.setattr(module.os, "environ", environment)
    if hasattr(module.os, "killpg"):
        monkeypatch.setattr(module.os, "killpg", lambda *args: None)
    adapter = OfficialAuthenticationAdapter(tmp_path, provider, wb)
    if provider == "workbuddy":
        # This test mocks the helper/process and uses a synthetic environment;
        # do not weaken production user/source validation to accommodate it.
        monkeypatch.setattr(adapter, 'observation', lambda: {
            'context_digest': 'a'*64, 'source_digest': 'b'*64, 'cache_digest': 'c'*64})
    if external:
        assert adapter.capabilities()["login"] is False
        assert adapter.capabilities()["logout"] is False
    result = asyncio.run(adapter.execute("check" if external else "login", lambda k, v: events.append((k, v))))
    assert result["authenticated"] is True and adapter.cleanup_complete
    command, options = calls[0][1:]
    assert command[0] == ("isolated-python" if provider == "workbuddy" else sys.executable)
    if provider == "workbuddy":
        assert command[1:3] == ("-I", "-B")
        assert command[-6:] == ("--cli-path", "locked-cli", "--service-site", "international", "--sdk-environment", "external")
    else:
        assert command[1] == str(tmp_path / "scripts/runtime-authentication-helper.py")
        assert "-I" not in command and "-B" not in command
    assert "secret" not in json.dumps(options["env"])
    assert (options["env"].get(key_name) == "synthetic-provider-key") is external
    assert result["method"] == ("external_key" if external else "browser")
    assert "synthetic-provider-key" not in json.dumps(result)
    if external:
        assert result["source"] == "external_configuration" and result["identity"] is None
    assert ("CODEX_HOME" in options["env"]) == (provider == "codex")
    assert calls.index(("attach", 12345)) < calls.index(("stdin", b"owned\n"))
    assert [e[0] for e in events] == ["owned_process", "waiting", "waiting"]
    assert events[-1][1]["browser_opened"] is True


def test_workbuddy_login_imports_do_not_mutate_release_and_integrity_stays_enforced(tmp_path, monkeypatch):
    """Real owned subprocess with a synthetic SDK; no official auth or network."""
    import subprocess
    import venv
    from sap_business_agents_platform.workbuddy_environment import (
        WorkBuddyEnvironment, WorkBuddyError, atomic_json, digest, file_hash,
    )
    environment = WorkBuddyEnvironment(tmp_path)
    preparing = tmp_path / "preparing"
    venv.EnvBuilder(with_pip=False).create(preparing)
    python = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    # Resolve the site directory using only the isolated interpreter's stdlib.
    site = Path(subprocess.check_output([str(preparing / python), "-I", "-B", "-c",
        "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip())
    sdk = site / "codebuddy_agent_sdk"
    sdk.mkdir()
    (sdk / "__init__.py").write_text(
        "class CodeBuddyAgentOptions:\n    def __init__(self, **kwargs): pass\n", encoding="utf-8")
    (sdk / "transport.py").write_text(
        "import json\n"
        "class SubprocessTransport:\n"
        "    def __init__(self, **kwargs): self.request_id = None\n"
        "    async def connect(self): pass\n"
        "    async def close(self): pass\n"
        "    async def write(self, value): self.request_id = json.loads(value)['request_id']\n"
        "    async def read_messages(self):\n"
        "        yield {'type': 'control_response', 'response': {'request_id': self.request_id,\n"
        "            'subtype': 'success', 'response': {'account':\n"
        "            {'userId': 'synthetic-user', 'token': 'synthetic-only'}}}}\n",
        encoding="utf-8")
    (preparing / "cli.exe").write_bytes(b"synthetic-never-executed")
    (preparing / "worker.py").write_text("# synthetic\n", encoding="utf-8")
    files = {p.relative_to(preparing).as_posix(): file_hash(p)
             for p in preparing.rglob("*") if p.is_file()}
    manifest = dict(python=python, cli="cli.exe", worker="worker.py", files=files)
    key = digest(manifest)
    directory = environment.root / "releases" / key
    directory.parent.mkdir(parents=True)
    preparing.rename(directory)
    atomic_json(directory / "environment.json", {**manifest, "environment_digest": key})
    from sap_business_agents_platform.workbuddy_site import make_configuration
    atomic_json(environment.state_path, {"environment_digest": key, **make_configuration("china", 1)})
    manifest_before = (directory / "environment.json").read_bytes()
    monkeypatch.setattr("sap_business_agents_platform.runtime_authentication_adapters.os.environ",
        {k: v for k, v in __import__('os').environ.items() if k.upper() in {
            "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "APPDATA",
            "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PATH", "HOME"}})
    adapter = OfficialAuthenticationAdapter(ROOT, "workbuddy", SimpleNamespace(environment=environment))
    for action in ("check", "login"):
        result = asyncio.run(adapter.execute(action, lambda *_: None))
        assert result["authenticated"] is True and adapter.cleanup_complete
        assert environment.release()["environment_digest"] == key
        assert not list(directory.rglob("__pycache__"))
        assert (directory / "environment.json").read_bytes() == manifest_before
    # The fix prevents cache writes; it does not ignore unexpected files.
    unexpected = directory / "unexpected.pyc"
    unexpected.write_bytes(b"unexpected")
    with pytest.raises(WorkBuddyError, match="workbuddy_environment_digest_mismatch"):
        environment.release()
    assert adapter.capabilities()["login"] is False


def test_failed_job_assignment_cleans_owned_helper_without_sdk_handshake(tmp_path, monkeypatch):
    import sap_business_agents_platform.runtime_authentication_adapters as module
    calls, events = [], []
    class Job:
        def attach(self, pid): raise RuntimeError("synthetic assignment failure")
        def terminate(self): raise AssertionError("not attached")
        def empty(self): return True
        def close(self): calls.append("job_closed")
    class Process:
        pid, returncode = 12345, None
        def kill(self): calls.append("owned_helper_killed"); self.returncode = -9
        async def wait(self): return self.returncode
        stdin = SimpleNamespace(write=lambda value: calls.append("sdk_handshake"))
    async def spawn(*args, **kwargs): return Process()
    monkeypatch.setattr(module, "WindowsJob", Job)
    monkeypatch.setattr(module, "process_identity", lambda pid: "owned-start")
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(module.os, "environ", {})
    adapter = OfficialAuthenticationAdapter(tmp_path, "codex")
    with pytest.raises(RuntimeError):
        asyncio.run(adapter.execute("check", lambda kind, data: events.append((kind, data))))
    assert calls == ["owned_helper_killed", "job_closed"]
    assert events == [("owned_process", {"pid": 12345, "process_identity": "owned-start"})]
    assert adapter.cleanup_complete is True


def test_workbuddy_auth_change_keeps_operation_evidence_and_codex_state(tmp_path):
    from sap_business_agents_platform.sdk_manager import SDKManager
    from sap_business_agents_platform.workbuddy_site import make_configuration
    context = make_configuration("china", 1)
    state = {"models": {"hy4-preview": {"compatible": True}}, "capabilities": {"original": {"status": "validated"}}, "enabled": False, **context}
    class Environment:
        def release(self): return {"environment_digest": "locked"}
        def mutate(self, callback): callback(state)
        def state(self): return state
    manager = object.__new__(SDKManager)
    manager.workbuddy = SimpleNamespace(environment=Environment())
    manager._runtime_config = {"default_provider_id": "codex", "unchanged": "codex"}
    manager.apply_authentication_state("workbuddy", dict(authenticated=True, status="authenticated", revision=1,
        checked_at="today", changed=True, failure_code=None, **context))
    assert state["models"]["hy4-preview"]["compatible"] is False
    assert state["capabilities"] == {"original": {"status": "validated"}}
    assert state["enabled"] is False
    assert manager._runtime_config == {"default_provider_id": "codex", "unchanged": "codex"}
