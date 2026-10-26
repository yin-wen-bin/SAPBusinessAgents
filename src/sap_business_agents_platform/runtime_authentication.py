"""SDK-free, local authentication operations. Never persist credentials or URLs."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .sdk_manager import SDKManagerError
from .workbuddy_environment import atomic_json
from .workbuddy_job import process_identity

ACTIVE = {"queued", "running", "waiting_authentication", "cancelling", "cleanup_pending"}
AUTH_OPERATION_SECONDS = 360
AUTH_CHECK_SECONDS = 30
EXTERNAL_CACHE_POLL_SECONDS = 5
IDENTIFIER = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def error(code: str, **detail: Any) -> SDKManagerError:
    return SDKManagerError("Authentication operation could not be completed.", code=code, detail=detail)


def unconfirmed_error(provider, account):
    code = ('workbuddy_authentication_confirmation_required'
            if provider == 'workbuddy' and account.get('failure_code') == 'workbuddy_authentication_confirmation_required'
            else 'runtime_authentication_check_required')
    return error(code)


def mask_identity(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if any(ord(c) < 32 for c in value):
        return None
    if "@" in value:
        name, domain = value.rsplit("@", 1)
        if not re.fullmatch(r"[a-zA-Z0-9.-]{1,100}", domain):
            return None
        return name[:2] + "***@" + domain[:100]
    return value[:2] + "***"


class RuntimeAuthenticationService:
    def __init__(self, root: Path, adapters: dict[str, Any], *, busy=None, changed=None):
        self.root = root
        self.adapters = adapters
        self.busy = busy or (lambda _provider: [])
        self.changed = changed or (lambda _provider, _state: None)
        self.lock = threading.RLock()
        self.tasks: dict[str, asyncio.Task] = {}
        self.checking: set[str] = set()
        self._shared_checks = {}
        self._post_use_checks: dict[str, asyncio.Task] = {}
        self._post_use_status: dict[str, dict[str, str]] = {}
        self._closing = False
        self._external_monitor = None
        self._automatic_attempt = None
        self.leases: dict[str, int] = {}
        self.sdk_maintenance_active: set[str] = set()
        self.transient: dict[str, dict] = {}
        self.path = root / "authentication.json"
        self.state = {"version": 1, "accounts": {}, "operations": {}}
        self.invalid_state = False
        if self.path.exists():
            try:
                self.state = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(self.state, dict) or self.state.get("version") != 1 or not isinstance(self.state.get("operations"), dict) or not isinstance(self.state.get("accounts"), dict):
                    raise ValueError()
                if any(not isinstance(o, dict) or not all(k in o for k in ("status", "provider_id", "operation_id", "created_at"))
                       for o in self.state["operations"].values()):
                    raise ValueError()
                if any(not isinstance(a, dict) or not isinstance(a.get("revision"), int) or isinstance(a.get("revision"), bool) or a.get("revision", -1) < 0
                       or not all(k in a for k in ("status", "authenticated")) for a in self.state["accounts"].values()):
                    raise ValueError()
            except (ValueError, OSError):
                # Fail closed for authentication, not the whole API/Agent catalog.
                # Preserve damaged state for explicit local repair; never reset it.
                self.invalid_state = True
                self.state = {"version": 1, "accounts": {}, "operations": {}}
        if not self.invalid_state:
            self.reconcile()

    def _save(self):
        if self.invalid_state:
            raise error("runtime_authentication_state_invalid")
        atomic_json(self.path, self.state)

    def _adapter(self, provider):
        if provider not in self.adapters:
            raise error("runtime_not_found")
        return self.adapters[provider]

    def _context(self, provider):
        callback = getattr(self._adapter(provider), "context", None)
        return callback() if callable(callback) else {}

    def _observation(self, provider):
        if provider != 'workbuddy':
            return None
        callback = getattr(self._adapter(provider), 'observation', None)
        try:
            return callback() if callable(callback) else None
        except (OSError, ValueError):
            raise error('runtime_authentication_context_unavailable') from None

    def _observation_current(self, provider):
        observed = self._observation(provider)
        return observed is None or self._account(provider).get('authentication_observation') == observed

    def _assert_context(self, provider, context):
        if self._context(provider) != context:
            raise error("workbuddy_service_site_changed")

    def _context_current(self, provider, context):
        if not context:  # Preserve providers without a separate routing binding.
            return True
        try:
            return self._context(provider) == context
        except Exception:
            return False

    @contextmanager
    def configuration_change(self, provider):
        """Same lock as sign-in, checks and business leases; never affects Codex."""
        with self.lock:
            if self.invalid_state:
                raise error("runtime_authentication_state_invalid")
            self.reconcile()
            if self._active(provider) or self._blockers(provider):
                raise error("runtime_authentication_busy", blockers=self._blockers(provider))
            if self._adapter(provider).capabilities().get("reason") == "runtime_authentication_external_configuration":
                raise error("runtime_authentication_external_configuration")
            yield

    def invalidate_configuration(self, provider, context):
        with self.lock:
            account = self._account(provider)
            self.state.setdefault("account_history", []).append({"provider_id": provider, **copy.deepcopy(account)})
            account.update(status="not_checked", authenticated=None, identity=None,
                           method="unknown", source="unknown", checked_at=None,
                           revision=account["revision"] + 1, confirmed_binding=None,
                           failure_code="runtime_authentication_check_required", **context)
            account.pop("invalidation_pending", None)
            self._save()

    def _account(self, provider):
        return self.state["accounts"].setdefault(provider, {
            "status": "not_checked", "authenticated": None, "identity": None,
            "method": "unknown", "source": "unknown", "checked_at": None,
            "last_successful_check": None, "revision": 0, "failure_code": None,
        })

    def _active(self, provider):
        return next((o for o in self.state["operations"].values()
                     if o["provider_id"] == provider and o["status"] in ACTIVE), None)

    def reconcile(self):
        """Do not kill an unverified PID or automatically resume a sign-in."""
        dirty = False
        for operation in self.state["operations"].values():
            if operation["status"] not in ACTIVE or operation["operation_id"] in self.tasks:
                continue
            pid = operation.get("pid")
            identity = process_identity(pid) if pid else None
            uncertain = identity == "unknown" or (identity is not None and identity == operation.get("process_identity"))
            operation.update(status="cleanup_pending" if uncertain else "interrupted",
                             cleanup_complete=not uncertain, failure_code="runtime_authentication_interrupted",
                             completed_at=None if uncertain else now())
            self._account(operation["provider_id"]).update(status="check_failed", authenticated=None,
                                                           failure_code="runtime_authentication_interrupted")
            dirty = True
        if dirty:
            self._save()

    def view(self, provider):
        with self.lock:
            adapter = self._adapter(provider)
            value = {k: copy.deepcopy(v) for k, v in self._account(provider).items() if k in {
                "status", "authenticated", "identity", "method", "source", "checked_at", "last_successful_check", "revision", "failure_code"}}
            if value.get("method") == "external_key":
                value["external_configuration"] = True
            value.update(capabilities=adapter.capabilities(), operation=None,
                         usage_status="not_checked", account_url=adapter.account_url,
                         usage_url=adapter.usage_url, configuration_url=adapter.configuration_url)
            if provider == "workbuddy" and callable(getattr(adapter, "site_view", None)):
                try:
                    context = adapter.site_view()
                    value.update({k: v for k, v in context.items() if k not in {"account_url", "configuration_url"}},
                                 account_url=context["account_url"], configuration_url=context["configuration_url"])
                    if any(self._account(provider).get(k) != context[k] for k in (
                            "service_site", "service_site_revision", "service_site_digest")):
                        value.update(status="not_checked", authenticated=None, identity=None,
                                     failure_code="runtime_authentication_check_required")
                except Exception as exc:
                    # Broken WorkBuddy routing must not hide the independent Codex card.
                    code = getattr(exc, "code", "workbuddy_service_site_invalid")
                    value.update(status="check_failed", authenticated=None, identity=None, failure_code=code,
                                 account_url=None, configuration_url=None)
                    value["capabilities"].update(check=False, login=False, logout=False, switch_account=False, reason=code)
            if value.get("external_configuration"):
                value["capabilities"].update(login=False, logout=False, switch_account=False,
                                            reason="runtime_authentication_external_configuration")
            operations = [o for o in self.state["operations"].values() if o["provider_id"] == provider]
            if operations:
                value["operation"] = self.operation(provider, max(operations, key=lambda o: o["created_at"])["operation_id"])
            value["blockers"] = self._blockers(provider)
            if provider == 'workbuddy' and callable(getattr(adapter, 'observation', None)):
                try:
                    value['authentication_context_status'] = 'current' if self._observation_current(provider) else 'check_required'
                    if value['authentication_context_status'] != 'current' and value.get('status') == 'authenticated':
                        value.update(status='not_checked', authenticated=None, failure_code='runtime_authentication_check_required')
                except (SDKManagerError, OSError, ValueError) as exc:
                    value.update(status='check_failed', authenticated=None, failure_code=getattr(exc, 'code', 'runtime_authentication_context_unavailable'))
            if self._account(provider).get("invalidation_pending"):
                value.update(status="check_failed", failure_code="runtime_authentication_check_required")
            if self.invalid_state:
                value.update(status="check_failed", failure_code="runtime_authentication_state_invalid")
                value["capabilities"].update(check=False, login=False, logout=False, switch_account=False,
                                            reason="runtime_authentication_state_invalid")
            if provider == 'workbuddy':
                if self._account(provider).get('last_check_diagnostic'):
                    value['last_check_diagnostic'] = copy.deepcopy(self._account(provider)['last_check_diagnostic'])
                sync = self._post_use_status.get(provider)
                try:
                    if (self._external_monitor is not None and not self._external_monitor.done()
                            and self._cache_only_change(provider, external=True)
                            and self._automatic_attempt != (self._account(provider)['revision'], self._observation(provider))
                            and not sync):
                        sync = {'status': 'queued'}
                except (SDKManagerError, OSError, ValueError):
                    sync = {'status': 'blocked', 'failure_code': 'runtime_authentication_context_unavailable'}
                if sync and sync.get('status') == 'confirmed' and value.get('authentication_context_status') != 'current':
                    sync = {'status': 'blocked', 'failure_code': 'runtime_authentication_check_required'}
                if sync and (provider in self._post_use_checks or value.get('authenticated') is not True):
                    value['authentication_sync'] = copy.deepcopy(sync)
            return value

    def operation(self, provider, operation_id):
        with self.lock:
            self._adapter(provider)
            value = self.state["operations"].get(operation_id)
            if not value or value["provider_id"] != provider:
                raise error("runtime_authentication_operation_not_found")
            result = {k: copy.deepcopy(v) for k, v in value.items() if k in {
                "operation_id", "provider_id", "actor", "request_id", "action", "status", "phase", "created_at", "started_at",
                "completed_at", "failure_code", "cleanup_complete"}}
            if provider == "workbuddy":
                result.update(value.get("service_context") or {})
            if result["status"] == "waiting_authentication":
                result.update(self.transient.get(operation_id, {}))
            return result

    def _blockers(self, provider):
        result = list(self.busy(provider))
        if provider in self.sdk_maintenance_active:
            result.append({"code": "sdk_maintenance_active"})
        if self.leases.get(provider):
            result.append({"code": "runtime_authentication_business_active"})
        if provider in self.checking:
            result.append({"code": "runtime_authentication_check_active"})
        return result

    def assert_binding(self, provider, snapshot=None, *, require_authenticated=True):
        with self.lock:
            if provider in self.sdk_maintenance_active:
                raise error("sdk_maintenance_active")
            if self.invalid_state:
                raise error("runtime_authentication_state_invalid")
            if self._active(provider):
                raise error("runtime_authentication_operation_active")
            account = self._account(provider)
            if provider == "workbuddy" and self._context(provider):
                context = self._context(provider)
                if any(account.get(k) != v for k, v in context.items()):
                    raise error("runtime_authentication_check_required")
                if snapshot is not None and any(snapshot.get(k) != v for k, v in context.items()):
                    raise error("workbuddy_service_site_changed")
            if account.get("invalidation_pending"):
                raise error("runtime_authentication_check_required")
            if require_authenticated and account["status"] in {"signed_out", "expired", "check_failed"}:
                raise unconfirmed_error(provider, account)
            revision = (snapshot or {}).get("authentication_binding_revision")
            if revision is not None and (not isinstance(revision, int) or isinstance(revision, bool) or revision < 0):
                raise error("runtime_authentication_binding_invalid")
            if (revision is not None and revision != account["revision"]) or (revision is None and account["revision"]):
                raise error("runtime_authentication_binding_changed")

    def bind(self, provider, snapshot):
        with self.lock:
            if provider in self.sdk_maintenance_active:
                raise error("sdk_maintenance_active")
            if self.invalid_state:
                raise error("runtime_authentication_state_invalid")
            if self._active(provider):
                raise error("runtime_authentication_operation_active")
            return {**snapshot, **self._context(provider), "authentication_binding_revision": self._account(provider)["revision"]}

    @asynccontextmanager
    async def sdk_maintenance(self, provider):
        """Exclusive mutation reservation; never hold a thread lock across await."""
        with self.lock:
            self.assert_binding(provider, self.bind(provider, {}), require_authenticated=False)
            if self._blockers(provider):
                raise error("runtime_authentication_busy", blockers=self._blockers(provider))
            self.sdk_maintenance_active.add(provider)
        try:
            yield
        finally:
            with self.lock:
                self.sdk_maintenance_active.discard(provider)

    @asynccontextmanager
    async def use(self, provider, snapshot=None, *, require_authenticated=True):
        if provider == 'workbuddy' and require_authenticated:
            # Cache metadata is a recheck trigger, not identity/authorization.
            # Same-account token renewal leaves the revision and model intact.
            with self.lock:
                stale = not self._observation_current(provider)
            if stale:
                await self.check(provider)
        with self.lock:
            if provider == 'workbuddy' and require_authenticated and not self._observation_current(provider):
                raise unconfirmed_error(provider, self._account(provider))
            self.assert_binding(provider, snapshot, require_authenticated=require_authenticated)
            self.leases[provider] = self.leases.get(provider, 0) + 1
        completed = False
        failed_owned_return = False
        try:
            yield
            completed = True
        except SDKManagerError as exc:
            # Non-auth SDK failures may still renew the shared cache. They are
            # not proof of either sign-out or cleanup. Only reconcile after an
            # explicit supervisor proof, retaining the original exception.
            failed_owned_return = not str(exc.code).startswith(('workbuddy_authentication_', 'runtime_authentication_')) and exc.code not in {
                'runtime_cleanup_incomplete', 'workbuddy_deadline_exceeded',
                'runtime_deadline_exceeded', 'workbuddy_cancelled', 'runtime_cancelled'}
            raise
        finally:
            with self.lock:
                self.leases[provider] -= 1
                # A managed SDK return has already drained its owned process.
                # Reconcile changed cache metadata separately from the model's
                # result/budget. Never bless it just because that call worked.
                if provider == 'workbuddy' and require_authenticated and (completed or failed_owned_return) and not self.leases[provider]:
                    self._schedule_post_use_check(provider, require_cleanup_proof=failed_owned_return)

    def _cache_only_change(self, provider, *, external=False):
        """Metadata is only a trigger. Identity still comes from official check."""
        account = self._account(provider)
        confirmed = account.get('status') == 'authenticated' and account.get('authenticated') is True
        recoverable = (external and account.get('status') == 'check_failed'
                       and account.get('confirmed_binding', {}).get('authenticated') is True)
        if (not (confirmed or recoverable) or account.get('invalidation_pending')):
            return False
        if external and any(account.get(k) != v for k, v in self._context(provider).items()):
            return False
        prior, current = account.get('authentication_observation'), self._observation(provider)
        return (isinstance(prior, dict) and isinstance(current, dict)
                and all(prior.get(key) and prior.get(key) == current.get(key)
                        for key in ('context_digest', 'source_digest'))
                and prior.get('cache_digest') is not None and current.get('cache_digest') is not None
                and prior != current)

    def start_external_sync(self):
        """Lifespan-owned observer; GET remains strictly read-only."""
        if 'workbuddy' in self.adapters and not self._closing and self._external_monitor is None:
            self._monitor_loop = asyncio.get_running_loop()
            self._external_monitor = asyncio.get_running_loop().create_task(self._watch_external_cache())

    async def _watch_external_cache(self):
        while not self._closing:
            await asyncio.sleep(EXTERNAL_CACHE_POLL_SECONDS)
            # Filesystem/context observation runs off the event loop. No CLI,
            # credentials or authority is obtained from this metadata.
            await asyncio.to_thread(self._external_cache_tick)

    def _external_cache_tick(self):
        with self.lock:
            try:
                if self._closing or self.invalid_state or (self.root.parent / 'startup-maintenance.json').exists():
                    return
            except OSError:
                self._post_use_status['workbuddy'] = {'status': 'blocked', 'failure_code': 'runtime_authentication_context_unavailable'}
                return
            self._schedule_post_use_check('workbuddy', external=True)

    def _schedule_post_use_check(self, provider, *, require_cleanup_proof=False, external=False):
        # Called under the auth lock; no await or SDK execution while holding it.
        # Failure to schedule must not replace the completed business result.
        if self._closing or self.invalid_state or self._active(provider):
            return
        existing = self._post_use_checks.get(provider)
        if existing is not None and not existing.done():
            return
        try:
            if not self._cache_only_change(provider, external=external):
                return
            context = self._context(provider)
            revision = self._account(provider)['revision']
            observed = self._observation(provider)
            if self._automatic_attempt == (revision, observed):
                return
            # The watcher observes in a thread, but tasks belong to its API loop.
            loop = getattr(self, '_monitor_loop', None) if external else asyncio.get_running_loop()
            if loop is None or loop.is_closed():
                return
            if external:
                loop.call_soon_threadsafe(self._queue_external_check, context, revision)
                return
            task = loop.create_task(self._check_after_use(
                provider, context, revision, require_cleanup_proof=require_cleanup_proof))
        except (SDKManagerError, OSError, ValueError, RuntimeError):
            self._post_use_status[provider] = {'status': 'blocked', 'failure_code': 'runtime_authentication_context_unavailable'}
            return
        self._post_use_checks[provider] = task
        self._post_use_status[provider] = {'status': 'queued'}
        def finished(done):
            with self.lock:
                if self._post_use_checks.get(provider) is done:
                    self._post_use_checks.pop(provider, None)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)

    def _queue_external_check(self, context, revision):
        with self.lock:
            if (self._closing or self._active('workbuddy') or self.leases.get('workbuddy')
                    or 'workbuddy' in self.sdk_maintenance_active
                    or not self._context_current('workbuddy', context)
                    or self._account('workbuddy')['revision'] != revision):
                return
            existing = self._post_use_checks.get('workbuddy')
            if existing is not None and not existing.done():
                return
            task = asyncio.create_task(self._check_after_use('workbuddy', context, revision,
                require_cleanup_proof=True, external=True))
            self._post_use_checks['workbuddy'] = task
            self._post_use_status['workbuddy'] = {'status': 'queued'}
            def finished(done):
                with self.lock:
                    if self._post_use_checks.get('workbuddy') is done:
                        self._post_use_checks.pop('workbuddy', None)
                if not done.cancelled():
                    done.exception()
            task.add_done_callback(finished)

    async def _check_after_use(self, provider, context, revision, *, require_cleanup_proof=False, external=False):
        # Some model-check APIs return a failed-check record rather than raise
        # a cleanup error. A normal lease return alone therefore cannot prove
        # that every owned SDK process has been drained. Reuse the existing
        # supervisor's active/pending proof, not broad business/history scans.
        supervisor = getattr(getattr(self._adapter(provider), 'workbuddy', None), 'supervisor', None)
        if require_cleanup_proof and (supervisor is None or not callable(getattr(supervisor, 'pending', None))):
            self._post_use_status[provider] = {'status': 'blocked', 'failure_code': 'runtime_authentication_cleanup_unconfirmed'}
            return
        if supervisor is not None:
            try:
                if await asyncio.to_thread(lambda: bool(supervisor.active or supervisor.pending())):
                    self._post_use_status[provider] = {'status': 'deferred', 'failure_code': 'runtime_authentication_business_active'}
                    return
            except Exception:
                self._post_use_status[provider] = {'status': 'blocked', 'failure_code': 'runtime_authentication_check_required'}
                return
        if external:
            try:
                if await asyncio.to_thread(self._blockers, provider):
                    self._post_use_status[provider] = {'status': 'deferred', 'failure_code': 'runtime_authentication_business_active'}
                    return
            except Exception:
                self._post_use_status[provider] = {'status': 'blocked', 'failure_code': 'runtime_authentication_cleanup_unconfirmed'}
                return
        with self.lock:
            # Do not resume an old completion into a new site/account, an
            # interactive auth operation, or another still-owned SDK lease.
            if (self._closing or self._active(provider) or self.leases.get(provider)
                    or not self._context_current(provider, context)
                    or self._account(provider)['revision'] != revision):
                self._post_use_status[provider] = {'status': 'deferred'}
                return
            try:
                if not self._cache_only_change(provider, external=external):
                    self._post_use_status[provider] = {'status': 'not_required'}
                    return
                observed = self._observation(provider)
                if self._automatic_attempt == (revision, observed):
                    return
            except (SDKManagerError, OSError, ValueError):
                self._post_use_status[provider] = {'status': 'blocked', 'failure_code': 'runtime_authentication_context_unavailable'}
                return
            self._post_use_status[provider] = {'status': 'checking'}
            self._automatic_attempt = (revision, observed)
        try:
            # Reuse the same bounded official check and single-flight path as
            # explicit/pre-dispatch checks. No login, retry or model call.
            result = await self.check(provider)
            self._post_use_status[provider] = {'status': 'confirmed' if result.get('authenticated') is True
                and result.get('authentication_context_status') == 'current' else 'failed'}
            if result.get('failure_code'):
                self._post_use_status[provider]['failure_code'] = result['failure_code']
        except asyncio.CancelledError:
            self._post_use_status[provider] = {'status': 'cancelled'}
            raise
        except Exception:
            self._post_use_status[provider] = {'status': 'failed', 'failure_code': 'runtime_authentication_check_required'}

    def _accept(self, provider, result, *, change=False, context=None, observation=None):
        context = context or {}
        self._assert_context(provider, context)
        account = self._account(provider)
        authenticated = result.get("authenticated")
        if not isinstance(authenticated, bool):
            raise error("runtime_authentication_result_invalid")
        if provider == 'workbuddy' and not authenticated and not change:
            # initialize may temporarily omit account/token while the official
            # CLI renews its cache. This is NOT a confirmed logout or account
            # change. Fail closed without destroying the last positive binding.
            account.update(status='check_failed', authenticated=None, checked_at=now(),
                           failure_code='workbuddy_authentication_confirmation_required',
                           last_check_diagnostic={'code': 'workbuddy_authentication_confirmation_required',
                               'evidence': result.get('authentication_evidence') if result.get('authentication_evidence') in {
                                   'initialize_account_absent', 'initialize_account_incomplete'} else 'initialize_account_unconfirmed'}, **context)
            self._save()
            self.changed(provider, {**copy.deepcopy(account), 'changed': False})
            return
        identity = mask_identity(result.get("identity"))
        previous = account.get("confirmed_binding")
        method = result.get("method", "unknown")
        source = result.get("source", "official_cli")
        # A confirmed identity/source change is meaningful; a failed check is not.
        identifier = result.get("identity")
        binding = {"identity_digest": hashlib.sha256(identifier.encode()).hexdigest() if isinstance(identifier, str) else None,
                   "method": method, "source": source, "authenticated": authenticated, **context}
        changed = change or account.get("invalidation_pending") or (previous is not None and previous != binding)
        account.update(status="authenticated" if authenticated else "signed_out", authenticated=authenticated,
                       identity=identity, method=method, source=source, checked_at=now(), failure_code=None,
                       last_successful_check=now(), **context)
        account["confirmed_binding"] = binding
        if provider == 'workbuddy' and authenticated:
            account['last_confirmed_binding'] = copy.deepcopy(binding)
            account.pop('last_check_diagnostic', None)
        if provider == 'workbuddy' and observation is not None:
            account['authentication_observation'] = observation
        if changed:
            account["revision"] += 1
            account["invalidation_pending"] = True
        self._save()
        # Legacy projections and account-dependent model checks remain in their owners.
        self.changed(provider, {**copy.deepcopy(account), "changed": bool(changed)})
        account["invalidation_pending"] = False
        self._save()

    async def check(self, provider):
        if provider != 'workbuddy':
            return await self._check_once(provider)
        # Same-binding concurrent rechecks share one official operation, never
        # a cached authorization. Cancelling one waiter cannot cancel the check.
        loop = asyncio.get_running_loop()
        with self.lock:
            context = self._context(provider)
            prior = self._shared_checks.get(provider)
            if prior is not None and not prior[1].done():
                if prior[0] != context or prior[2] is not loop:
                    raise error('runtime_authentication_context_changed')
                task = prior[1]
            else:
                task = loop.create_task(self._check_once(provider, expected_context=context))
                self._shared_checks[provider] = (context, task, loop)
                def finished(done):
                    with self.lock:
                        if self._shared_checks.get(provider, (None, None))[1] is done:
                            self._shared_checks.pop(provider, None)
                    if not done.cancelled():
                        done.exception()  # Consume even when every waiter left.
                task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _check_once(self, provider, *, expected_context=None):
        with self.lock:
            if self.invalid_state:
                raise error("runtime_authentication_state_invalid")
            self.reconcile()
            adapter = self._adapter(provider)
            context = self._context(provider)
            if expected_context is not None and expected_context != context:
                raise error('runtime_authentication_context_changed')
            if self._active(provider) or provider in self.checking or provider in self.sdk_maintenance_active:
                raise error("runtime_authentication_operation_active")
            self.checking.add(provider)
        check_started = time.monotonic()
        try:
            before = self._observation(provider)
            result = await asyncio.wait_for(adapter.check(context=context) if context else adapter.check(), AUTH_CHECK_SECONDS)
            after = self._observation(provider)
            if before is not None and (after is None or before['context_digest'] != after['context_digest']):
                raise error('runtime_authentication_context_changed')
            with self.lock:
                # An unrelated concurrent cache write is not confirmed by this
                # result. Leave it due for a fresh official check before use.
                confirmed = result.get('authentication_observation') if provider == 'workbuddy' else None
                if confirmed is not None:
                    # Only a complete official account read, bracketed by an
                    # unchanged cache observation in the owned helper, can
                    # attest a cache renewed during startup. Outer observations
                    # alone still cannot bless a concurrent cache mutation.
                    if (result.get('authenticated') is not True or before is None or confirmed != after
                            or confirmed.get('context_digest') != before.get('context_digest')):
                        raise error('runtime_authentication_context_changed')
                    observed = confirmed
                else:
                    observed = before if before is not None and before != after else after
                self._accept(provider, result, context=context, observation=observed)
        except Exception as exc:
            with self.lock:
                # A late check must not overwrite the account for a newer site.
                if self._context_current(provider, context):
                    code = getattr(exc, "code", "runtime_authentication_check_failed")
                    if provider == 'workbuddy':
                        if isinstance(exc, TimeoutError):
                            code = 'runtime_authentication_check_timeout'
                        phase = (getattr(adapter, 'check_diagnostic', {}) or {}).get('phase')
                        self._account(provider)['last_check_diagnostic'] = {
                            'code': code,
                            'phase': phase if phase in {'preparing', 'starting_official_process',
                                'waiting_official_result', 'verifying_installation', 'completed'} else 'official_check',
                            'elapsed_ms': round((time.monotonic()-check_started)*1000, 2),
                            'cleanup_complete': getattr(adapter, 'cleanup_complete', False) is True}
                    self._account(provider).update(status="expired" if provider == 'workbuddy' and getattr(exc, 'code', None) == 'workbuddy_authentication_required' else "check_failed", authenticated=None,
                        checked_at=now(), failure_code=code, **context)
                    self._save()
                    self.changed(provider, {**copy.deepcopy(self._account(provider)), "changed": False})
        finally:
            self.checking.discard(provider)
        return self.view(provider)

    def report_workbuddy_failure(self, snapshot, diagnostic, *, started_at):
        """Only a current owned-job explicit auth failure fences readiness."""
        if (diagnostic.get('failure_code') != 'workbuddy_authentication_required'
                or not (diagnostic.get('http_status') == 401 or diagnostic.get('authentication_reason') == 'login_required')
                or diagnostic.get('failure_source') not in {'owned_worker_stderr', 'owned_cli_trace'}):
            return
        with self.lock:
            account = self._account('workbuddy')
            revision = snapshot.get('authentication_binding_revision', snapshot.get('authentication_revision'))
            if revision != account['revision'] or not self._context_current('workbuddy', {k: snapshot[k] for k in self._context('workbuddy') if k in snapshot}):
                return
            if account.get('checked_at') and datetime.fromisoformat(account['checked_at']).timestamp() > started_at:
                return  # A later official check must not be overwritten by an old job.
            account.update(status='expired', authenticated=None, failure_code='workbuddy_authentication_required')
            self._save()
            self.changed('workbuddy', {**copy.deepcopy(account), 'changed': False})

    async def start(self, provider, action, request_id, *, confirmed=False, service_site_digest=None):
        if action not in {"login", "logout", "switch_account"} or not IDENTIFIER.fullmatch(request_id):
            raise error("runtime_authentication_request_invalid")
        with self.lock:
            self.reconcile()
            adapter = self._adapter(provider)
            context = self._context(provider)
            if service_site_digest is not None and context.get("service_site_digest") != service_site_digest:
                raise error("workbuddy_service_site_changed")
            base = f"{provider}:{action}:{confirmed}"
            fingerprint = hashlib.sha256((base + (":" + json.dumps(context, sort_keys=True) if context else "")).encode()).hexdigest()
            for prior in self.state["operations"].values():
                if prior["provider_id"] == provider and prior["request_id"] == request_id:
                    if prior["fingerprint"] != fingerprint:
                        raise error("runtime_authentication_request_conflict")
                    return self.operation(provider, prior["operation_id"])
            capabilities = self.view(provider)["capabilities"]
            if not capabilities.get(action):
                raise error(capabilities.get("reason") or "runtime_authentication_action_unavailable")
            if action in {"logout", "switch_account"} and not confirmed:
                raise error("runtime_authentication_confirmation_required")
            if self._active(provider) or self._blockers(provider):
                raise error("runtime_authentication_busy", blockers=self._blockers(provider))
            operation_id = "auth_" + uuid.uuid4().hex
            record = {"operation_id": operation_id, "provider_id": provider, "actor": "local-user",
                      "request_id": request_id, "fingerprint": fingerprint, "action": action,
                      "status": "queued", "phase": "preparing", "created_at": now(), "completed_at": None,
                      "failure_code": None, "cleanup_complete": None}
            if context:
                record["service_context"] = context
            self.state["operations"][operation_id] = record
            self._save()
            task = asyncio.create_task(self._run(record))
            self.tasks[operation_id] = task
            task.add_done_callback(lambda _task: self.tasks.pop(operation_id, None))
            return self.operation(provider, operation_id)

    async def _run(self, record):
        provider, operation_id = record["provider_id"], record["operation_id"]
        adapter = self._adapter(provider)
        context = record.get("service_context") or {}
        # Only an already-confirmed ordinary WorkBuddy login may retain its
        # revision. Logout/account switching and any intervening invalidation
        # stay revocation boundaries. Codex keeps its existing behavior.
        with self.lock:
            original_account = copy.deepcopy(self._account(provider)) if provider == "workbuddy" else None
        signed_out = False

        def event(kind, data):
            nonlocal signed_out
            with self.lock:
                if record["status"] in {"cancelling", "cancelled", "failed", "completed", "cleanup_pending"}:
                    return
                if kind == "waiting":
                    # Adapter validates the URL. It is memory-only and never part of audit.
                    self.transient[operation_id] = {k: data[k] for k in ("auth_url", "browser_opened") if k in data}
                    record.update(status="waiting_authentication", phase="waiting_authentication")
                elif kind == "owned_process":
                    record.update(pid=data["pid"], process_identity=data["process_identity"])
                elif kind == "signed_out":
                    signed_out = True
                    self._accept(provider, {"authenticated": False, "method": "browser", "source": "official_cli"}, change=True, context=context)
                    record["phase"] = "signed_out"
                self._save()
        try:
            before_observation = self._observation(provider)
            with self.lock:
                self._assert_context(provider, context)
                record.update(status="running", started_at=now())
                self._save()
            async with asyncio.timeout(AUTH_OPERATION_SECONDS):
                result = await adapter.execute(record["action"], event, context=context) if context else await adapter.execute(record["action"], event)
            after_observation = self._observation(provider)
            if before_observation is not None and (after_observation is None or before_observation['context_digest'] != after_observation['context_digest']):
                raise error('runtime_authentication_context_changed')
            with self.lock:
                if record["status"] == "cancelling":
                    raise asyncio.CancelledError()
                change = True
                if provider == "workbuddy":
                    if getattr(adapter, "cleanup_complete", False) is not True:
                        raise error("runtime_authentication_cleanup_pending")
                    identifier = result.get("identity")
                    candidate = {
                        "identity_digest": hashlib.sha256(identifier.encode()).hexdigest()
                            if isinstance(identifier, str) and identifier.strip() else None,
                        "authenticated": result.get("authenticated"),
                        "method": result.get("method", "unknown"),
                        "source": result.get("source", "official_cli"), **context}
                    current = self._account(provider)
                    confirmed_observation = result.get("authentication_observation")
                    if confirmed_observation is not None and confirmed_observation != after_observation:
                        raise error("runtime_authentication_context_changed")
                    observation_matches = (
                        before_observation is not None and after_observation is not None
                        and bool(before_observation.get("context_digest"))
                        and bool(before_observation.get("source_digest"))
                        and before_observation.get("context_digest") == after_observation.get("context_digest")
                        and all((original_account.get("authentication_observation") or {}).get(key)
                                == before_observation.get(key) for key in ("context_digest", "source_digest"))
                        and (before_observation == after_observation or confirmed_observation == after_observation))
                    same_login = (
                        record["action"] == "login" and not signed_out and observation_matches
                        and candidate["identity_digest"] is not None
                        and candidate["source"] == "official_cli" and candidate["authenticated"] is True
                        and all(account.get("authenticated") is True
                                and account.get("status") == "authenticated"
                                and not account.get("invalidation_pending") and not account.get("failure_code")
                                and account.get("confirmed_binding") == candidate
                                for account in (original_account, current))
                        and original_account.get("revision") == current.get("revision"))
                    change = not same_login
                self._accept(provider, result, change=change, context=context, observation=after_observation)
                record.update(status="completed", phase="completed", cleanup_complete=True, completed_at=now())
        except (Exception, asyncio.CancelledError) as exc:
            cleanup = getattr(adapter, "cleanup_complete", True)
            record.update(status="cleanup_pending" if not cleanup else "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                          phase="cleanup" if not cleanup else "finished", cleanup_complete=cleanup,
                          completed_at=now() if cleanup else None,
                          failure_code=getattr(exc, "code", "runtime_authentication_cancelled" if isinstance(exc, asyncio.CancelledError)
                                               else "runtime_authentication_timeout" if isinstance(exc, TimeoutError) else "runtime_authentication_failed"))
            # Cancellation can follow a completed credential change: do not invent a signed-out result.
            if self._context_current(provider, context):
                self._account(provider).update(status="check_failed", authenticated=None, failure_code=record["failure_code"], **context)
                self.changed(provider, {**copy.deepcopy(self._account(provider)), "changed": False})
        finally:
            self.transient.pop(operation_id, None)
            with self.lock:
                self._save()

    async def cancel(self, provider, operation_id):
        with self.lock:
            record = self.state["operations"].get(operation_id)
            self.operation(provider, operation_id)  # ownership check
            task = self.tasks.get(operation_id)
            if task and not task.done():
                if record["status"] == "queued":
                    # Cancelling a task before its coroutine starts never runs
                    # that coroutine's finally block; record the no-op here.
                    record.update(status="cancelled", phase="finished", cleanup_complete=True,
                                  completed_at=now(), failure_code="runtime_authentication_cancelled")
                else:
                    record["status"] = "cancelling"
                self._save()
                task.cancel()
        if task:
            await asyncio.wait({task}, timeout=10)
        return self.operation(provider, operation_id)

    async def close(self):
        self._closing = True
        tasks = (set(self.tasks.values()) | {item[1] for item in self._shared_checks.values()}
                 | set(self._post_use_checks.values()))
        if self._external_monitor is not None:
            tasks.add(self._external_monitor)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=10)
