"""Synthetic gated process fixture. Never executes a package manager or SDK."""
import json
from pathlib import Path
import subprocess
import sys
import time

if '--child' in sys.argv:
    time.sleep(30)
else:
    request=json.loads(sys.stdin.buffer.readline())
    mode=request[3]
    print('Downloading synthetic_package-1.0.0-py3-none-any.whl',flush=True)
    print('private token=synthetic-secret https://private.invalid/?key=synthetic-secret',flush=True)
    print('Progress 50 of 0' if mode=='fixture-stream' else 'Progress 50 of 100',flush=True)
    if mode=='fixture-hang':
        child=subprocess.Popen([sys.executable,'-I','-B',__file__,'--child'])
        Path(request[4]).write_text(json.dumps({'child':child.pid}))
        time.sleep(30)
