"""Fresh-process Campaign startup probe. Every execution boundary is a stub."""
import asyncio
import json
import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

archive, state = Path(sys.argv[1]), Path(sys.argv[2])
mode = sys.argv[3]
boot = runpy.run_path(str(archive / 'source/scripts/service-startup.py'))
manifest = json.loads((archive / 'manifest.json').read_text())
if mode == 'foreign':
    foreign = ModuleType('scripts')
    foreign.__path__ = [str(state / 'mutable-checkout/scripts')]
    sys.modules['scripts'] = foreign
try:
    boot['bind_frozen_imports'](archive, manifest)
except Exception as exc:
    print(json.dumps({'error': str(exc), 'type': type(exc).__name__}))
    raise SystemExit(0 if mode in {'foreign', 'missing', 'tampered'} else 1)
assert mode in {'codex', 'workbuddy'}

from sap_business_agents_platform.agent_acceptance_campaigns import AgentAcceptanceJobs, AcceptanceCampaignOperationalError, _contract
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.config import Settings

async def exercise():
    store = RunStore(state / 'isolated.sqlite3')
    draft_id, campaign_id = 'synthetic-draft', 'synthetic-campaign'
    package = {'manifest': {'slug': 'offline-acceptance', 'module': 'Common', 'version': '0.1.0',
        'title': {'zh': '离线测试', 'en': 'Offline test'}, 'validation': {'acceptanceMode': 'three_stage'},
        'execution': {'inputSchema': {'type': 'object'}, 'steps': [],
                      'acceptance': {'schemaVersion': '2.0', 'businessKeys': ['id'], 'facts': [], 'metrics': []}}}, 'rules': None}
    store.save_agent_authoring_draft({'draft_id': draft_id, 'agent_id': 'offline-acceptance', 'source_type': 'blank',
        'status': 'draft', 'revision': 1, 'path': str(state / 'draft'), 'metadata': {}}, package=package)
    operation = store.reserve_agent_operation(draft_id, 1, 'formal_acceptance')
    case = {'case_id': 'one', 'input': {'id': 'synthetic'}, 'sensitive_inputs': {}, 'sensitive_fingerprints': {}}
    runtime = {'provider_id': mode, 'model': 'offline-model', 'configuration_digest': 'frozen'}
    store.create_agent_acceptance_campaign(campaign={'campaign_id': campaign_id, 'draft_id': draft_id,
        'revision': 1, 'operation_id': operation['operation_id'], 'request_id': 'isolated-request',
        'request_hash': 'isolated-hash', 'agent_id': 'offline-acceptance', 'acceptance_mode': 'three_stage',
        'runtime': runtime, 'digests': {}, 'artifact_dir': str(state / 'report')}, cases=[case])
    submitted = []
    async def submit(payload, **kwargs):
        assert kwargs['direct_baseline'] is True and kwargs['hard_limit_seconds'] == 600
        assert kwargs['runtime'] == runtime and payload.acceptance_spec is not None
        submitted.append('baseline-stub')
        return 'baseline-stub'
    async def cancel(*args, **kwargs):
        assert kwargs['timeout'] == 10
        return True
    coordinator = SimpleNamespace(submit_acceptance_query=submit, cancel_acceptance_run=cancel)
    jobs = AgentAcceptanceJobs(SimpleNamespace(data_root=state), store, None, coordinator, None)
    async def wait(*args, **kwargs):
        assert args[-1] == 600 and kwargs['return_waiting_input'] is True
        raise AcceptanceCampaignOperationalError('offline_baseline_boundary_observed', failure_stage='baseline')
    jobs._wait_run = wait
    jobs._run_diagnostics = lambda *args: {}
    # No subprocess or HTTP boundary may be reached by this synthetic task.
    with patch('subprocess.Popen', side_effect=AssertionError('external process forbidden')), \
         patch('httpx.AsyncClient.send', side_effect=AssertionError('network forbidden')):
        await jobs._run(campaign_id, draft_id, 1, operation['operation_id'], package, _contract(package['manifest']), runtime, [case])
    actual = jobs.get(draft_id, campaign_id)
    assert submitted == ['baseline-stub']
    assert actual['cases'][0]['baseline_run_id'] == 'baseline-stub'
    assert actual['cases'][0]['free_query_run_id'] is None and actual['cases'][0]['fixed_agent_run_id'] is None
    assert actual['report']['error']['code'] == 'offline_baseline_boundary_observed'
    assert actual['report']['verdict'] == 'NOT_TESTED' and not actual['report']['executable']
    assert actual['report']['diagnostics']['cleanup_complete'] is True
    assert store.get_agent_operation(draft_id) is None
    boot['verify_loaded_sources'](archive, manifest)
    print(json.dumps({'provider': mode, 'reached': 'mock baseline', 'verdict': 'NOT_TESTED', 'model_calls': 0, 'sap_calls': 0}))

asyncio.run(exercise())
