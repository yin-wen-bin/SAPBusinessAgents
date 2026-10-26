"""Official protocol adapters, SDK-free in the API process."""
from __future__ import annotations
import asyncio
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
import webbrowser
from contextlib import suppress
from urllib.parse import urlsplit
from .runtime_authentication import error
from .workbuddy_job import WindowsJob, process_identity

AUTH_HOSTS = {"codex": ("openai.com", "chatgpt.com"),
              "workbuddy": ("codebuddy.ai", "copilot.tencent.com", "workbuddy.ai")}
EXTERNAL_ENV = {"codex": ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"),
                "workbuddy": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CODEBUDDY_API_KEY", "CODEBUDDY_AUTH_TOKEN", "CODEBUDDY_BASE_URL")}


def safe_auth_url(provider, url, *, hosts=None):
    try:
        value = urlsplit(url)
        host = value.hostname or ""
        return (isinstance(url, str) and len(url) <= 8192 and value.scheme == "https"
                and not value.username and not value.password and value.port in {None, 443}
                and not any(ord(c) < 32 for c in url)
                and any(host == h or host.endswith("." + h) for h in (hosts if hosts is not None else AUTH_HOSTS.get(provider, ()))))
    except (ValueError, TypeError):
        return False


class OfficialAuthenticationAdapter:
    def __init__(self, root, provider, workbuddy=None):
        self.root, self.provider, self.workbuddy = root, provider, workbuddy
        self.cleanup_complete = True
        self.account_url = "https://chatgpt.com/" if provider == "codex" else "https://www.codebuddy.ai/"
        self.usage_url = None  # No verified SDK quota API; don't invent balances.
        self.configuration_url = "https://learn.chatgpt.com/docs/auth" if provider == "codex" else "https://www.codebuddy.ai/docs/cli/sdk-python"
        self.external_configuration = False
        self._authentication_sources = None
        self.check_diagnostic = {}
        if provider == 'workbuddy' and (root / 'scripts/runtime-authentication-helper.py').is_file():
            from .workbuddy_auth_context import source_signature
            self._authentication_sources = source_signature(root)
        if provider not in AUTH_HOSTS:
            self.account_url = self.configuration_url = None

    def context(self):
        if self.provider != "workbuddy":
            return {}
        from .workbuddy_site import configuration
        return configuration(self.workbuddy.environment.state(), required=True)

    def site_view(self):
        from .workbuddy_site import configuration, routing
        current = configuration(self.workbuddy.environment.state())
        route = routing(current)
        return {**current, "account_url": route["account_url"], "configuration_url": route["configuration_url"]}

    def capabilities(self):
        try:
            if self.provider == "workbuddy":
                self.workbuddy.environment.release()
                self.context()
            elif self.provider == "codex":
                if not importlib.util.find_spec("openai_codex"):
                    raise ValueError()
            else:
                return {"check": False, "login": False, "logout": False, "switch_account": False,
                        "reason": "runtime_authentication_action_unavailable"}
        except Exception as exc:
            return {"check": False, "login": False, "logout": False, "switch_account": False,
                    "reason": getattr(exc, "code", "runtime_authentication_install_required")}
        external = self.external_configuration or any(os.environ.get(k) for k in EXTERNAL_ENV[self.provider])
        return {"check": True, "login": not external, "logout": not external,
                "switch_account": not external, "reason": "runtime_authentication_external_configuration" if external else None}

    async def check(self, *, context=None):
        return await self._execute("check", lambda *_: None, context=context)

    def _prepare_workbuddy(self, action, context):
        # One synchronous inspection fence. Never keep it across an await or
        # reuse it for a later check; both end-point integrity checks remain.
        from .workbuddy_inspection import inspect
        from .workbuddy_site import assert_binding, routing
        with inspect(self.workbuddy.environment):
            capabilities = self.capabilities()
            if not capabilities.get("check" if action == "check" else action):
                raise error(capabilities.get("reason"))
            release = self.workbuddy.environment.release()
            context = context or self.context()
            assert_binding(self.workbuddy.environment.state(), context)
            return release, context, routing(context), self.observation()['context_digest']

    def _verify_workbuddy(self, release, context):
        from .workbuddy_inspection import inspect
        from .workbuddy_site import assert_binding
        with inspect(self.workbuddy.environment):
            if self.workbuddy.environment.release()["environment_digest"] != release["environment_digest"]:
                raise error("runtime_authentication_environment_changed")
            assert_binding(self.workbuddy.environment.state(), context)

    def observation(self):
        if self.provider != 'workbuddy':
            return None
        from .workbuddy_auth_context import observe, validate_user_environment, AuthenticationContextError
        try:
            validate_user_environment(os.environ)
        except AuthenticationContextError:
            raise error('runtime_authentication_context_changed') from None
        value = observe(self.root, os.environ, self.workbuddy.environment.state().get('environment_digest'),
                        external=any(os.environ.get(k) for k in EXTERNAL_ENV[self.provider]))
        if self._authentication_sources is not None and value['source_digest'] != self._authentication_sources:
            raise error('runtime_authentication_context_changed')
        return value

    async def execute(self, action, event, *, context=None):
        return await self._execute(action, event, context=context)

    async def _execute(self, action, event, *, context=None):
        checking = self.provider == 'workbuddy' and action == 'check'
        if checking:
            self.check_diagnostic = {'phase': 'preparing'}
        release, expected_context = None, None
        route = None
        if self.provider == 'workbuddy':
            # Full installation/source hashing must not block the API loop
            # which drains the official response and enforces its deadline.
            release, context, route, expected_context = await asyncio.to_thread(
                self._prepare_workbuddy, action, context)
        elif not self.capabilities().get("check" if action == "check" else action):
            raise error(self.capabilities().get("reason"))
        python = release["python_path"] if release else sys.executable
        # WorkBuddy releases are immutable, including their file inventory.
        # Match the existing worker/console-login launcher: isolated imports
        # must not create bytecode caches inside the verified installation.
        # Keep the Codex launcher and its official configuration unchanged.
        command = [str(python), *(["-I", "-B"] if release else []),
                   str(self.root / "scripts/runtime-authentication-helper.py"),
                   "--provider", self.provider, "--action", action]
        if release:
            command += ['--auth-context-protocol', 'sapba.workbuddy.authentication/2']
            if (self.root / 'scripts/runtime-authentication-helper.py').is_file():
                command += ['--expected-auth-context', expected_context,
                            '--release-digest', release['environment_digest']]
            command += ["--cli-path", release["cli_path"], "--service-site", context["service_site"],
                        "--sdk-environment", route["sdk_environment"]]
        allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "APPDATA",
                   "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PATH", "HOME"}
        if self.provider == "codex":
            allowed.add("CODEX_HOME")  # Preserve current official cache/config selection.
        else:
            # Match official console login; HOME must not select another cache.
            allowed.discard('HOME')
        external = action == "check" and any(os.environ.get(k) for k in EXTERNAL_ENV[self.provider])
        if external:
            # Preserve only this SDK's externally managed authentication for
            # its read-only account check. Never copy it into prompts/state,
            # and never substitute the unrelated cached browser account.
            allowed.update(EXTERNAL_ENV[self.provider])
        env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
        env.update(PYTHONUTF8="1", DISABLE_AUTOUPDATER="1")
        job, process, result, attached = WindowsJob(), None, None, False
        self.cleanup_complete = False
        try:
            if checking:
                self.check_diagnostic = {'phase': 'starting_official_process'}
            process = await asyncio.create_subprocess_exec(*command, cwd=self.root, env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                limit=65536, **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}))
            event("owned_process", {"pid": process.pid, "process_identity": process_identity(process.pid)})
            job.attach(process.pid)
            attached = True
            process.stdin.write(b"owned\n")
            await process.stdin.drain()
            if checking:
                self.check_diagnostic = {'phase': 'waiting_official_result'}
            total = 0
            while line := await process.stdout.readline():
                total += len(line)
                if total > 1048576:
                    raise error("runtime_authentication_output_limit")
                frame = json.loads(line)
                kind, data = frame.get("kind"), frame.get("value", {})
                if result is not None:
                    raise error("runtime_authentication_protocol_invalid")
                if kind == "waiting":
                    url = data.get("auth_url")
                    if not safe_auth_url(self.provider, url, hosts=route["auth_hosts"] if route else None):
                        raise error("runtime_authentication_url_invalid")
                    # Publish fallback immediately; a slow OS browser launcher
                    # must not hide the login link or stall cancellation.
                    event("waiting", {"auth_url": url, "browser_opened": False})
                    opened = False
                    with suppress(OSError, webbrowser.Error, TimeoutError):
                        opened = await asyncio.wait_for(asyncio.to_thread(webbrowser.open, url), 2)
                    if opened:
                        event("waiting", {"auth_url": url, "browser_opened": True})
                elif kind == "signed_out":
                    event(kind, {})
                elif kind == "result":
                    result = {k: data.get(k) for k in ("authenticated", "identity", "method", "source")}
                    if self.provider == 'workbuddy' and data.get('authentication_observation') is not None:
                        proof = data['authentication_observation']
                        import re
                        if (not isinstance(proof, dict) or set(proof) != {'context_digest','cache_digest','source_digest'}
                                or any(not isinstance(proof.get(k), str) or not re.fullmatch('[0-9a-f]{64}',proof[k]) for k in ('context_digest','source_digest'))
                                or proof['cache_digest'] is not None and (not isinstance(proof['cache_digest'],str) or not re.fullmatch('[0-9a-f]{64}',proof['cache_digest']))):
                            raise error('runtime_authentication_protocol_invalid')
                        result['authentication_observation'] = proof
                    if self.provider == 'workbuddy' and data.get('authentication_evidence') in {
                            'initialize_account_absent', 'initialize_account_incomplete', 'initialize_account_unconfirmed'}:
                        result['authentication_evidence'] = data['authentication_evidence']
                elif kind == "error":
                    if data.get("code") == "runtime_authentication_external_configuration":
                        self.external_configuration = True
                        raise error("runtime_authentication_external_configuration")
                    if self.provider == 'workbuddy' and data.get('code') in {
                            'workbuddy_authentication_required', 'workbuddy_authentication_refresh_failed',
                            'runtime_authentication_context_changed'}:
                        raise error(data['code'])
                    raise error("runtime_authentication_provider_failed")
                else:
                    raise error("runtime_authentication_protocol_invalid")
            if await process.wait() or not result or not isinstance(result["authenticated"], bool):
                raise error("runtime_authentication_result_missing")
            if release:
                if checking:
                    self.check_diagnostic = {'phase': 'verifying_installation'}
                await asyncio.to_thread(self._verify_workbuddy, release, context)
            if external:
                result.update(method="external_key", source="external_configuration", identity=None)
            self.external_configuration = result.get("method") == "external_key"
        except BaseException:
            if checking:
                self.check_diagnostic = {**self.check_diagnostic, 'failed': True}
            raise
        finally:
            if process:
                deadline = time.monotonic() + 9
                with suppress(ProcessLookupError, OSError):
                    if not attached:
                        # No handshake was sent, so the owned helper cannot
                        # have started an SDK/CLI. Clean it up even if Job
                        # assignment failed; never leave an untracked helper.
                        if process.returncode is None:
                            process.kill()
                    elif os.name == "nt":
                        job.terminate()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                with suppress(TimeoutError):
                    await asyncio.wait_for(process.wait(), max(.01, deadline - time.monotonic()))
                while not job.empty() and time.monotonic() < deadline:
                    await asyncio.sleep(.05)
                self.cleanup_complete = process.returncode is not None and job.empty()
            else:
                self.cleanup_complete = True
            job.close()
        if not self.cleanup_complete:
            raise error("runtime_authentication_cleanup_pending")
        if release and result.get('authentication_observation') is not None and result['authentication_observation'] != self.observation():
            raise error('runtime_authentication_context_changed')
        if checking:
            self.check_diagnostic = {'phase': 'completed'}
        return result
