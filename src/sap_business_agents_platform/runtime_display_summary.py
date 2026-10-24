"""WorkBuddy settings projections only. Never consumed by admission or execution."""
from __future__ import annotations

import copy
import hashlib
import json
import platform
import re
import time
import uuid
from pathlib import Path


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def binding(state):
    # Display invalidation, NOT an execution/qualification digest. Quality and
    # diagnostics intentionally do not rebuild forms or authorize anything.
    keys = ('enabled', 'configuration_revision', 'default_model_id', 'environment_digest',
            'service_site', 'service_site_revision', 'service_site_digest', 'authenticated',
            'authentication_revision', 'authentication_environment', 'authentication_site',
            'authentication_status', 'models', 'model_check_reuse', 'model_discovery',
            'sdk_integration_records', 'runtime_admission_policy')
    return fingerprint({key: state.get(key) for key in keys})


class WorkBuddyDisplaySummary:
    def __init__(self, manager, qualification, template, *, clock=time.monotonic):
        from .status_projection import StatusProjection
        self.manager, self.qualification, self.template = manager, qualification, template
        self._parsed_hash, self._parsed = None, None
        self._revision, self._binding, self._sequence = None, None, 0
        self.instance_id = uuid.uuid4().hex
        self.cache = StatusProjection({'workbuddy': self._read},
            {'workbuddy': lambda: manager.default_provider_id}, {'workbuddy': lambda: {}},
            ttl=5, refresh_every=2, accept_late={'workbuddy'}, clock=clock)

    def _account(self):
        service = getattr(self.manager, 'authentication_service', None)
        value = {'status': 'not_checked', 'authenticated': None, 'identity': None,
                 'capabilities': {key: False for key in ('login', 'check', 'logout', 'switch_account')},
                 'blockers': [], 'operation': None, 'display_only': True}
        if service is None or not service.lock.acquire(blocking=False):
            return value
        try:
            account = service.state.get('accounts', {}).get('workbuddy', {})
            for key in ('status', 'authenticated', 'identity', 'method', 'source', 'checked_at', 'revision', 'failure_code'):
                if key in account:
                    value[key] = copy.deepcopy(account[key])
            if service.invalid_state or account.get('invalidation_pending'):
                value.update(status='not_checked', authenticated=None, failure_code='runtime_authentication_check_required')
            # This is saved progress, never adapter capabilities()/observation().
            operations = [op for op in service.state.get('operations', {}).values()
                          if op.get('provider_id') == 'workbuddy']
            if operations:
                op = max(operations, key=lambda item: item.get('created_at', ''))
                value['operation'] = {key: copy.deepcopy(op[key]) for key in (
                    'operation_id', 'status', 'phase', 'failure_code', 'cleanup_complete') if key in op}
            return value
        finally:
            service.lock.release()

    def _read(self):
        started = time.perf_counter()
        path = self.manager.workbuddy.environment.state_path
        raw = Path(path).read_bytes()
        read_at = time.perf_counter()
        content = hashlib.sha256(raw).hexdigest()
        if content != self._parsed_hash:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError('runtime_display_state_invalid')
            self._parsed_hash, self._parsed = content, parsed
        state = self._parsed
        revision, enabled = state.get('revision', 0), state.get('enabled')
        if type(revision) is not int or revision < 0 or not isinstance(enabled, bool):
            raise ValueError('runtime_display_state_invalid')
        if self._revision is not None and revision < self._revision:
            raise ValueError('runtime_display_revision_regressed')
        key = state.get('environment_digest')
        versions = {}
        if isinstance(key, str) and re.fullmatch('[0-9a-f]{64}', key):
            manifest_path = Path(path).parent / 'releases' / key / 'environment.json'
            if not manifest_path.resolve().is_relative_to((Path(path).parent / 'releases').resolve()):
                raise ValueError('runtime_display_path_invalid')
            try:
                manifest = json.loads(manifest_path.read_bytes())
                versions = {'recorded_sdk_version': manifest.get('sdk_version'),
                            'recorded_cli_version': manifest.get('cli_version')}
            except (OSError, ValueError, AttributeError):
                versions = {'recorded_sdk_version': None, 'recorded_cli_version': None}
        source_binding = binding(state)
        if self._binding is not None and source_binding != self._binding:
            self.qualification.invalidate(['workbuddy'])
        self._revision, self._binding = revision, source_binding
        self._sequence += 1
        from .workbuddy_site import configuration
        site = configuration(state)
        discovered = state.get('model_discovery') or {}
        models = []
        if (discovered.get('source') == 'locked_cli_help' and discovered.get('environment_digest') == key
                and all(discovered.get(name) == site.get(name) for name in (
                    'service_site', 'service_site_revision', 'service_site_digest'))):
            for model in dict.fromkeys(discovered.get('models', [])):
                if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', model):
                    raise ValueError('runtime_display_catalog_invalid')
                check = (state.get('models') or {}).get(model) or {}
                models.append({'model_id': model, 'display_name': model,
                    'selected': model == state.get('default_model_id'), 'source': 'locked_cli_help',
                    'selectable': False, 'check_status': 'not_checked', 'display_pending': True,
                    'last_check_status': check.get('status'), 'last_check_at': check.get('checked_at')})
        return {'enabled': enabled, 'revision': revision, 'binding': source_binding,
                'snapshot_id': self._sequence, 'default_model_id': state.get('default_model_id'),
                'environment_digest': key, 'runtime_configuration_revision': state.get('configuration_revision', 0),
                'authentication': self._account(), 'models': models, **site, **versions,
                'timings_ms': {'read': round((read_at-started)*1000, 2),
                               'project': round((time.perf_counter()-read_at)*1000, 2)}}

    def _values(self):
        metadata = self.cache.get('workbuddy')
        # Unknown/failed metadata must not launch another full inspection.
        complete = self.qualification.get('workbuddy', refresh=not metadata['status_refresh']['stale'])
        fresh = (not metadata['status_refresh']['stale'] and not complete['status_refresh']['stale']
                 and metadata.get('binding') == complete.get('_settings_binding'))
        if fresh and getattr(self.manager, 'authentication_service', None):
            account, checked = metadata.get('authentication') or {}, complete.get('authentication') or {}
            # A completed negative context check is authoritative display data,
            # not an unfinished refresh. Never promote a positive old result
            # over a changed account/revision. Execution still checks context.
            reviewed_negative = (isinstance(checked, dict) and checked.get('authenticated') is not True
                and checked.get('authentication_context_status') == 'check_required')
            fresh = isinstance(account, dict) and isinstance(checked, dict) and account.get('revision') == checked.get('revision') and (
                account.get('status') == checked.get('status') or reviewed_negative)
        return metadata, complete, fresh

    def _status(self, metadata):
        return {**metadata['status_refresh'], 'snapshot_id': metadata.get('snapshot_id'),
                'instance_id': self.instance_id, 'binding': metadata.get('binding'), 'timings_ms': metadata.get('timings_ms')}

    @staticmethod
    def _same_binding(metadata, complete):
        # Reuse facts only for display, never eligibility. A different account,
        # site, model or installation must not inherit a previous confirmation.
        account, checked = metadata.get('authentication'), complete.get('authentication')
        return (bool(metadata.get('binding')) and metadata['binding'] == complete.get('_settings_binding')
                and isinstance(account, dict) and isinstance(checked, dict)
                and all(account.get(key) == checked.get(key) for key in ('revision', 'status')))

    def get(self):
        metadata, complete, fresh = self._values()
        value = {**self.template(), **complete}
        for key in ('_settings_catalog', '_settings_binding'):
            value.pop(key, None)
        value.update(display_status=self._status(metadata), provider_implemented=True,
                     platform_supported=platform.system() == 'Windows', model_discovery_supported=True,
                     selected=self.manager.default_provider_id == 'workbuddy')
        for key in ('default_model_id', 'environment_digest', 'runtime_configuration_revision',
                    'service_site', 'service_site_revision', 'service_site_digest',
                    'service_site_migration_required', 'recorded_sdk_version', 'recorded_cli_version'):
            if key in metadata:
                value[key] = metadata[key]
        value['enabled'] = metadata.get('enabled') if not metadata['status_refresh']['stale'] else None
        if not fresh:
            value.update(can_enable=False, selectable=False, authenticated=None, model_check_supported=False,
                         authentication=copy.deepcopy(metadata.get('authentication') or self._unknown_auth()))
            if self._same_binding(metadata, complete):
                account = copy.deepcopy(complete.get('authentication') or value['authentication'])
                account.update(display_only=True, verification_pending=True,
                               capabilities={key: False for key in ('login', 'check', 'logout', 'switch_account')})
                value['authentication'] = account
            value['status_refresh'] = {**complete['status_refresh'], 'stale': True}
            value['blockers'] = list(dict.fromkeys([*value.get('blockers', []), 'runtime_status_pending']))
        elif (getattr(self.manager, 'authentication_service', None)
              and (value.get('authentication') or {}).get('authenticated') is not True):
            # A finished display check may report a real authentication blocker.
            # Its capabilities permit explicit review, never model use/enablement.
            value.update(can_enable=False, selectable=False, authenticated=None, model_check_supported=False)
            value['blockers'] = list(dict.fromkeys([*value.get('blockers', []), 'runtime_authentication_check_required']))
        value['display_status']['last_confirmed_enabled'] = metadata.get('enabled')
        value['control_status'] = {'status': 'ready' if not metadata['status_refresh']['stale'] else 'unknown',
            'revision': metadata.get('revision'), 'checked_at': metadata['status_refresh']['checked_at'],
            'running': metadata['status_refresh']['running']}
        return value

    @staticmethod
    def _unknown_auth():
        return {'status': 'not_checked', 'authenticated': None, 'capabilities': {}, 'display_only': True}

    def models(self):
        metadata, complete, fresh = self._values()
        fresh = fresh and isinstance(complete.get('_settings_catalog'), dict)
        retained = (not fresh and self._same_binding(metadata, complete)
                    and isinstance(complete.get('_settings_catalog'), dict))
        if fresh or retained:
            result = copy.deepcopy(complete['_settings_catalog'])
            if retained:
                result.update(catalog_status='pending', display_only=True)
                for item in result.get('items', []):
                    item.update(selectable=False, display_pending=True, last_confirmed=True)
            elif (getattr(self.manager, 'authentication_service', None)
                  and (complete.get('authentication') or {}).get('authenticated') is not True):
                for item in result.get('items', []):
                    item.update(selectable=False, compatibility_applicability={'status': 'authentication_required'})
        else:
            result = {'provider_id': 'workbuddy', 'items': copy.deepcopy(metadata.get('models', [])),
                'default_model_id': metadata.get('default_model_id'), 'catalog_status': 'pending',
                'model_catalog_complete': False, 'source': 'locked_cli_help',
                **{key: metadata.get(key) for key in ('environment_digest', 'service_site', 'service_site_revision', 'service_site_digest')}}
        result['display_status'] = self._status(metadata)
        result['status_refresh'] = {**complete['status_refresh'], 'stale': not fresh}
        return result

    def invalidate(self):
        self.cache.invalidate(['workbuddy'])

    def close(self):
        self.cache.close()
