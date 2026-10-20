"""User-invoked authentication only; run with the verified independent Python.

The pinned SDK's auth.py defines the control protocol. Its transport needs an
explicit --print for the pinned CLI, supplied here without modifying the SDK.
No model prompt, tools, user settings, platform state or account details escape.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import webbrowser
import importlib.util
from pathlib import Path
from contextlib import suppress
from urllib.parse import urlsplit


class LoginError(Exception):
    pass


def authentication_failure(value):
    path = Path(__file__).resolve().parents[1] / 'src/sap_business_agents_platform/workbuddy_auth_signals.py'
    spec = importlib.util.spec_from_file_location('sapba_auth_signals', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.authentication_failure(value, authentication_scope=True)


def isolate_environment() -> None:
    # Do not pass .env credentials, API keys, endpoints or personal MCP overrides.
    allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "APPDATA",
               "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PATH"}
    clean = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    os.environ.clear()
    os.environ.update(clean, PYTHONUTF8="1", DISABLE_AUTOUPDATER="1")


def has_account(value: dict) -> bool:
    account = value.get("account") or {}
    return isinstance(account, dict) and all(
        isinstance(account.get(key), str) and bool(account[key]) for key in ("userId", "token"))


async def send(transport, request_id: str, request: dict) -> None:
    await transport.write(json.dumps({"type": "control_request", "request_id": request_id,
                                     "request": request}))


async def read_response(reader, request_id: str) -> dict:
    async for message in reader:
        response = message.get("response", {})
        if message.get("type") == "control_response" and response.get("request_id") == request_id:
            if response.get("subtype") == "error":
                raise LoginError(authentication_failure(response.get('error')) or "workbuddy_login_protocol_rejected")
            value = response.get("response") or {}
            if not isinstance(value, dict):
                raise LoginError("workbuddy_login_protocol_invalid")
            return value
    raise LoginError("workbuddy_login_connection_closed")


async def acknowledge(transport, request_id: str, value: dict) -> None:
    await transport.write(json.dumps({"type": "control_response", "response": {
        "subtype": "success", "request_id": request_id, "response": value}}))


def open_login_url(url: str, *, no_browser: bool) -> None:
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username
                 and not parsed.password and not any(ord(char) < 32 for char in url))
    except ValueError:
        valid = False
    if not valid:
        raise LoginError("workbuddy_login_url_invalid")
    opened = False
    if not no_browser:
        with suppress(OSError, webbrowser.Error):
            opened = webbrowser.open(url)
    if not opened:
        print("Open this temporary sign-in URL locally. Do not share it or paste it into chat:", flush=True)
        print(url, flush=True)  # Local user console only; never platform logs/state.
    print("Complete browser sign-in. Waiting up to 300 seconds; Ctrl+C cancels.", flush=True)


async def run_authentication(make_transport, *, check_only: bool, method_id: str,
                             no_browser: bool, startup_seconds: float = 30,
                             login_seconds: float = 300, emit=None, environment=None, auth_hosts=None,
                             settle_account: bool = False, observation=None) -> dict:
    scope = {"environment": environment, "endpoint": None} if environment is not None else {}
    transport = make_transport()
    try:
        async with asyncio.timeout(startup_seconds):
            await transport.connect()
            reader = transport.read_messages().__aiter__()
            # The pinned CLI initialize handler reads currentSessionSubject
            # without awaiting AuthenticationManager.initialized. Re-observe
            # within this *same* bounded session; never start OAuth or a prompt.
            initialized, confirmed = {}, None
            for attempt, delay in enumerate((0, .25, .75, 1.5) if settle_account else (0,)):
                if delay:
                    await asyncio.sleep(delay)
                before = observation() if observation else None
                request_id = "login_init" if attempt == 0 else "login_init_settle_" + str(attempt)
                await send(transport, request_id, {"subtype": "initialize", "hasPrompt": False, **scope})
                initialized = await read_response(reader, request_id)
                after = observation() if observation else None
                if before is not None and (after is None or before['context_digest'] != after['context_digest']):
                    raise LoginError('runtime_authentication_context_changed')
                if has_account(initialized) and before == after:
                    confirmed = after
                    break
            if has_account(initialized) and observation and confirmed is None:
                raise LoginError('runtime_authentication_context_changed')
            if has_account(initialized):
                if emit is None:
                    print("WorkBuddy login confirmed (existing CLI session).", flush=True)
                account = initialized["account"]
                result = {"authenticated": True, "identity": account.get("email") or account.get("userId"),
                          "method": "browser", "source": "official_cli"}
                if confirmed is not None:
                    result['authentication_observation'] = confirmed
                return result
            if check_only:
                failure = LoginError("workbuddy_login_required")
                account = initialized.get('account')
                failure.authentication_evidence = ('initialize_account_incomplete' if isinstance(account, dict) and account
                                                   else 'initialize_account_absent')
                raise failure
            await send(transport, "login_start", {"subtype": "authenticate", "methodId": method_id, **scope})
            async for message in reader:
                if message.get("type") == "control_response":
                    response = message.get("response", {})
                    if response.get("request_id") == "login_start" and response.get("subtype") == "error":
                        raise LoginError("workbuddy_login_method_rejected")
                if message.get("type") != "control_request":
                    continue
                request = message.get("request", {})
                if request.get("subtype") != "auth_url_callback":
                    continue
                url = (request.get("authState") or {}).get("authUrl")
                if not isinstance(url, str) or not url:
                    raise LoginError("workbuddy_login_url_missing")
                parsed = urlsplit(url)
                if auth_hosts is not None and (parsed.scheme != "https" or parsed.hostname not in auth_hosts
                        or parsed.username or parsed.password or parsed.port not in {None, 443}
                        or any(ord(char) < 32 for char in url)):
                    raise LoginError("workbuddy_login_url_site_mismatch")
                await acknowledge(transport, message["request_id"], {"received": True})
                break
            else:
                raise LoginError("workbuddy_login_connection_closed")
        if emit is None:
            open_login_url(url, no_browser=no_browser)
        else:
            emit("waiting", {"auth_url": url})
        async with asyncio.timeout(login_seconds):
            async for message in reader:
                if message.get("type") != "control_request":
                    continue
                request = message.get("request", {})
                if request.get("subtype") != "auth_result_callback":
                    continue
                await acknowledge(transport, message["request_id"], {"handled": True})
                userinfo = request.get("userinfo") or {}
                # SDK callback UserInfo.token is optional. Never require the
                # callback to expose it; the fresh CLI verifies its own cache.
                if (request.get("success") is not True or not isinstance(userinfo, dict)
                        or not isinstance(userinfo.get("userId"), str) or not userinfo["userId"]):
                    raise LoginError("workbuddy_login_failed")
                break
            else:
                raise LoginError("workbuddy_login_connection_closed")
    finally:
        async with asyncio.timeout(10):
            await transport.close()
    # A successful callback alone is not proof that the next platform probe can
    # reuse the login. Confirm it in a fresh, no-prompt CLI process.
    try:
        return await run_authentication(make_transport, check_only=True, method_id=method_id,
                                 no_browser=True, startup_seconds=startup_seconds, emit=emit, environment=environment, auth_hosts=auth_hosts)
    except LoginError as exc:
        if str(exc) == "workbuddy_login_required":
            raise LoginError("workbuddy_login_not_persisted") from None
        raise


async def run_logout(make_transport, *, environment="external") -> None:
    """Pinned SDK logout control protocol; never read or delete credential files."""
    transport = make_transport()
    try:
        await transport.connect()
        reader = transport.read_messages().__aiter__()
        await send(transport, "logout_init", {"subtype": "initialize", "hasPrompt": False, "environment": environment, "endpoint": None})
        await read_response(reader, "logout_init")
        await send(transport, "logout", {"subtype": "logout", "environment": environment, "endpoint": None})
        await read_response(reader, "logout")
    finally:
        async with asyncio.timeout(10):
            await transport.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Authenticate the pinned WorkBuddy CLI without a model task.")
    parser.add_argument("--cli-path", required=True)
    parser.add_argument("--method-id", default="external")  # Pinned SDK authenticate() default.
    parser.add_argument("--sdk-environment", required=True, choices=["internal", "external"])
    parser.add_argument("--auth-host", action="append", required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    isolate_environment()
    try:
        # Deliberately lazy: platform imports and offline tests do not load SDK.
        from codebuddy_agent_sdk import CodeBuddyAgentOptions
        from codebuddy_agent_sdk.transport import SubprocessTransport
        sdk_env = {"CODEBUDDY_INTERNET_ENVIRONMENT": "internal"} if args.sdk_environment == "internal" else {}

        with tempfile.TemporaryDirectory(prefix="sapba-workbuddy-login-") as directory:
            def make_transport():
                return SubprocessTransport(options=CodeBuddyAgentOptions(
                    codebuddy_code_path=args.cli_path, cwd=directory, tools=[],
                    setting_sources=[], persist_session=False, env=sdk_env,
                    extra_args={"print": None, "strict-mcp-config": None}))
            asyncio.run(run_authentication(make_transport, check_only=args.check_only,
                                           method_id=args.method_id, no_browser=args.no_browser, environment=args.sdk_environment, auth_hosts=args.auth_host))
        return 0
    except LoginError as exc:
        print(str(exc), file=sys.stderr)
    except TimeoutError:
        print("workbuddy_login_timeout", file=sys.stderr)
    except KeyboardInterrupt:
        print("workbuddy_login_cancelled", file=sys.stderr)
    except Exception:
        # SDK exceptions may contain account data or URLs; never print them.
        print("workbuddy_login_unavailable", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
