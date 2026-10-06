"""Compatibility facade over the canonical read-scope contract."""
from .runtime_query_contract import *
from . import runtime_query_contract as _contract
from .workbuddy_compat import legacy
plans = legacy(_contract.plans)
preserve_grounding = legacy(_contract.preserve_grounding)
class ReadScope(_contract.ReadScope):
    pass
for _name in ("__init__", "check", "check_skill", "record_schema", "remember"):
    if hasattr(_contract.ReadScope, _name):
        setattr(ReadScope, _name, legacy(getattr(_contract.ReadScope, _name)))
