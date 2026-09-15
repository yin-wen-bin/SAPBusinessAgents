import json
from pathlib import Path


def test_generated_manifest_is_read_only():
    manifest = json.loads((Path(__file__).parents[1] / 'agent.json').read_text(encoding='utf-8'))
    assert manifest['schemaVersion'] == 2
    assert all(step.get('readOnly', True) for step in manifest['execution']['steps'])
