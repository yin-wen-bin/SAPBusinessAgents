"""Bounded display-only projections. Never authorize a business operation."""
from __future__ import annotations

import copy
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Callable


class StatusProjection:
    def __init__(self, loaders: dict[str, Callable], fingerprints: dict[str, Callable],
                 templates: dict[str, Callable], *, ttl=60.0, refresh_limit=10.0, clock=time.monotonic, guards=None,
                 accept_late=(), refresh_every=None, idle_after=30.0):
        self.loaders, self.fingerprints, self.templates = loaders, fingerprints, templates
        self.ttl, self.refresh_limit, self.clock = ttl, refresh_limit, clock
        self._lock = threading.RLock()
        self._slots: dict[str, dict] = {}
        self._closed = False
        self.guards = guards or {}
        self.accept_late = frozenset(accept_late)
        self._epochs = {key: 0 for key in loaders}
        self.refresh_every, self.idle_after = refresh_every, idle_after
        self._requested = {}
        # Thread creation itself can stall a Windows GET under scheduler/AV
        # pressure. Prepare one idle reader per key; GET only enqueues work.
        self._queues = {key: queue.SimpleQueue() for key in loaders}
        for key in loaders:
            threading.Thread(target=self._reader, args=(key,), daemon=True,
                             name='status-' + key).start()

    def _reader(self, key):
        while True:
            try:
                task = self._queues[key].get(timeout=self.refresh_every)
            except queue.Empty:
                with self._lock:
                    if self._closed:
                        return
                    now = self.clock()
                    slot = self._slots.get(key)
                    if (not slot or slot['running'] or key not in self._requested
                            or now - self._requested[key] >= self.idle_after or now < slot.get('retry_at', 0)):
                        continue
                    try:
                        binding = self._binding(key)
                    except Exception:
                        continue
                    slot.update(running=True, started=now, error=None)
                    task = (slot, binding)
            if task is None:
                return
            if self._closed:
                return
            slot, binding = task
            self._load(key, slot, binding)

    def _binding(self, key):
        return (self._epochs[key], self.fingerprints[key]())

    def _load(self, key, slot, binding):
        start = self.clock()
        try:
            guard = self.guards[key]() if key in self.guards else None
            value = self.loaders[key]()
            valid = self._binding(key) == binding and (key not in self.guards or self.guards[key]() == guard)
            error = None
        except Exception:
            value, valid, error = None, False, "status_projection_failed"
        with self._lock:
            slot["running"] = False
            # A confirmed management response may have published a newer value
            # while this one reader was in flight. Never undo that publication.
            if slot.get("published_epoch", -1) > binding[0]:
                return
            slot["elapsed_ms"] = round((self.clock() - start) * 1000, 2)
            if self._closed:
                return
            if not valid:
                slot["error"] = error or "status_binding_changed"
                slot["retry_at"] = self.clock() + self.ttl
            elif slot["elapsed_ms"] > self.refresh_limit * 1000 and key not in self.accept_late:
                slot["error"], slot["retry_at"] = "status_refresh_timeout", self.clock() + self.ttl
            else:
                slot.update(value=copy.deepcopy(value), binding=binding, completed=self.clock(),
                    checked_at=datetime.now(timezone.utc).isoformat(), error=None, retry_at=0)

    def get(self, key, *, refresh=True):
        try:
            binding = self._binding(key)
        except Exception:
            binding = None
        with self._lock:
            now = self.clock()
            self._requested[key] = now
            slot = self._slots.setdefault(key, {"running": False, "value": None, "retry_at": 0})
            fresh = (binding is not None and slot.get("binding") == binding and slot.get("value") is not None
                     and now - slot.get("completed", 0) < self.ttl and not slot.get("error"))
            if refresh and not fresh and not self._closed and not slot["running"] and now >= slot["retry_at"] and binding is not None:
                slot.update(running=True, started=now, error=None)
                self._queues[key].put((slot, binding))
            timed_out = slot["running"] and now - slot.get("started", now) >= self.refresh_limit
            error = "status_refresh_timeout" if timed_out else slot.get("error")
            waiting_late = slot["running"] and key in self.accept_late
            value = copy.deepcopy(slot["value"] if slot.get("value") is not None else self.templates[key]())
            if not fresh:
                value.update(can_enable=False, selectable=False)
                value["blockers"] = list(dict.fromkeys([*value.get("blockers", []), error or "runtime_status_pending"]))
            value["status_refresh"] = {"display_only": True, "stale": not fresh,
                "status": "ready" if fresh else "refreshing" if waiting_late else "failed" if error else "refreshing" if slot["running"] else "unknown",
                "checked_at": slot.get("checked_at"),
                "elapsed_ms": round((now - slot.get("started", now)) * 1000, 2) if slot["running"] else slot.get("elapsed_ms"), "error_code": error,
                "running": slot["running"], "wait_exceeded": bool(timed_out),
                "completed_after_limit": bool(fresh and slot.get("elapsed_ms", 0) > self.refresh_limit * 1000)}
            return value

    def publish(self, key, value):
        """Publish a confirmed display fact; never an execution authorization."""
        with self._lock:
            if self._closed:
                return
            self._epochs[key] += 1
            self._requested[key] = self.clock()
            slot = self._slots.setdefault(key, {"running": False})
            slot.update(value=copy.deepcopy(value), binding=self._binding(key), completed=self.clock(),
                checked_at=datetime.now(timezone.utc).isoformat(), error=None, retry_at=0,
                elapsed_ms=0, published_epoch=self._epochs[key])

    def close(self):
        with self._lock:
            self._closed = True
            for pending in self._queues.values():
                pending.put(None)

    def invalidate(self, keys=None):
        with self._lock:
            for key in (keys or self._epochs):
                if key in self._epochs:
                    self._epochs[key] += 1
                    self._slots.get(key, {}).update(retry_at=0)


