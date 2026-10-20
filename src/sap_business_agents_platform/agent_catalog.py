"""Display-only catalog snapshots and request-local management read contexts.

No SDK, model, execution authorization or persistent qualification cache.
"""
from __future__ import annotations

import copy
import hashlib
import json
import queue
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .manifests import AgentRepository, ManifestError, validate_manifest
from .workflows import agent_digest


def _hash(value):
    return hashlib.sha256(value).hexdigest()


class CatalogSourceChanged(ValueError):
    pass


class ManifestSnapshots:
    """Only immutable, fully validated content is reusable for display."""
    def __init__(self):
        directory = Path(__file__).parent
        self.rules = _hash(b''.join((directory / name).read_bytes()
            for name in ('manifests.py', 'agent_identity.py', 'relationships.py')))
        self.values = {}
        self.validation_count = 0

    def parse(self, path, content):
        key = (str(path), _hash(content), self.rules)
        if key not in self.values:
            value = json.loads(content.decode('utf-8'))
            if value.get('schemaVersion') == 2:
                validate_manifest(value, str(path))
            self.validation_count += 1
            # Keep only one content version per path; this is not an audit store.
            self.values = {k: v for k, v in self.values.items() if k[0] != str(path)}
            self.values[key] = value
        return copy.deepcopy(self.values[key])


class CatalogReadContext(AgentRepository):
    def __init__(self, repository, *, snapshots=None, agent_id=None):
        super().__init__(repository.agents_root)
        self.snapshots = snapshots or ManifestSnapshots()
        self.paths = sorted(self.agents_root.glob('*/*/agent.json'))
        self.index = {p.parent.name: p for p in self.paths}
        self.selected = agent_id
        if agent_id is not None and agent_id not in self.index:
            raise KeyError(agent_id)
        self.loaded, self.lifecycles, self.inputs = {}, {}, {}
        self.generation = self._gate.generation
        self.timings = defaultdict(float)
        self.validation_before = self.snapshots.validation_count

    def _path(self, agent_id):
        try:
            return self.index[agent_id]
        except KeyError:
            raise KeyError(agent_id) from None

    def read(self, path):
        start = time.perf_counter()
        content = path.read_bytes()
        self.inputs[path] = _hash(content)
        self.timings['disk'] += time.perf_counter() - start
        return content

    def json(self, path):
        value = json.loads(self.read(path).decode('utf-8'))
        if not isinstance(value, dict):
            raise ManifestError('Catalog source is not an object')
        return value

    def _load_path(self, path):
        if path not in self.loaded:
            content = self.read(path)
            start = time.perf_counter()
            self.loaded[path] = self.snapshots.parse(path, content)
            self.timings['validation'] += time.perf_counter() - start
        return self.loaded[path]

    def list_all(self):
        paths = [self._path(self.selected)] if self.selected else self.paths
        return [self._load_path(p) for p in paths]

    def lifecycle(self, directory_or_agent):
        directory = directory_or_agent if isinstance(directory_or_agent, Path) else self._path(directory_or_agent).parent
        if directory not in self.lifecycles:
            publication = directory / 'publication.json'
            if publication.is_file():
                # Use the original lifecycle validator; verify the bytes it read.
                before = self.read(publication)
                value = super().lifecycle(directory)
                if publication.read_bytes() != before:
                    raise CatalogSourceChanged()
            else:
                value = super().lifecycle(directory)
            self.lifecycles[directory] = value
        return self.lifecycles[directory]

    def finish(self):
        if self._gate.generation != self.generation or sorted(self.agents_root.glob('*/*/agent.json')) != self.paths:
            raise CatalogSourceChanged()
        # Catch external writers, including changes with preserved timestamps.
        for path, expected in self.inputs.items():
            if not path.is_file() or _hash(path.read_bytes()) != expected:
                raise CatalogSourceChanged()
        for directory in self.lifecycles:
            publication = directory / 'publication.json'
            if publication not in self.inputs and publication.exists():
                raise CatalogSourceChanged()


class CatalogStore:
    def __init__(self, store):
        self.store = store
        self.drafts = store.list_agent_authoring_drafts()
        with store._connect() as connection:
            rows = connection.execute("SELECT agent_id, COUNT(*) AS count FROM runs WHERE status NOT IN ('completed','inconclusive','failed','cancelled') GROUP BY agent_id").fetchall()
        self.runs = {row['agent_id']: int(row['count']) for row in rows}
        self.operations = {}

    def list_agent_authoring_drafts(self):
        return self.drafts

    def count_open_agent_runs(self, agent_id):
        return self.runs.get(agent_id, 0)

    def latest_agent_operation(self, draft_id, kind):
        key = draft_id, kind
        if key not in self.operations:
            self.operations[key] = self.store.latest_agent_operation(draft_id, kind)
        return self.operations[key]

    def __getattr__(self, name):
        return getattr(self.store, name)


