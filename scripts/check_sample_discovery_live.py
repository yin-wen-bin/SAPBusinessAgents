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

import uvicorn

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.models import AgentSampleDiscoveryRequest


async def main(args):
    root = Path(__file__).resolve().parents[1]
    target = (root / '.local-data' / args.name).resolve()
    if target.parent != (root / '.local-data').resolve() or target.exists():
        raise ValueError('A fresh immediate child of .local-data is required')
    with sqlite3.connect(f'file:{root.as_posix()}/.local-data/platform.sqlite3?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        draft = dict(connection.execute('SELECT * FROM agent_authoring_drafts WHERE draft_id=?', (args.draft_id,)).fetchone())
        package = json.loads(connection.execute('SELECT package_json FROM agent_authoring_revisions WHERE draft_id=? AND revision=?',
                            (args.draft_id, draft['revision'])).fetchone()[0])
    draft.update(metadata=json.loads(draft['metadata_json']), validation={}, validation_run_id=None, thread_id=None)
    target.mkdir()
    (target / 'sdk-runtimes').mkdir()
    shutil.copy2(root / '.local-data/sdk-runtimes/default.json', target / 'sdk-runtimes/default.json')
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
        (target / 'sample-check-result.json').write_text(json.dumps(safe, indent=2), encoding='utf-8')
        print(json.dumps(safe), flush=True)
        return result['status'] == 'ready'
    finally:
        server.should_exit = True
        await serving


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--draft-id', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--port', type=int, default=8776)
    parser.add_argument('--fields', nargs='+', default=None)
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(main(args)) else 1)
