"""Owned subprocess: actual platform core checks, no SDK/model/SAP calls."""
from pathlib import Path
import argparse
import os
import subprocess
import sys

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    checks=[
        'tests/test_workbuddy_isolation.py::test_worker_boundaries_and_cleanup',
        'tests/test_workbuddy_isolation.py::test_tool_relay_and_no_connection_credentials',
        'tests/test_workbuddy_isolation.py::test_timeout_and_owned_child_tree',
        'tests/test_workbuddy_three_layers.py::test_versioned_probe_idempotent_and_strict',
        'tests/test_workbuddy_three_layers.py::test_hard_gates_remain']
    environment={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
    # -I keeps the installer/CLI environment out; pytest supplies only frozen source roots.
    command=[sys.executable,'-I','-B','-m','pytest','-q','-p','no:cacheprovider',
        '-c',str(root/'pyproject.toml'),'-o',f'pythonpath={root} {root / "src"}',
        '--basetemp',str(out/'temporary'),'--junitxml',str(out/'results.xml')]
    result=subprocess.run(command+[str(root/check) for check in checks],cwd=root,env=environment,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=280,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    raise SystemExit(result.returncode)
