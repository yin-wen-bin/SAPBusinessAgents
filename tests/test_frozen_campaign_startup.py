"""Real immutable imports plus simulated dispatch; no installed SDK execution."""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('frozen_startup_test', ROOT/'scripts/service-startup.py')
startup = importlib.util.module_from_spec(spec); spec.loader.exec_module(startup)


@pytest.fixture(scope='module')
def release():
    return startup.freeze(ROOT, startup.source_manifest(ROOT))


def probe(archive, state, mode):
    result = subprocess.run([sys.executable, '-I', '-B', str(ROOT/'tests/fixtures/frozen_campaign_start.py'),
                             str(archive), str(state), mode], cwd=state.parent,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize('provider', ['codex', 'workbuddy'])
def test_frozen_campaign_reaches_mock_baseline_without_checkout_imports(release,tmp_path,provider):
    value = probe(release, tmp_path/provider, provider)
    assert value == {'provider': provider, 'reached': 'mock baseline', 'verdict': 'NOT_TESTED', 'model_calls': 0, 'sap_calls': 0}


def test_preloaded_mutable_scripts_namespace_is_rejected(release,tmp_path):
    assert probe(release,tmp_path/'foreign','foreign')['error'] == 'startup_import_source_mismatch'


@pytest.mark.parametrize('mode', ['missing','tampered'])
def test_missing_or_modified_helper_cannot_use_mutable_checkout(release,tmp_path,mode):
    candidate=tmp_path/'candidate'
    shutil.copytree(release,candidate)
    helper=candidate/'source/scripts/run_three_stage_acceptance.py'
    manifest=json.loads((candidate/'manifest.json').read_text())
    if mode=='missing':
        helper.unlink()
        manifest['files'].pop('scripts/run_three_stage_acceptance.py')
        body={key:value for key,value in manifest.items() if key!='digest'}
        manifest['digest']=startup.control.digest(body)
        (candidate/'manifest.json').write_text(json.dumps(manifest))
    else:
        helper.write_text("raise AssertionError('tampered source must not execute')")
    failure=probe(candidate,tmp_path/'state',mode)
    if mode=='missing':
        assert failure['type']=='ModuleNotFoundError' and 'scripts.run_three_stage_acceptance' in failure['error']
    else:
        assert failure['error']=='startup_archive_changed'
