"""Real disposable local processes only. No API, SDK, installation or network."""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from sap_business_agents_platform.sdk_activation_handoff import create_executor, inventory, verify_executor


@pytest.mark.skipif(os.name!='nt',reason='Windows process ancestry contract')
def test_executor_survives_owned_parent_tree_cleanup(tmp_path):
    script=tmp_path/'owned-local-fixture.py'
    script.write_text('import sys,time\nfrom pathlib import Path\np=Path(sys.argv[1]);p.write_text("ready")\n'
                      'end=time.monotonic()+45\nwhile time.monotonic()<end and not p.with_suffix(".stop").exists():time.sleep(.05)\n')
    parent_file=tmp_path/'parent.ready';child_file=tmp_path/'child.ready'
    parent=subprocess.Popen([sys.executable,'-I','-B',str(script),str(parent_file)],creationflags=subprocess.CREATE_NO_WINDOW)
    child_pid=None
    try:
        before=inventory(pids=[parent.pid]);api=next(p for p in before['processes'] if p['pid']==parent.pid)
        command=subprocess.list2cmdline([sys.executable,'-I','-B',str(script),str(child_file)])
        child_pid=create_executor(command,tmp_path)
        end=time.monotonic()+10
        while not child_file.exists() and time.monotonic()<end:time.sleep(.05)
        assert child_file.read_text()=='ready'
        identity=verify_executor(inventory(pids=[parent.pid,child_pid]),child_pid,parent.pid,api,command)
        # Only the exact parent created above, with creation identity rechecked.
        current=next(p for p in inventory(pids=[parent.pid])['processes'] if p['pid']==parent.pid)
        assert current['birth']==api['birth'] and str(script) in current['command']
        subprocess.run(['taskkill.exe','/PID',str(parent.pid),'/T','/F'],check=True,capture_output=True)
        parent.wait(timeout=10)
        rows=inventory(pids=[child_pid])['processes']
        surviving=next(p for p in rows if p['pid']==child_pid)
        assert surviving['birth']==identity['birth'] and surviving['sid']==identity['sid']
    finally:
        parent_file.with_suffix('.stop').write_text('stop')
        child_file.with_suffix('.stop').write_text('stop')
        if parent.poll() is None:parent.wait(timeout=10)
        if child_pid:
            end=time.monotonic()+10
            while any(p['pid']==child_pid for p in inventory()['processes']) and time.monotonic()<end:time.sleep(.1)
            assert not any(p['pid']==child_pid for p in inventory()['processes'])
