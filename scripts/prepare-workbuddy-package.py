"""Owned, offline pip candidate builder. Never reads or writes active state."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def stable(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def prepare(path,expected,*,run=subprocess.run,clock=time.monotonic):
    if sha(path)!=expected:raise ValueError('workbuddy_candidate_request_changed')
    request=json.loads(path.read_bytes());root=Path(request['repository']).resolve()
    python=Path(request['python']).resolve(strict=True)
    if python.is_relative_to(root/'.venv') or Path(sys.executable).resolve()!=python or sha(python)!=request['python_sha256']:
        raise ValueError('workbuddy_bootstrap_changed')
    bundle=request['bundle'];directory=Path(bundle['directory']).resolve(strict=True)
    if not directory.is_relative_to(root/'.local-data/sdk-update-cache/workbuddy'):
        raise ValueError('workbuddy_package_cache_invalid')
    if bundle['package']!='codebuddy-agent-sdk' or bundle['cli_binding']['kind']!='bundled':
        raise ValueError('workbuddy_package_invalid')
    files=bundle['files'];lines=[]
    if not files or len(files)>128:raise ValueError('workbuddy_package_invalid')
    for name,info in files.items():
        f=directory/name
        if (Path(name).name!=name or f.is_symlink() or f.resolve().parent!=directory
                or f.stat().st_size!=info['size'] or sha(f)!=info['sha256']
                or not re.fullmatch(r'[a-zA-Z0-9_.-]+',info['name'])
                or not re.fullmatch(r'[a-zA-Z0-9_.+!-]+',info['version'])
                or not re.fullmatch(r'[a-f0-9]{64}',info['sha256'])):
            raise ValueError('workbuddy_package_changed')
        lines.append(f"{info['name']}=={info['version']} --hash=sha256:{info['sha256']}")
    runtime=root/'.local-data/runtimes/workbuddy';releases=runtime/'releases';releases.mkdir(parents=True,exist_ok=True)
    building=Path(tempfile.mkdtemp(prefix='preparing-pip-',dir=runtime));started=clock()
    def call(command,limit,*,capture=False):
        remaining=700-(clock()-started)
        if remaining<=0:raise TimeoutError('workbuddy_candidate_timeout')
        return run(command,check=True,timeout=min(limit,remaining),stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
            text=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PIP_CONFIG_FILE':os.devnull,'PIP_NO_INPUT':'1'})
    call([str(python),'-I','-B','-m','venv',str(building)],60)
    executable=building/'Scripts/python.exe';lock=building/'workbuddy-requirements.lock'
    lock.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    call([str(executable),'-I','-B','-m','pip','install','--no-index','--find-links',str(directory),
          '--only-binary=:all:','--require-hashes','--no-deps','--no-compile','--disable-pip-version-check','-r',str(lock)],300)
    worker=Path(request['worker']).resolve(strict=True)
    source_key=request['source_binding'].get('environment_digest')
    if source_key is not None and not re.fullmatch(r'[a-f0-9]{64}',source_key):raise ValueError('workbuddy_source_binding_invalid')
    allowed_worker=root/'.local-data/runtimes/workbuddy/releases'/source_key if source_key else root/'src/sap_business_agents_platform'
    if not worker.is_relative_to(allowed_worker):raise ValueError('workbuddy_worker_source_invalid')
    if sha(worker)!=request['worker_sha256']:raise ValueError('workbuddy_worker_changed')
    shutil.copyfile(worker,building/'worker.py')
    cli=building/'Lib/site-packages'/bundle['cli_binding']['member']
    if not cli.resolve().is_relative_to(building) or sha(cli)!=bundle['cli_binding']['sha256']:
        raise ValueError('workbuddy_cli_changed')
    call([str(executable),'-I','-B','-m','pip','check'],30)
    tree=ast.parse(worker.read_text(encoding='utf-8'));modules={};fields=set()
    for node in ast.walk(tree):
        if isinstance(node,ast.ImportFrom) and node.module and node.module.startswith('codebuddy_agent_sdk'):
            modules.setdefault(node.module,set()).update(n.name for n in node.names)
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='CodeBuddyAgentOptions':
            fields.update(k.arg for k in node.keywords if k.arg)
    surface=("import importlib,inspect,json;import codebuddy_agent_sdk as sdk;"
        +f"modules={repr({k:sorted(v) for k,v in modules.items()})};fields={repr(sorted(fields))};"
        +"assert all(hasattr(importlib.import_module(module),name) for module,names in modules.items() for name in names),'workbuddy_worker_contract_unsupported';"
        +"params=inspect.signature(sdk.CodeBuddyAgentOptions).parameters;"
        +"assert any(p.kind==inspect.Parameter.VAR_KEYWORD for p in params.values()) or set(fields)<=set(params),'workbuddy_worker_contract_unsupported';"
        +"print(json.dumps({'worker_surface':True}))")
    surface_result=json.loads(call([str(executable),'-I','-B','-c',surface],15,capture=True).stdout)
    if surface_result!={'worker_surface':True}:raise ValueError('workbuddy_worker_contract_unsupported')
    inspect="import importlib.metadata as m,json;print(json.dumps({'sdk_version':m.version('codebuddy-agent-sdk'),'dependencies':{d.metadata['Name']:d.version for d in m.distributions()}}))"
    info=json.loads(call([str(executable),'-I','-B','-c',inspect],15,capture=True).stdout)
    if info['sdk_version']!=bundle['target']:raise ValueError('workbuddy_sdk_version_mismatch')
    for record in files.values():
        versions={k.lower().replace('_','-'):v for k,v in info['dependencies'].items()}
        if versions.get(record['name'])!=record['version']:raise ValueError('workbuddy_dependency_version_mismatch')
    raw=call([str(cli),'--version'],15,capture=True).stdout.strip()
    match=re.fullmatch(r'(?:CodeBuddy(?: Code)?\s+)?v?(\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?)',raw)
    if not match:raise ValueError('workbuddy_cli_version_unconfirmed')
    manifest={'schema_version':'1.0',**info,'cli_version':match[1],'python':'Scripts/python.exe',
              'cli':cli.relative_to(building).as_posix(),'worker':'worker.py','service_site_contract':'1',
              'dependency_lock_digest':sha(lock),'sdk_wheel_digest':next(v['sha256'] for v in files.values() if v['name']=='codebuddy-agent-sdk'),
              'python_bootstrap_digest':request['python_sha256'],'worker_surface_checked':True,
              'files':{p.relative_to(building).as_posix():sha(p) for p in building.rglob('*') if p.is_file()}}
    key=stable(manifest);manifest['environment_digest']=key;destination=releases/key
    if destination.exists():raise ValueError('workbuddy_release_already_exists')
    (building/'environment.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8');building.rename(destination)
    # Verify actual final paths after moving, without import side effects.
    final=json.loads(call([str(destination/manifest['python']),'-I','-B','-c',inspect],15,capture=True).stdout)
    if final!=info:raise ValueError('workbuddy_relocated_environment_invalid')
    call([str(destination/manifest['python']),'-I','-B','-m','pip','check'],30)
    if any(sha(destination/name)!=value for name,value in manifest['files'].items()):raise ValueError('workbuddy_candidate_changed')
    result={'environment_digest':key,'sdk_version':info['sdk_version'],'cli_version':manifest['cli_version'],
            'source_binding':request['source_binding'],'operation_id':request['operation_id'],'activated':False,'cleanup_complete':True}
    (path.parent/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--request-sha256',required=True)
    args=p.parse_args();prepare(args.request,args.request_sha256)
