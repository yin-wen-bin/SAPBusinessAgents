"""Owned, no-prompt authentication subprocess. stdout is a bounded safe protocol.

Wait for ownership handshake before importing any SDK or spawning its CLI.
Never print SDK exceptions, credential objects, or raw CLI output.
"""
from __future__ import annotations
import argparse
import asyncio
import importlib.util
import json
import sys
import tempfile
import os
from pathlib import Path


def emit(kind, value):
    print(json.dumps({"kind": kind, "value": value}), flush=True)


class ExternalAuthentication(Exception):
    pass


async def codex(action):
    from openai_codex import AsyncCodex
    async with AsyncCodex() as client:
        if action != "check":
            initial = await client.account()
            if getattr(initial, "requires_openai_auth", None) is False:
                raise ExternalAuthentication()
            current = initial.account
            current = getattr(current, "root", current)
            if current and current.model_dump(mode="json").get("type") != "chatgpt":
                raise ExternalAuthentication()
        if action in {"logout", "switch_account"}:
            await client.logout()
            emit("signed_out", {})
        if action in {"login", "switch_account"}:
            handle = await client.login_chatgpt()
            emit("waiting", {"auth_url": handle.auth_url})
            try:
                result = await handle.wait()
                if not result.success:
                    raise RuntimeError()
            except BaseException:
                await handle.cancel()
                raise
        value = await client.account()
        account = value.account
        if account is not None and hasattr(account, "root"):
            account = account.root
        data = account.model_dump(mode="json") if account else {}
        return {"authenticated": bool(account), "identity": data.get("email"),
                "method": "browser" if data.get("type") in {None, "chatgpt"} else "external_key",
                "source": "official_cli"}


async def workbuddy(action, cli_path, environment, observation=None):
    spec = importlib.util.spec_from_file_location("sapba_login", Path(__file__).with_name("login-workbuddy-runtime.py"))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    from codebuddy_agent_sdk import CodeBuddyAgentOptions
    from codebuddy_agent_sdk.transport import SubprocessTransport
    sdk_env = {"CODEBUDDY_INTERNET_ENVIRONMENT": "internal"} if environment == "internal" else {}
    with tempfile.TemporaryDirectory(prefix="sapba-auth-") as directory:
        def transport():
            return SubprocessTransport(options=CodeBuddyAgentOptions(
                codebuddy_code_path=cli_path, cwd=directory, tools=[], setting_sources=[],
                persist_session=False, env=sdk_env, extra_args={"print": None, "strict-mcp-config": None}))
        if action in {"logout", "switch_account"}:
            await helper.run_logout(transport, environment=environment)
            emit("signed_out", {})
        try:
            result = await helper.run_authentication(transport, check_only=action in {"check", "logout"},
                                method_id="external", no_browser=True, emit=emit, environment=environment,
                                settle_account=action == 'check', observation=observation if action == 'check' else None)
        except helper.LoginError as exc:
            if str(exc) != "workbuddy_login_required":
                raise
            result = {"authenticated": False, "method": "browser", "source": "official_cli",
                      "authentication_evidence": getattr(exc, 'authentication_evidence', 'initialize_account_unconfirmed')}
        if action == "logout" and result["authenticated"]:
            raise RuntimeError()
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["codex", "workbuddy"], required=True)
    parser.add_argument("--action", choices=["check", "login", "logout", "switch_account"], required=True)
    parser.add_argument("--cli-path")
    parser.add_argument("--service-site", choices=["china", "international"])
    parser.add_argument("--sdk-environment", choices=["internal", "external"])
    parser.add_argument('--auth-context-protocol')
    parser.add_argument('--expected-auth-context')
    parser.add_argument('--release-digest')
    args = parser.parse_args()
    if sys.stdin.readline().strip() != "owned":
        return 1
    try:
        def current_observation():
            source = Path(__file__).resolve().parents[1] / 'src/sap_business_agents_platform/workbuddy_auth_context.py'
            spec = importlib.util.spec_from_file_location('sapba_auth_context', source)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.validate_user_environment(os.environ)
            external = any(os.environ.get(k) for k in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CODEBUDDY_API_KEY', 'CODEBUDDY_AUTH_TOKEN', 'CODEBUDDY_BASE_URL'))
            return module.observe(Path(__file__).resolve().parents[1], os.environ, args.release_digest, external=external)
        def current_context():
            return current_observation()['context_digest']
        if args.provider == 'workbuddy' and (args.auth_context_protocol != 'sapba.workbuddy.authentication/2'
                or not args.expected_auth_context or args.expected_auth_context != current_context()):
            emit('error', {'code':'runtime_authentication_context_changed'})
            return 1
        if args.provider == "workbuddy" and (args.service_site, args.sdk_environment) not in {
                ("china", "internal"), ("international", "external")}:
            raise ValueError()
        result = asyncio.run(codex(args.action) if args.provider == "codex" else workbuddy(args.action, args.cli_path, args.sdk_environment, observation=current_observation))
        if args.provider == 'workbuddy' and args.expected_auth_context != current_context():
            emit('error', {'code':'runtime_authentication_context_changed'})
            return 1
        emit("result", result)
        return 0
    except ExternalAuthentication:
        emit("error", {"code": "runtime_authentication_external_configuration"})
        return 1
    except BaseException as exc:
        if args.provider == 'workbuddy' and str(exc) in {'workbuddy_authentication_required', 'workbuddy_authentication_refresh_failed', 'runtime_authentication_context_changed'}:
            emit('error', {'code': str(exc)})
            return 1
        emit("error", {"code": "runtime_authentication_provider_failed"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
