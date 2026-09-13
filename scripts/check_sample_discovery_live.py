"""Explicit, isolated SDK + read-only SAP sample check; never publishes a draft.

Source database is opened read-only. A fresh data root is mandatory. Business
values remain in the isolated platform evidence store, never stdout/report.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
import re
from pathlib import Path
import shutil
import sqlite3
import time

import uvicorn

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.models import (
    AgentSampleDiscoveryRequest,
    TERMINAL_STATUSES,
)
from sap_business_agents_platform.relationships import apply_advisory_relationship_policy


async def main(args):
    root = Path(__file__).resolve().parents[1]
    source_root = Path(args.source_root).resolve() if args.source_root else root
    target = (root / '.local-data' / args.name).resolve()
    if target.parent != (root / '.local-data').resolve() or target.exists():
        raise ValueError('A fresh immediate child of .local-data is required')
    with sqlite3.connect(f'file:{source_root.as_posix()}/.local-data/platform.sqlite3?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        draft = dict(connection.execute('SELECT * FROM agent_authoring_drafts WHERE draft_id=?', (args.draft_id,)).fetchone())
        package = json.loads(connection.execute('SELECT package_json FROM agent_authoring_revisions WHERE draft_id=? AND revision=?',
                            (args.draft_id, draft['revision'])).fetchone()[0])
    if args.advisory_copy:
        apply_advisory_relationship_policy(package['manifest'])
    draft.update(metadata=json.loads(draft['metadata_json']), validation={}, validation_run_id=None, thread_id=None)
    target.mkdir()
    (target / 'sdk-runtimes').mkdir()
    shutil.copy2(source_root / '.local-data/sdk-runtimes/default.json', target / 'sdk-runtimes/default.json')
    settings = replace(Settings.from_env(root), data_root=target, draft_root=target / 'drafts',
                       internal_api_url=f'http://127.0.0.1:{args.port}')
    app = create_app(settings)
    draft['path'] = str(settings.draft_root / args.draft_id)
    app.state.store.save_agent_authoring_draft(draft, package=package)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=args.port, log_level='error', access_log=False))
    serving = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(30):
            while not server.started:
                if serving.done():
                    serving.result()
                    raise RuntimeError('isolated_api_start_failed')
                await asyncio.sleep(.1)
        jobs = app.state.agent_sample_discovery
        run = jobs.start(args.draft_id, AgentSampleDiscoveryRequest(expectedRevision=draft['revision'], input={}, selectedFields=args.fields, requestId='live-sample-check'))
        print(json.dumps({'stage': 'started', 'run_id': run['run_id'], 'port': args.port}), flush=True)
        await jobs.tasks[run['run_id']]
        result = jobs.get(args.draft_id, run['run_id'])
        safe = {k: result.get(k) for k in ['run_id', 'status', 'model', 'reasoning_effort', 'timeout_seconds',
                'elapsed_seconds', 'candidate_count', 'query_count', 'selection_method', 'failed_stage', 'codes']}
        safe['verified_parameter_count'] = len(result.get('field_sources') or {})
        safe['evidence_refs'] = result.get('evidence_refs', [])
        safe['input_sha256'] = hashlib.sha256(json.dumps(result.get('input', {}), sort_keys=True).encode()).hexdigest()
        safe['selected_fields'] = args.fields
        date_fields = [key for key, source in result.get('field_sources', {}).items() if source.get('normalization') == 'sap_date_to_iso_date']
        safe['normalized_date_count'] = len(date_fields)
        safe['normalized_dates_are_iso'] = all(re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(result['input'][key])) is not None for key in date_fields)
        safe['relationship_policy'] = (package['manifest'].get('execution') or {}).get('relationshipPolicy') or 'legacy_enforced'
        trial_succeeded = True
        if args.run_trial and result['status'] == 'ready':
            trial = await app.state.agent_lifecycle.live_validate(
                args.draft_id,
                input_value=result.get('input') or {},
                expected_revision=int(draft['revision']),
                request_id='isolated-advisory-trial',
            )
            trial_run_id = str(trial['run_id'])
            deadline = time.monotonic() + settings.deterministic_run_seconds + 30
            while app.state.store.get_run(trial_run_id).status not in TERMINAL_STATUSES:
                if time.monotonic() >= deadline:
                    raise TimeoutError('isolated_trial_timeout')
                await asyncio.sleep(.25)
            trial_report = app.state.agent_lifecycle.validation_report(args.draft_id)
            trial_run = app.state.store.get_run(trial_run_id)
            trial_result = trial_run.result
            trial_error_codes = []
            if isinstance(trial_run.error, dict) and trial_run.error.get('code'):
                trial_error_codes.append(str(trial_run.error['code']))
            trial_error_codes.extend(
                str(item.get('code') or '')
                for item in ((trial_result.errors if trial_result else []) or [])
                if isinstance(item, dict) and item.get('code')
            )
            safe['trial'] = {
                'run_id': trial_run_id,
                'status': trial_run.status.value,
                'verdict': (trial_report.get('trial') or {}).get('verdict'),
                'error_codes': sorted(set(trial_error_codes)),
                'sap_operations': sorted({str(item.get('operation') or '') for item in ((trial_result.tool_calls if trial_result else []) or [])}),
                'sap_read_only_operations': all(
                    item.get('tool') == 'sap_read'
                    and item.get('operation') in {'execute_plan', 'execute_get'}
                    for item in ((trial_result.tool_calls if trial_result else []) or [])
                ),
                'advisory_codes': sorted({str(item.get('code') or '') for step in ((trial_result.steps if trial_result else []) or []) for item in (step.get('advisories') or []) if isinstance(item, dict)}),
            }
            trial_succeeded = trial_run.status.value in {'completed', 'inconclusive'}
        (target / 'sample-check-result.json').write_text(json.dumps(safe, indent=2), encoding='utf-8')
        print(json.dumps(safe), flush=True)
        return result['status'] == 'ready' and trial_succeeded
    finally:
        server.should_exit = True
        await serving


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--draft-id', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--port', type=int, default=8776)
    parser.add_argument('--fields', nargs='+', default=None)
    parser.add_argument('--source-root')
    parser.add_argument('--advisory-copy', action='store_true')
    parser.add_argument('--run-trial', action='store_true')
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(main(args)) else 1)
