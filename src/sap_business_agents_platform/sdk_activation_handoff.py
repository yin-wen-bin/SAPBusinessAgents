"""One-shot, same-user Windows handoff; no resident updater or elevation.

Win32_Process.Create is used only to escape the API's taskkill /T ancestry.
Before any switch the child independently checks its SID and process ancestry.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from .sdk_update import failure
from .startup_control import atomic, digest


def inventory(run=subprocess.run, pids=None):
    wanted=','.join(str(int(p)) for p in (pids or [os.getpid()]))
    script = ("$wanted=@("+wanted+");$me=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value;"
              "$ps=@(Get-CimInstance Win32_Process | ForEach-Object {"
              "$o=$null;if($wanted -contains $_.ProcessId){$o=Invoke-CimMethod -InputObject $_ -MethodName GetOwnerSid -ErrorAction Stop};"
              "[pscustomobject]@{pid=$_.ProcessId;parent=$_.ParentProcessId;birth=$_.CreationDate.ToUniversalTime().ToString('o');"
              "command=$_.CommandLine;sid=$o.Sid}}); @{sid=$me;processes=$ps}|ConvertTo-Json -Depth 4 -Compress")
    result=run(['powershell.exe','-NoProfile','-Command',script],capture_output=True,text=True,check=True,timeout=30)
    return json.loads(result.stdout)


def create_executor(command,root,run=subprocess.run):
    script=("$v=[Console]::In.ReadToEnd()|ConvertFrom-Json;"
            "$s=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0};"
            "Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
            "@{CommandLine=$v.command;CurrentDirectory=$v.root;ProcessStartupInformation=$s}|ConvertTo-Json -Compress")
    result=run(['powershell.exe','-NoProfile','-Command',script],input=json.dumps({'command':command,'root':str(root)}),
               capture_output=True,text=True,check=True,timeout=30)
    created=json.loads(result.stdout)
    if created['ReturnValue']!=0:raise failure('sdk_activation_handoff_unconfirmed','waiting_maintenance')
    return int(created['ProcessId'])


def verify_executor(rows,executor_pid,api_pid,expected_api,expected_command):
    by_id={int(row['pid']):row for row in rows['processes']}
    child=by_id.get(executor_pid);api=by_id.get(api_pid)
    if (not child or not api or api['birth']!=expected_api['birth'] or api['sid']!=rows['sid']
            or child['sid']!=rows['sid'] or child['command']!=expected_command):
        raise failure('sdk_activation_handoff_unconfirmed','waiting_maintenance')
    seen=set();row=child
    while row and row['parent']:
        parent=by_id.get(int(row['parent']))
        if not parent:break
        if parent['birth']>row['birth']:break  # reused PID is not an ancestor
        if parent['pid']==api_pid or parent['pid'] in seen:
            raise failure('sdk_activation_executor_in_api_tree','waiting_maintenance')
        seen.add(parent['pid']);row=parent
    return {k:child[k] for k in ('pid','birth','sid','command')}


def handoff(root,record,*,run=subprocess.run,clock=time.monotonic,sleep=time.sleep):
    if os.name!='nt':raise failure('sdk_update_platform_unsupported','waiting_maintenance')
    root=Path(root);area=root/'.local-data/sdk-update-jobs/codex'/record['operation_id']
    command=subprocess.list2cmdline([record['base']['environment']['python'],'-I','-B',
        str(Path(record['base']['archive'])/'source/scripts/service-startup.py'),'activate-sdk',
        '--root',str(root),'--operation',record['operation_id'],
        '--port',str(record['base']['api_port']),'--site-port',str(record['base']['site_port']),'--no-browser'])
    rows=inventory(run);api=next((p for p in rows['processes'] if p['pid']==os.getpid()),None)
    if not api or api['sid']!=rows['sid']:raise failure('sdk_activation_handoff_unconfirmed','waiting_maintenance')
    # Use the existing launcher's 90-second bootstrap bound. This handshake
    # precedes service mutation and is not an extension of installation/cleanup.
    ticket={'operation_id':record['operation_id'],'attempt':uuid.uuid4().hex,'expires_at':time.time()+90,'api':api,'command':command,
            'configuration':record['configuration'],'base':record['base'],'target':record['target']}
    ticket['digest']=digest(ticket)
    atomic(area/'handoff.json',ticket)
    # JSON on stdin, not interpolated shell commands or credentials.
    created=create_executor(command,root,run)
    until=clock()+90
    while clock()<until:
        path=area/'executor-ready.json'
        if path.exists():
            ready=json.loads(path.read_bytes())
            if ready.get('ticket_digest')!=ticket['digest']:
                sleep(.1);continue
            identity=verify_executor(inventory(run,pids=[created,os.getpid()]),created,os.getpid(),api,command)
            if clock()>=until or time.time()>=ticket['expires_at'] or ready.get('ticket_digest')!=ticket['digest'] or ready.get('identity')!=identity:
                raise failure('sdk_activation_handoff_unconfirmed','waiting_maintenance')
            atomic(area/'executor-accepted.json',{'ticket_digest':ticket['digest'],'identity':identity})
            return identity
        sleep(.1)
    # No acceptance => the gated executor exits without stopping anything.
    raise failure('sdk_activation_handoff_unconfirmed','waiting_maintenance')
