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
        'terminal_validated': not issues, 'validation_issues': fields,
        'instruction': 'Correct the complete final object against the unchanged Schema. '
            'Use only declared enum members and JSON types. JSON null is not the string "null". '
            'Reuse the unchanged validated report as presentation; report validation alone is not '
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

    def check(self, value, *, submission_id, origin, phase):
        if origin not in {'formatter_hook', 'message_observation', 'native_terminal'}:
            raise ValueError('runtime_terminal_protocol_invalid')
        if phase != ('native_terminal' if origin == 'native_terminal' else 'native_candidate'):
            raise ValueError('runtime_terminal_protocol_invalid')
        if origin != 'native_terminal' and (not isinstance(submission_id, str)
                or not re.fullmatch('[a-f0-9]{64}', submission_id)):
            raise ValueError('runtime_terminal_protocol_invalid')
        issues = schema_issues(value, self.schema)
        if origin == 'native_terminal':
            self.native_digest = None if issues else _digest(value)
        if origin == 'formatter_hook':
            fingerprint = _digest(value)
            if submission_id in self.calls:
                if self.calls[submission_id] != fingerprint:
                    raise ValueError('runtime_terminal_protocol_invalid')
            else:
                self.calls[submission_id] = fingerprint
                self.state = advance(self.state, issues=issues, acceptance=False,
                    legacy_progress=None, relevant_generation=_digest(self.schema))
            if issues:
                self.last_rejected = copy.deepcopy(issues)
        # An empty native terminal must not erase the explanatory candidate errors.
        result = {'repair_feedback': repair_feedback(self.schema, issues),
            'convergence': copy.deepcopy(self.state), 'stop': self.state.get('terminal') is True}
        return result

    def accepts_native_result(self, text):
        if self.native_digest is None or self.state.get('terminal') is True:
            return False
        try:
            value = json.loads(text)
            return not schema_issues(value, self.schema) and _digest(value) == self.native_digest
        except (ValueError, TypeError):
            return False
