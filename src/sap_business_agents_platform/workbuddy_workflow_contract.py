"""Compatibility facade; the proposal contract belongs to the platform."""
from .runtime_workflow_contract import *
from .runtime_workflow_contract import decode as _decode
from .workbuddy_compat import legacy
decode = legacy(_decode)