class CatalogHealthProjection:
    def __init__(self, agents, workflows, skills):
        self.agents, self.workflows, self.skills = agents, workflows, skills
        self.cache = StatusProjection({"catalog": self._load}, {"catalog": self._generation},
            {"catalog": lambda: {"executable_agents": None, "published_workflows": None, "approved_skills": None}},
            refresh_limit=30)

    def _generation(self):
        return getattr(getattr(self.agents, "_gate", None), "generation", 0)

    def _load(self):
        start = time.perf_counter()
        # One immutable repository generation; never acquire this lock in /health.
        with self.agents.read_transaction():
            locked = time.perf_counter()
            agents = len(self.agents.executable())
            a = time.perf_counter()
            workflows = len(self.workflows.list())
            b = time.perf_counter()
            skills = len(self.skills.list())
        return {"executable_agents": agents, "published_workflows": workflows, "approved_skills": skills,
                "timings_ms": {"directory_lock_wait": round((locked-start)*1000,2),
                               "agent_catalog": round((a-locked)*1000,2), "workflow_catalog": round((b-a)*1000,2),
                               "skill_catalog": round((time.perf_counter()-b)*1000,2)}}

    def view(self):
        return self.cache.get("catalog")

    def close(self):
        self.cache.close()


class SDKDisplayProjection:
    """Independent, cheap WorkBuddy control facts over the full display gate.

    No filesystem work is performed on the GET thread. Neither projection is
    consumed by the actual enable/default/dispatch admission implementations.
    """
    CONFIG_FIELDS = ('default_model_id', 'service_site', 'service_site_revision', 'environment_digest')
    def __init__(self, manager, qualification, *, clock=time.monotonic):
        self.manager, self.qualification = manager, qualification
        self._lock = threading.RLock()
        self._revision = None
        self.controls = StatusProjection({'workbuddy': self._read_control},
            {'workbuddy': lambda: manager.default_provider_id},
            {'workbuddy': lambda: {'enabled': None, 'revision': None}},
            ttl=5, clock=clock, accept_late={'workbuddy'}, refresh_every=2.0)

    def _adopt_revision(self, revision):
        with self._lock:
            if self._revision is not None and revision < self._revision:
                raise ValueError('runtime_control_revision_regressed')
            if revision != self._revision:
                self._revision = revision
                self.qualification.invalidate(['workbuddy'])

    def _read_control(self):
        # state() validates the small local management record only. In
        # particular: no release(), snapshot(), auth probe or qualification.
        state = self.manager.workbuddy.environment.state()
        enabled, revision = state.get('enabled', False), state.get('revision', 0)
        if not isinstance(enabled, bool) or type(revision) is not int or revision < 0:
            raise ValueError('runtime_control_invalid')
        self._adopt_revision(revision)
        return {'enabled': enabled, 'revision': revision,
                **{key: state.get(key) for key in self.CONFIG_FIELDS}}

    @staticmethod
    def _control_revision(item):
        # Three-layer admission freezes a model configuration revision that is
        # deliberately independent of the host's enable/disable fact revision.
        # Keep the legacy response fallback only when no explicit control fact
        # is present; an unknown explicit fact must not grant display readiness.
        fact = item.get('control_status')
        revision = (fact.get('revision') if fact.get('status') == 'ready' else None) \
            if isinstance(fact, dict) else item.get('runtime_configuration_revision')
        return revision if type(revision) is int and revision >= 0 else None

    def get(self, key):
        if key != 'workbuddy':
            return self.qualification.get(key)
        control = self.controls.get(key)
        refresh = control['status_refresh']
        known = not refresh['stale']
        value = self.qualification.get(key, refresh=known)
        qualified_control_revision = self._control_revision(value)
        value['enabled'] = control['enabled'] if known else None
        if known:
            value.update({key: control[key] for key in self.CONFIG_FIELDS if key in control})
        value['selected'] = self.manager.default_provider_id == key
        value['control_status'] = {'status': 'ready' if known else 'unknown',
            'revision': control.get('revision') if known else None,
            'checked_at': refresh['checked_at'], 'running': refresh['running'],
            'error_code': refresh['error_code']}
        blockers = [b for b in value.get('blockers', []) if b != 'runtime_disabled']
        if known and control['enabled'] is False:
            blockers.append('runtime_disabled')
        if not known:
            value.update(can_enable=False, selectable=False)
            blockers.append('runtime_control_unknown')
        elif qualified_control_revision != control['revision']:
            # Even a fresh expensive projection cannot certify another control
            # revision. A background read will compute the new full result.
            value.update(can_enable=False, selectable=False)
            value['status_refresh']['stale'] = True
            blockers.append('runtime_status_pending')
        if value['enabled'] is not True:
            value['selectable'] = False
        value['blockers'] = list(dict.fromkeys(blockers))
        return value

    def publish_control(self, provider, item):
        if provider != 'workbuddy':
            return
        enabled, revision = item.get('enabled'), self._control_revision(item)
        if not isinstance(enabled, bool) or type(revision) is not int or revision < 0:
            return
        with self._lock:
            if self._revision is not None and revision < self._revision:
                return
            self._adopt_revision(revision)
            self.controls.publish(provider, {'enabled': enabled, 'revision': revision,
                **{key: item[key] for key in self.CONFIG_FIELDS if key in item}})

    def invalidate(self, keys=None):
        self.qualification.invalidate(keys)
        if keys is None or 'workbuddy' in keys:
            self.controls.invalidate(['workbuddy'])

    def close(self):
        self.controls.close()
        self.qualification.close()


