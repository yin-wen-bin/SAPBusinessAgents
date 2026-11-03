"""Keep the actual MCP stream open while non-interactive Git tools run."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which('git') is None, reason='Git is required')
def test_inventory_plan_snapshot_and_status_return_before_mcp_stdin_eof(tmp_path):
    repository = tmp_path/'repository'
    (repository/'src').mkdir(parents=True)
    (repository/'tests').mkdir()
    (repository/'src/a.py').write_text('VALUE = 1\n', encoding='utf-8')
    (repository/'.gitignore').write_text('.local-data/\n', encoding='utf-8')
    for name in ('start-sap-business-agents.cmd','restart-sap-business-agents.cmd'):
        (repository/name).write_text('@echo off\n', encoding='utf-8')
    (repository/'tests/test_free_query_stream.py').write_text('def test_value(): assert True\n', encoding='utf-8')
    subprocess.run(['git','init','--template=',str(repository)], check=True, capture_output=True, stdin=subprocess.DEVNULL)
    # Match the real checkout's tracked launchers, even when a developer's
    # global Git excludes ignore untracked .cmd files in temporary repositories.
    subprocess.run(['git','-C',str(repository),'add','--force','start-sap-business-agents.cmd',
        'restart-sap-business-agents.cmd'],check=True,capture_output=True,stdin=subprocess.DEVNULL)
    # Model a Git helper that reads its input before executing real Git. A child
    # inheriting the live MCP stream would wait indefinitely for the host's EOF.
    # This is a real process/pipe test, independent of the host's Git settings.
    program = r'''
import runpy,subprocess,sys,shutil
original = subprocess.run
git = shutil.which('git')
helper = ("import sys,subprocess; sys.stdin.buffer.read(); "
          "sys.exit(subprocess.call(sys.argv[1:],stdin=subprocess.DEVNULL,stdout=sys.stdout,stderr=sys.stderr))")
def run(command,*args,**kwargs):
    if command[0] == 'git':
        command = [sys.executable,'-B','-c',helper,git,*command[1:]]
    return original(command,*args,**kwargs)
subprocess.run = run
sys.argv = ['mcp','--repository',sys.argv[1]]
runpy.run_module('sap_business_agents_test_agent.mcp',run_name='__main__')
'''
    environment = {**os.environ,'PYTHONPATH':str(ROOT/'src'),'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'}
    messages = queue.Queue()
    with (tmp_path/'stderr.log').open('wb') as errors:
        process = subprocess.Popen([sys.executable,'-B','-c',program,str(repository)], cwd=tmp_path,
            env=environment,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        def receive():
            for line in process.stdout:
                messages.put(json.loads(line))
        reader = threading.Thread(target=receive,daemon=True)
        reader.start()
        def request(identifier,method,params=None):
            value={'jsonrpc':'2.0','id':identifier,'method':method}
            if params is not None:value['params']=params
            process.stdin.write((json.dumps(value)+'\n').encode())
            process.stdin.flush()
            result=messages.get(timeout=20)
            assert result['id']==identifier
            assert 'error' not in result and not result['result'].get('isError'),result
            assert not process.stdin.closed and process.poll() is None
            return result['result']
        def tool(identifier,name,arguments):
            return request(identifier,'tools/call',{'name':name,'arguments':arguments})['structuredContent']
        try:
            request(1,'initialize',{'protocolVersion':'2025-06-18'})
            inventory=tool(2,'test_inventory',{})
            assert len(inventory['features'])==8
            case={'id':'stream-case','feature':'free-query','operation':'query','scenario':'Isolated protocol check',
                'kind':'native','mode':'simulated','assertions':['business_result_matches'],
                'impact_paths':['src/a.py'],'depends_on':[]}
            plan=tool(3,'test_prepare',{'selected':['free-query'],'cases':[case],'inventory_digest':inventory['digest']})
            task=tool(4,'test_start',{'plan_id':plan['id'],'plan_digest':plan['digest'],'request_id':'stream-request'})
            for name in ('start-sap-business-agents.cmd','restart-sap-business-agents.cmd'):
                assert (Path(task['workspace']['source'])/name).read_bytes()==(repository/name).read_bytes()
            assert (Path(task['workspace']['source'])/'src/a.py').read_bytes()==(repository/'src/a.py').read_bytes()
            status=tool(5,'test_status',{'task_id':task['id']})
            assert status['task_id']==task['id']
            tool(6,'test_cancel',{'task_id':task['id']})
        finally:
            process.stdin.close()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name=='nt':
                    subprocess.run(['taskkill.exe','/PID',str(process.pid),'/T','/F'],capture_output=True,stdin=subprocess.DEVNULL)
                else:process.kill()
                process.wait(timeout=5)
            reader.join(timeout=2)