def management_catalog(service, state='all', agent_id=None):
    """Run the original management algorithm on one coherent read context."""
    with service.agents.read_transaction():
        context = CatalogReadContext(service.agents, agent_id=agent_id)
        owned = copy.copy(service)
        owned.agents = context
        owned.store = CatalogStore(service.store)
        owned._read_json = context.json
        dependencies = defaultdict(list)
        for path in (service.settings.repository_root / 'workflows').glob('*/*/workflow.json'):
            try:
                workflow = context.json(path)
            except (OSError, ValueError):
                continue  # Same omission policy as the existing dependency reader.
            for node in workflow.get('nodes') or []:
                if isinstance(node, dict) and node.get('agentId'):
                    dependencies[node['agentId']].append({'workflow_id': workflow.get('id'),
                        'version': workflow.get('version'), 'agent_version': node.get('agentVersion')})
        owned._workflow_dependencies = lambda key: dependencies[key]
        original_versions, versions = owned.versions, {}
        def version_list(key):
            if key not in versions:
                versions[key] = original_versions(key)
            return versions[key]
        owned.versions = version_list
        result = owned._catalog_from_context(state)
        context.finish()
        return result


class AgentCatalogProjection:
    TTL = 60.0

    def __init__(self, service, *, clock=time.monotonic):
        self.service, self.clock = service, clock
        self.lock = threading.RLock()
        self.tasks = queue.SimpleQueue()
        self.snapshots = ManifestSnapshots()
        self.epoch, self.sequence, self.completed_epoch = 0, 0, -1
        self.value, self.checked_at, self.completed = None, None, 0.0
        self.running, self.error, self.closed = False, None, False
        self.generation = service.agents._gate.generation
        self.instance = uuid.uuid4().hex
        self.timings = {}
        self.thread = threading.Thread(target=self._reader, daemon=True, name='agent-catalog-display')
        self.thread.start()

    def invalidate(self, agent_id=None):
        with self.lock:
            self.epoch += 1
            self.error = None

    def get(self, state='all', *, refresh=False):
        with self.lock:
            generation = self.service.agents._gate.generation
            if generation != self.generation:
                self.generation = generation
                self.epoch += 1
            stale = self.value is None or self.completed_epoch != self.epoch or self.clock() - self.completed >= self.TTL
            if not self.closed and not self.running and (refresh or stale and not self.error):
                self.running = True
                self.error = None
                self.tasks.put(self.epoch)
            values = copy.deepcopy(self.value or [])
            return {'items': [item for item in values if state == 'all' or item['lifecycle']['state'] == state],
                'snapshot': {'id': self.sequence, 'instance': self.instance, 'available': self.value is not None,
                    'checked_at': self.checked_at, 'stale': stale or self.error is not None,
                    'refreshing': self.running, 'error_code': self.error, 'timings_ms': dict(self.timings)}}

    def _reader(self):
        while True:
            epoch = self.tasks.get()
            if epoch is None:
                return
            try:
                values, context, timings = self._build()
                error = None
            except CatalogSourceChanged:
                values, error = None, 'agent_catalog_source_changed'
            except Exception:
                values, error = None, 'agent_catalog_refresh_failed'
            with self.lock:
                self.running = False
                if self.closed:
                    return
                if epoch != self.epoch or self.service.agents._gate.generation != self.generation:
                    # Invalidation wins over a late result. The next GET queues one refresh.
                    continue
                self.error = error
                if error is None:
                    self.value, self.timings = values, timings
                    self.completed_epoch = epoch
                    self.sequence += 1
                    self.checked_at = datetime.now(timezone.utc).isoformat()
                    self.completed = self.clock()

    def _build(self):
        start = time.perf_counter()
        with self.service.agents.read_transaction():
            locked = time.perf_counter()
            context = CatalogReadContext(self.service.agents, snapshots=self.snapshots)
            values = []
            for manifest in context.list_all():
                if manifest.get('kind') == 'platform_assistant' or not isinstance(manifest.get('execution'), dict):
                    continue
                key = str(manifest.get('slug') or '')
                lifecycle = context.lifecycle(key)
                module = context.catalog_module(key)
                values.append({'id': key, 'module': module, 'catalog_module': module,
                    'catalog_revision': int(lifecycle.get('catalog_revision') or 1),
                    'repository_module': context.repository_module(key),
                    'title': manifest.get('title'), 'summary': manifest.get('summary'),
                    'version': manifest.get('version'), 'digest': agent_digest(manifest),
                    'validation': copy.deepcopy(manifest.get('validation') or {}), 'lifecycle': lifecycle})
            context.finish()
        timings = {name: round(value * 1000, 2) for name, value in context.timings.items()}
        timings.update(lock_wait=round((locked - start) * 1000, 2), total=round((time.perf_counter() - start) * 1000, 2),
            manifest_loads=len(context.loaded), manifest_validations=self.snapshots.validation_count-context.validation_before,
            database_reads=0)
        return values, context, timings

    def close(self):
        with self.lock:
            self.closed = True
            self.tasks.put(None)