def sdk_display_projection(manager):
    """Keep the full manager gate intact; only GET-list uses these projections."""
    import hashlib
    import json
    from dataclasses import asdict
    from pathlib import Path

    def guard(definition):
        state = manager._state.get(definition.sdk_id, {})
        workbuddy = getattr(manager, "workbuddy", None)
        path = getattr(getattr(workbuddy, "environment", None), "state_path", None)
        signature = None
        if definition.provider_id == "workbuddy" and path:
            stat = Path(path).stat() if Path(path).exists() else None
            signature = (stat.st_mtime_ns, stat.st_size) if stat else None
        if manager.authentication_service:
            with manager.authentication_service.lock:
                authentication = copy.deepcopy(manager.authentication_service.state)
        else:
            authentication = None
        from .runtime_policy import ORCHESTRATION_FILES
        root = Path(__file__).parent
        sources = []
        if definition.provider_id == "workbuddy":
            for name in ORCHESTRATION_FILES:
                file = root / name
                stat = file.stat() if file.exists() else None
                sources.append((name, stat.st_mtime_ns, stat.st_size) if stat else (name, None))
        value = [state, manager._runtime_config, signature, authentication, sources]
        return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

    def fingerprint(definition):
        # GET must remain independent even of a slow stat()/auth-state disk.
        return hashlib.sha256(json.dumps([manager._state.get(definition.sdk_id, {}), manager._runtime_config],
            sort_keys=True, default=str).encode()).hexdigest()

    def template(definition):
        value = asdict(definition)
        value.update(enabled=None, selected=manager.default_provider_id == definition.provider_id,
                     can_enable=False, selectable=False, blockers=["runtime_status_pending"],
                     installed_version=None, model_status="unknown", authenticated=None, platform_supported=None)
        return value

    definitions = manager.definitions
    def load(definition):
        if definition.provider_id == "workbuddy":
            with manager.workbuddy.environment.display_inspection():
                return manager._snapshot(definition)
        return manager._snapshot(definition)
    qualification = StatusProjection(
        {d.provider_id: lambda d=d: load(d) for d in definitions},
        {d.provider_id: lambda d=d: fingerprint(d) for d in definitions},
        {d.provider_id: lambda d=d: template(d) for d in definitions},
        guards={d.provider_id: lambda d=d: guard(d) for d in definitions},
        # WorkBuddy's full provenance inspection can outlive the display wait
        # budget. Keep pending/timeout responses bounded, but accept an eventual
        # complete result only when both configuration and disk guard still
        # match. This never changes admission, the 60s TTL or Codex's policy.
        accept_late={'workbuddy'})
    return SDKDisplayProjection(manager, qualification)
