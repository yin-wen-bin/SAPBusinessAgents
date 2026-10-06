"""Compatibility facade; citations are checked by the shared business contract."""
from .runtime_role_contract import *
from .runtime_role_contract import decode as _decode
from .workbuddy_compat import legacy
decode = legacy(_decode)
