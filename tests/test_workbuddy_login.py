"""Offline authentication protocol checks; no browser, SDK or external requests."""
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("workbuddy_login_helper", ROOT / "scripts/login-workbuddy-runtime.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def response(request_id, value, subtype="success"):
    return {"type": "control_response", "response": {
        "request_id": request_id, "subtype": subtype, "response": value}}


def callback(subtype, **value):
    return {"type": "control_request", "request_id": subtype, "request": {"subtype": subtype, **value}}


ACCOUNT = {"userId": "private-account", "token": "private-token"}
URL = "https://example.test/sign-in?state=private-state"


class FakeTransport:
    def __init__(self, messages, *, wait=False):
        self.messages = messages
        self.wait = wait
        self.writes = []
        self.connected = False
        self.closed = False

    async def connect(self):
        self.connected = True

    async def write(self, encoded):
        self.writes.append(json.loads(encoded))

    async def read_messages(self):
        for message in self.messages:
            yield message
        if self.wait:
            await asyncio.Event().wait()

    async def close(self):
        self.closed = True


def run(transports, **options):
    pending = iter(transports)
    asyncio.run(helper.run_authentication(lambda: next(pending), check_only=options.pop("check_only", False),
                method_id="external", no_browser=False, **options))


def test_existing_login_does_not_open_browser_or_send_task(monkeypatch, capsys):
    monkeypatch.setattr(helper.webbrowser, "open", lambda url: pytest.fail("must not open browser"))
    transport = FakeTransport([response("login_init", {"account": ACCOUNT})])
    run([transport])
    assert transport.closed
    assert [item["request"]["subtype"] for item in transport.writes] == ["initialize"]
    assert transport.writes[0]["request"]["hasPrompt"] is False
    output = capsys.readouterr()
    assert "confirmed" in output.out
    assert "private-" not in output.out + output.err


def test_check_only_never_initiates_oauth():
    transport = FakeTransport([response("login_init", {})])
    with pytest.raises(helper.LoginError, match="workbuddy_login_required"):
        run([transport], check_only=True)
    assert transport.closed
    assert len(transport.writes) == 1


def test_login_sends_authentication_opens_browser_and_verifies_fresh_cli(monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(helper.webbrowser, "open", lambda url: opened.append(url) or True)
    transport = FakeTransport([response("login_init", {}),
        callback("auth_url_callback", authState={"authUrl": URL}),
        callback("auth_result_callback", success=True, userinfo=ACCOUNT)])
    fresh = FakeTransport([response("login_init", {"account": ACCOUNT})])
    run([transport, fresh])
    assert opened == [URL]
    assert transport.closed and fresh.closed
    assert transport.writes[1]["request"] == {"subtype": "authenticate", "methodId": "external"}
    assert [item["response"]["response"] for item in transport.writes if item["type"] == "control_response"] == [
        {"received": True}, {"handled": True}]
    assert all(item["type"] != "user" for item in transport.writes + fresh.writes)
    assert len(fresh.writes) == 1
    output = capsys.readouterr()
    assert "confirmed" in output.out
    assert "private-" not in output.out + output.err


@pytest.mark.parametrize("messages", [[], [response("login_init", {})]])
def test_silent_cli_exit_never_counts_as_login(messages, capsys):
    transport = FakeTransport(messages)
    with pytest.raises(helper.LoginError, match="workbuddy_login_connection_closed"):
        run([transport])
    assert transport.closed
    assert "confirmed" not in capsys.readouterr().out


@pytest.mark.parametrize("success,userinfo", [(False, ACCOUNT), ("true", ACCOUNT), (True, {}),
    (True, {"userId": ""}), (True, {"token": "private-token"})])
def test_failed_callback_or_missing_identity_is_not_success(monkeypatch, success, userinfo, capsys):
    monkeypatch.setattr(helper.webbrowser, "open", lambda url: True)
    transport = FakeTransport([response("login_init", {}),
        callback("auth_url_callback", authState={"authUrl": URL}),
        callback("auth_result_callback", success=success, userinfo=userinfo,
                 error={"message": "private-sensitive-error"})])
    with pytest.raises(helper.LoginError, match="workbuddy_login_failed"):
        run([transport])
    assert transport.closed
    output = capsys.readouterr()
    assert "private-" not in output.out + output.err


def test_success_callback_without_persistent_login_is_rejected(monkeypatch):
    monkeypatch.setattr(helper.webbrowser, "open", lambda url: True)
    transport = FakeTransport([response("login_init", {}),
        callback("auth_url_callback", authState={"authUrl": URL}),
        callback("auth_result_callback", success=True, userinfo=ACCOUNT)])
    fresh = FakeTransport([response("login_init", {})])
    with pytest.raises(helper.LoginError, match="workbuddy_login_not_persisted"):
        run([transport, fresh])
    assert fresh.closed


def test_callback_need_not_disclose_token_when_fresh_cli_confirms_cache(monkeypatch):
    monkeypatch.setattr(helper.webbrowser, "open", lambda url: True)
    transport = FakeTransport([response("login_init", {}),
        callback("auth_url_callback", authState={"authUrl": URL}),
        callback("auth_result_callback", success=True, userinfo={"userId": "private-account"})])
    fresh = FakeTransport([response("login_init", {"account": ACCOUNT})])
    run([transport, fresh])
    assert fresh.closed


@pytest.mark.parametrize("url", ["http://example.test/login", "file:///secret", "javascript:alert(1)",
    "https://user:password@example.test/login", "https://example.test/login\nsecret", "https://[broken"])
def test_invalid_login_url_not_opened(monkeypatch, url):
    monkeypatch.setattr(helper.webbrowser, "open", lambda url: pytest.fail("must not open unsafe URL"))
    with pytest.raises(helper.LoginError, match="workbuddy_login_url_invalid"):
        helper.open_login_url(url, no_browser=False)


def test_manual_url_fallback_is_local_and_explicit(monkeypatch, capsys):
    monkeypatch.setattr(helper.webbrowser, "open", lambda url: False)
    helper.open_login_url(URL, no_browser=False)
    output = capsys.readouterr().out
    assert URL in output and "Do not share" in output


@pytest.mark.parametrize("login_phase", [False, True])
def test_deadlines_close_cli_and_do_not_claim_login(monkeypatch, login_phase, capsys):
    monkeypatch.setattr(helper.webbrowser, "open", lambda url: True)
    messages = ([response("login_init", {}), callback("auth_url_callback", authState={"authUrl": URL})]
                if login_phase else [])
    transport = FakeTransport(messages, wait=True)
    with pytest.raises(TimeoutError):
        run([transport], startup_seconds=0.03, login_seconds=0.03)
    assert transport.closed
    assert "confirmed" not in capsys.readouterr().out


def test_helper_strips_connection_credentials_and_custom_endpoints(monkeypatch):
    monkeypatch.setattr(helper.os, "environ", {"USERPROFILE": "safe-user", "PATH": "safe-path",
        "SAP_PASSWORD": "private-password", "CODEBUDDY_AUTH_TOKEN": "private-token",
        "CODEBUDDY_BASE_URL": "https://private-endpoint", "CODEBUDDY_CODE_PATH": "other-cli",
        "CODEX_HOME": "other-runtime"})
    helper.isolate_environment()
    assert dict(helper.os.environ) == {"USERPROFILE": "safe-user", "PATH": "safe-path",
                                     "PYTHONUTF8": "1", "DISABLE_AUTOUPDATER": "1"}


def test_helper_is_sdk_free_until_independent_interpreter_main():
    source = (ROOT / "scripts/login-workbuddy-runtime.py").read_text(encoding="utf-8")
    wrapper = (ROOT / "scripts/login-workbuddy-runtime.ps1").read_text(encoding="utf-8")
    assert 'from codebuddy_agent_sdk' not in source.split('def main()')[0]
    assert 'extra_args={"print": None, "strict-mcp-config": None}' in source
    assert 'setting_sources=[], persist_session=False' in source
    assert '& $release.python_path @loginArguments' in wrapper
    assert '& $release.cli_path --tools=' not in wrapper
    assert '"-I"' in wrapper or "'-I'" in wrapper


def test_control_protocol_error_is_redacted():
    transport = FakeTransport([response("login_init", {"message": "private-token"}, subtype="error")])
    with pytest.raises(helper.LoginError) as error:
        run([transport])
    assert str(error.value) == "workbuddy_login_protocol_rejected"
    assert transport.closed


def test_login_method_rejection_does_not_wait_for_browser(monkeypatch):
    monkeypatch.setattr(helper.webbrowser, "open", lambda url: pytest.fail("must not open browser"))
    transport = FakeTransport([response("login_init", {}),
        response("login_start", {"message": "private-endpoint"}, subtype="error")])
    with pytest.raises(helper.LoginError, match="workbuddy_login_method_rejected"):
        run([transport])
    assert transport.closed
