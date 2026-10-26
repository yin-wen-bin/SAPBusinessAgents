"""SDK-independent terminal feedback and bounded, idempotent correction checks.

Never manufactures a terminal, repairs a candidate, or substitutes report PASS.
Only trusted Schema metadata and safe issue signatures leave this component.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

from .runtime_diagnostics import schema_issues, schema_definition
from .runtime_report_convergence import advance

VERSION = 'sapba.runtime-terminal/1'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':')).encode()).hexdigest()


def _field(schema, path):
    return schema_definition(schema, path) or {}


def handoff_requirements(schema):
    result = {}
    for name in schema.get('required', []):
        if name not in schema.get('properties', {}): continue
        definition = _field(schema, '/' + name)
        item = {k: copy.deepcopy(v) for k, v in definition.items() if k in {'type', 'enum', 'description'}}
        if 'type' not in item and isinstance(definition.get('anyOf'), list):
            types = [branch.get('type') for branch in definition['anyOf']]
            if types and all(isinstance(kind, str) for kind in types): item['type'] = types
        result[name] = item
    return result


def handoff_instructions(schema):
    return ('\nComplete terminal handoff: return the entire frozen root object, not a report or clarification fragment. '
        'Place the exact validated_report OBJECT under presentation, including validation_ref and platform verification status. '
        'Copy ALL of its block fields, including empty items/columns/rows/metrics arrays and nullable fields; do not reconstruct or shorten it. '
        'Do not JSON.stringify, json.dumps, quote or escape that object. The tool argument itself is already JSON. '
        'Do not invent evidence, default business fields or repeat external reads to repair structure. '
        'Required root members and their declared types: ' + json.dumps(handoff_requirements(schema), ensure_ascii=False))


def repair_feedback(schema, issues):
    """Describe constraints, not a guessed replacement value or user content."""
    fields = []
    for issue in issues[:50]:
        definition = _field(schema, issue['path'])
        item = dict(issue)
        if definition:
            item['expected_type'] = definition.get('type') or [v.get('type', 'declared_schema')
                for v in definition.get('anyOf', [])] or 'declared_schema'
            if isinstance(definition.get('enum'), list):
                item['allowed_values'] = copy.deepcopy(definition['enum'])
            if isinstance(definition.get('description'), str):
                item['field_instruction'] = definition['description'][:1000]
            if issue.get('constraint') == 'additionalProperties':
                item['allowed_properties'] = list(definition.get('properties', {}))
                item['field_instruction'] = 'Remove undeclared properties from this object; keep declared content and evidence unchanged.'
        fields.append(item)
    return {'version': VERSION, 'schema_sha256': _digest(schema),
        'required_root_members': handoff_requirements(schema),
        'missing_root_members': [i['path'][1:] for i in issues
            if i.get('constraint') == 'required' and i.get('path', '').count('/') == 1],
        'terminal_validated': not issues, 'validation_issues': fields,
        'instruction': 'Correct the complete final object against the unchanged Schema. '
            'Use only declared enum members and JSON types. JSON null is not the string "null". '
            'Reuse the unchanged validated report OBJECT as presentation, never a JSON-encoded string. '
            'Remove only the extra serialization layer in your new submission; the platform will not coerce it. '
            'Report validation alone is not '
            'a complete terminal. Do not repeat evidence reads to repair an output-structure error.',
        'retry_scope': 'terminal_only', 'extends_deadline': False}


class TerminalChecks:
    """One owned attempt. Observation is not another model submission."""
    def __init__(self, schema):
        self.schema = copy.deepcopy(schema)
        self.state = {}
        self.calls = {}
        self.last_rejected = None
        self.native_digest = None
        self.v2 = "input_kind" not in schema.get("properties", {}) and "status" in schema.get("properties", {})
        self.handoff_deadline = None
        self.repair_deadline = None
        self.last_submission = None
        self.validated_report = None

    def report_ready(self, hard_deadline, reserve=300, *, now=None, validated_report=None):
        import time
        now = time.monotonic() if now is None else now
        if validated_report is not None:
            declared = {**self.schema.get('properties', {}).get('presentation', {}),
                        '$defs':self.schema.get('$defs', {})}
            if not isinstance(validated_report, dict) or schema_issues(validated_report, declared):
                raise ValueError('runtime_validated_report_schema_mismatch')
            self.validated_report = copy.deepcopy(validated_report)
        if self.v2 and self.handoff_deadline is None:
            self.handoff_deadline = min(hard_deadline, now + reserve)

    def remaining(self, hard_deadline, *, now=None):
        import time
        now = time.monotonic() if now is None else now
        return max(0, min(x for x in (hard_deadline, self.handoff_deadline, self.repair_deadline) if x is not None) - now)

    def rejected(self, hard_deadline, *, now=None):
        import time
        now = time.monotonic() if now is None else now
        if self.v2:
            self.repair_deadline = min(hard_deadline, self.handoff_deadline or hard_deadline, now + 120)

    def check(self, value, *, submission_id, origin, phase):
        if origin not in {'formatter_hook', 'message_observation', 'native_terminal'}:
            raise ValueError('runtime_terminal_protocol_invalid')
        if phase != ('native_terminal' if origin == 'native_terminal' else 'native_candidate'):
            raise ValueError('runtime_terminal_protocol_invalid')
        if origin != 'native_terminal' and (not isinstance(submission_id, str)
                or not re.fullmatch('[a-f0-9]{64}', submission_id)):
            raise ValueError('runtime_terminal_protocol_invalid')
        issues = schema_issues(value, self.schema)
        copy_mismatch = (self.validated_report is not None and isinstance(value,dict)
                         and value.get('presentation') != self.validated_report)
        if copy_mismatch:
            issues.append({'code':'runtime_terminal_report_changed','path':'/presentation',
                           'constraint':'validated_report_identity'})
        if origin == 'native_terminal':
            self.native_digest = None if issues else _digest(value)
        if origin == 'formatter_hook':
            fingerprint = _digest(value)
            if submission_id in self.calls:
                if self.calls[submission_id] != fingerprint:
                    raise ValueError('runtime_terminal_protocol_invalid')
            else:
                self.calls[submission_id] = fingerprint
                repeated = self.last_submission == (fingerprint, _digest(issues))
                self.state = advance(self.state, issues=issues, acceptance=False,
                    legacy_progress=None, relevant_generation=_digest(self.schema))
                if self.v2 and issues and repeated:
                    self.state.update(terminal=True, stop_code="report_correction_stalled")
                self.last_submission = (fingerprint, _digest(issues))
            if issues:
                self.last_rejected = copy.deepcopy(issues)
        # An empty native terminal must not erase the explanatory candidate errors.
        result = {'repair_feedback': repair_feedback(self.schema, issues),
            'handoff_diagnostic': {'schema_sha256': _digest(self.schema),
                'root_fields': root_shapes(value, self.schema)},
            'convergence': copy.deepcopy(self.state), 'stop': self.state.get('terminal') is True}
        if copy_mismatch:
            result['repair_feedback']['report_handoff'] = {
                'validation_ref':self.validated_report.get('validation_ref'),
                'validated_report_sha256':_digest(self.validated_report),
                'instruction':'Copy the complete validated_report returned by sap_final_report_validate as presentation. Keep every declared block field, including empty arrays and nulls. Do not regenerate the report, omit defaults, borrow another validation_ref or reread SAP. The platform will not fill missing fields.'}
        return result

    def accepts_native_result(self, text):
        if self.native_digest is None or self.state.get('terminal') is True:
            return False
        try:
            value = json.loads(text)
            return not schema_issues(value, self.schema) and _digest(value) == self.native_digest
        except (ValueError, TypeError):
            return False


def root_shapes(value, schema):
    """Only declared names and JSON types; never persist candidate contents."""
    def kind(v):
        return ('null' if v is None else 'boolean' if type(v) is bool else
                'object' if isinstance(v, dict) else 'array' if isinstance(v, list) else
                'string' if isinstance(v, str) else 'number')
    return {name: {'present': isinstance(value, dict) and name in value,
        'json_type': kind(value[name]) if isinstance(value, dict) and name in value else 'missing'}
        for name in schema.get('properties', {})}
