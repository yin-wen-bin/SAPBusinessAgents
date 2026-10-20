"""Legacy CLI shim. Shared protocol execution has one entry point."""
import importlib.util
from pathlib import Path

from sap_business_agents_platform.runtime_validation import ValidationError
from sap_business_agents_platform.runtime_validation_store import read


def delegate(root, batch, manifest=None):
    root = Path(root)
    state_file = root / '.local-data/runtimes/workbuddy/state.json'
    state = read(state_file) if state_file.exists() else {}
    if manifest is None:
        if state.get('qualification_protocol'):
            raise ValidationError('validation_use_shared_runner')
        return None  # Original protocol only; retired at controlled activation.
    spec = importlib.util.spec_from_file_location('shared_validation_cli', Path(__file__).with_name('validate-ai-runtime.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main(['run', '--root', str(root), '--batch', str(batch), '--manifest', str(manifest), '--execute'])
