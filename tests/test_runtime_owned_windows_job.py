"""Real owned process-tree cleanup; no SDK, model or SAP invocation."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from sap_business_agents_platform.runtime_execution import owned_client,RuntimeExecutionError
from sap_business_agents_platform.workbuddy_job import process_identity


@pytest.mark.skipif(os.name != 'nt',reason='Windows native process ownership')
def test_owned_client_reaps_its_parent_and_child_before_confirming_cleanup(tmp_path):
    go=tmp_path/'go';child_file=tmp_path/'child.json'
    code=('import pathlib,subprocess,sys,time,json;'
          'go=pathlib.Path(sys.argv[1]);out=pathlib.Path(sys.argv[2]);'
          '\nwhile not go.exists(): time.sleep(.01)\n'
          'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);'
          'out.write_text(json.dumps(child.pid));time.sleep(60)')
    class Client:
        def __init__(self):
            self._client=SimpleNamespace(_sync=SimpleNamespace(_proc=None));self.closed=False
        async def __aenter__(self):
            self._client._sync._proc=subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-I','-c',code,str(go),str(child_file)],
                creationflags=subprocess.CREATE_NO_WINDOW)
            return self
        async def close(self):
            self._client._sync._proc.wait(timeout=1)
            self.closed=True
    client=Client();state={}
    async def run():
        async with owned_client(client,cleanup_state=state):
            go.write_text('start')
            for _ in range(300):
                if child_file.exists():break
                await asyncio.sleep(.01)
            assert child_file.exists()
            assert process_identity(json.loads(child_file.read_text())) is not None
        assert client.closed and client._client._sync._proc.poll() is not None
        assert state['process_ownership']=='windows_job'
        assert state['process_tree_empty'] is True and state['complete'] is True
    try:
        asyncio.run(run())
    finally:
        process=client._client._sync._proc
        if process and process.poll() is None:process.kill();process.wait(timeout=2)


@pytest.mark.skipif(os.name != 'nt',reason='Windows native process ownership')
def test_tree_cleanup_error_still_closes_sdk_and_cannot_claim_confirmation(monkeypatch):
    from sap_business_agents_platform import workbuddy_job
    original=workbuddy_job.WindowsJob
    class RefusingJob(original):
        def terminate(self):raise OSError('synthetic termination failure')
    monkeypatch.setattr(workbuddy_job,'WindowsJob',RefusingJob)
    class Client:
        closed=False
        async def __aenter__(self):
            self.process=subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-I','-c','import time;time.sleep(60)'],
                creationflags=subprocess.CREATE_NO_WINDOW)
            self._client=SimpleNamespace(_sync=SimpleNamespace(_proc=self.process))
            return self
        async def close(self):
            self.process.kill();self.process.wait(timeout=2);self.closed=True
    client=Client();state={}
    async def run():
        with pytest.raises(RuntimeExecutionError,match='runtime_cleanup_incomplete'):
            async with owned_client(client,cleanup_state=state):pass
    asyncio.run(run())
    assert client.closed and state['complete'] is False
    assert state['failure_code']=='runtime_cleanup_incomplete'
