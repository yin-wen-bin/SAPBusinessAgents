"""Only decode the owned helper protocol; never execute the command."""
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT=Path(__file__).resolve().parents[1]


def helper(monkeypatch,command):
    spec=importlib.util.spec_from_file_location('sdk_package_gate_test',ROOT/'scripts/sdk-package-helper.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    calls=[]
    monkeypatch.setattr(module.sys,'stdin',SimpleNamespace(buffer=io.BytesIO(json.dumps(command).encode()+b'\n')))
    def fake(*args,**kwargs):calls.append((args,kwargs));return SimpleNamespace(returncode=0)
    monkeypatch.setattr(module.subprocess,'run',fake)
    return module,calls


def test_original_codex_pip_protocol_is_unchanged(monkeypatch,tmp_path):
    command=[str(tmp_path/'python.exe'),'-m','pip','check'];module,calls=helper(monkeypatch,command)
    assert module.main()==0 and calls[0][0][0]==command


def test_candidate_only_allows_the_fixed_sibling_builder(monkeypatch,tmp_path):
    command=[str(tmp_path/'python.exe'),'-I','-B',str(ROOT/'scripts/prepare-workbuddy-package.py'),
        '--request',str(tmp_path/'request.json'),'--request-sha256','a'*64]
    module,calls=helper(monkeypatch,command)
    assert module.main()==0 and calls[0][0][0]==command


@pytest.mark.parametrize('change',['relative_python','other_script','bad_flags','relative_request','missing_hash','short','non_string'])
def test_unowned_or_arbitrary_command_shapes_are_rejected(monkeypatch,tmp_path,change):
    command=[str(tmp_path/'python.exe'),'-I','-B',str(ROOT/'scripts/prepare-workbuddy-package.py'),
        '--request',str(tmp_path/'request.json'),'--request-sha256','a'*64]
    if change=='relative_python':command[0]='python.exe'
    if change=='other_script':command[3]=str(tmp_path/'prepare-workbuddy-package.py')
    if change=='bad_flags':command[1]='-c'
    if change=='relative_request':command[5]='request.json'
    if change=='missing_hash':command[7]='short'
    if change=='short':command=command[:3]
    if change=='non_string':command[7]=9
    module,calls=helper(monkeypatch,command)
    assert module.main()==2 and calls==[]
