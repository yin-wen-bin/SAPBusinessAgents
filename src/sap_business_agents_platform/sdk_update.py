"""Codex-only download/verify/local-install plumbing; no SDK or model imports.

pip remains the dependency resolver. Only complete PyPI-hash-verified wheels
are cached; the install transaction is resolved again offline under maintenance.
No registry, login, model selection or historical qualification is changed here.
"""
from __future__ import annotations

from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import quote, urlsplit
from urllib.request import url2pathname
import zipfile

import httpx
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from .startup_control import atomic
from .workbuddy_job import WindowsJob, process_identity


def failure(code, phase, **detail):
    from .sdk_manager import SDKManagerError
    return SDKManagerError('SDK maintenance did not complete.', code=code, detail={'phase':phase, **detail})


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class OwnedPip:
    """A suspended-by-protocol helper owns every pip descendant, even venv shims.

    After 10s active cleanup, keep the caller's maintenance reservation alive
    and expose cleanup_pending until the exact Job is observed empty. Never
    release a mutation guard on an unconfirmed process, nor kill by bare PID.
    """
    def __init__(self, *, clock=time.monotonic, sleep=time.sleep, cleanup_seconds=10):
        self.clock, self.sleep, self.cleanup_seconds = clock, sleep, cleanup_seconds

    def run(self, arguments, *, phase, seconds, progress, idle_seconds=None):
        if os.name != 'nt':
            raise failure('sdk_update_platform_unsupported', phase, cleanup_complete=True)
        helper = Path(__file__).parents[2]/'scripts/sdk-package-helper.py'
        safe_environment={'PATH','SYSTEMROOT','WINDIR','COMSPEC','SYSTEMDRIVE','TEMP','TMP','USERPROFILE',
                          'APPDATA','LOCALAPPDATA','PROGRAMDATA','PROGRAMFILES','PROGRAMFILES(X86)',
                          'HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','SSL_CERT_FILE','REQUESTS_CA_BUNDLE',
                          'LANG','LC_ALL','HOME'}
        environment={k:v for k,v in os.environ.items() if k.upper() in safe_environment}
        environment.update(PIP_CONFIG_FILE=os.devnull, PIP_NO_INPUT='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
        job, process, reader = WindowsJob(), None, None
        started = self.clock()
        observed = {'at':started,'bytes':-1,'filename':None}
        lock = threading.Lock()
        error = None
        def output():
            while line := process.stdout.readline(8193):
                if len(line)>8192: continue
                text = line.decode('utf-8',errors='replace').strip()
                event = {}
                match = re.search(r'([A-Za-z0-9][A-Za-z0-9_.+!-]*\.whl)(?:\s|$|\))', text)
                if match:
                    try: parse_wheel_filename(match[1])
                    except ValueError: pass
                    else:
                        if observed['filename'] != match[1]:
                            observed.update(filename=match[1],bytes=-1)
                            event.update(filename=match[1],downloaded_bytes=0,total_bytes=None)
                raw = re.fullmatch(r'Progress (\d+) of (\d+)',text)
                if raw:
                    current,total=map(int,raw.groups())
                    if 0<=current<=20_000_000_000 and (total==0 or current<=total<=20_000_000_000) and current>observed['bytes']:
                        observed['bytes']=current
                        event.update(downloaded_bytes=current,total_bytes=total or None)
                if event:
                    with lock: observed['at']=self.clock()
                    progress(phase=phase,**event)
                # Never persist raw output, URLs, credentials or installer text.
        try:
            process = subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-I','-B',str(helper)],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=environment,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            owned_identity=process_identity(process.pid)
            if owned_identity in {None,'unknown'}: raise OSError('owned_identity_unconfirmed')
            job.attach(process.pid)
            progress(phase=phase,process_id=process.pid,process_identity=owned_identity,cleanup_complete=False)
            reader=threading.Thread(target=output,daemon=True);reader.start()
            process.stdin.write(json.dumps([sys.executable,'-m','pip',*arguments]).encode()+b'\n')
            process.stdin.close()
            while process.poll() is None:
                now=self.clock()
                with lock: idle=now-observed['at']
                if now-started>=seconds:
                    error=failure('sdk_download_timeout' if phase=='downloading' else 'sdk_update_timeout',phase)
                    break
                if idle_seconds is not None and idle>=idle_seconds:
                    error=failure('sdk_download_stalled',phase)
                    break
                self.sleep(.1)
            if error is None and process.returncode != 0:
                error=failure('sdk_package_manager_failed',phase,exit_code=process.returncode)
        except (OSError,ValueError) as exc:
            error=failure('sdk_package_manager_failed',phase,reason=type(exc).__name__)
        finally:
            deadline=self.clock()+self.cleanup_seconds
            # The gated helper cannot spawn pip if Job attachment failed.
            try:
                if not job.empty(): job.terminate()
                if process is not None and process.poll() is None: process.kill()
            except OSError:
                pass
            pending=False
            while not job.empty() or process is not None and process.poll() is None:
                if self.clock()>=deadline and not pending:
                    pending=True
                    progress(phase='cleanup',status='cleanup_pending',cleanup_complete=False)
                self.sleep(.1)  # Observation only after the active cleanup window.
            job.close()
            if reader: reader.join(timeout=1)
            if process and process.stdout: process.stdout.close()
            progress(phase=phase,status='running',cleanup_complete=True,process_id=None,process_identity=None)
        if error:
            error.detail['cleanup_complete']=True
            raise error


class CodexPackageUpdate:
    def __init__(self, root, *, run=None, client_factory=None, download_seconds=1800,
                 idle_seconds=60, install_seconds=300, clock=time.monotonic):
        self.root=Path(root).resolve()
        self.run=run or OwnedPip().run
        self.client_factory=client_factory or (lambda: httpx.Client(timeout=30,trust_env=False))
        self.download_seconds,self.idle_seconds,self.install_seconds=download_seconds,idle_seconds,install_seconds
        self.clock=clock

    def _cache(self,target):
        # Platform/interpreter binding, not a new qualification or release ID.
        key=hashlib.sha256(json.dumps([target,sys.platform,sys.implementation.cache_tag],sort_keys=True).encode()).hexdigest()
        return self.root/key

    def _verify_files(self,directory,bundle):
        try:
            if bundle.get('package')!='openai-codex' or not isinstance(bundle.get('files'),dict) or not 0<len(bundle['files'])<=128:
                raise ValueError('invalid_inventory')
            if {p.name for p in directory.iterdir()}!={*bundle['files'],'verified.json'}:
                raise ValueError('changed_inventory')
            sdk=[]
            for name,info in bundle['files'].items():
                path=directory/name
                package,version,_,_=parse_wheel_filename(name)
                if (package!=info['name'] or str(version)!=info['version'] or not re.fullmatch(r'[a-f0-9]{64}',info['sha256'])
                        or path.is_symlink() or path.resolve().parent!=directory.resolve() or not path.is_file()
                        or path.stat().st_size!=info['size'] or sha(path)!=info['sha256']):
                    raise ValueError('changed_artifact')
                if package=='openai-codex':sdk.append(path)
            if len(sdk)!=1 or parse_wheel_filename(sdk[0].name)[1]!=Version(bundle['target']):
                raise ValueError('target_mismatch')
            # A cache receipt never replaces the SDK's own pinned CLI contract.
            if self._cli_pin(sdk[0])!=bundle['cli_version'] or not any(
                    f['name']=='openai-codex-cli-bin' and Version(f['version'])==Version(bundle['cli_version'])
                    for f in bundle['files'].values()):
                raise ValueError('cli_mismatch')
        except (OSError,KeyError,TypeError,ValueError,zipfile.BadZipFile):
            raise failure('sdk_download_integrity_failed','verification')

    @staticmethod
    def _cli_pin(sdk):
        with zipfile.ZipFile(sdk) as archive:
            sources=[n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
            if len(sources)!=1:raise ValueError('metadata_ambiguous')
            package_metadata=BytesParser().parsebytes(archive.read(sources[0]))
        requirements=[Requirement(r) for r in package_metadata.get_all('Requires-Dist',[])]
        cli=[r for r in requirements if canonicalize_name(r.name)=='openai-codex-cli-bin' and (r.marker is None or r.marker.evaluate())]
        pins=list(cli[0].specifier) if len(cli)==1 else []
        if len(pins)!=1 or pins[0].operator!='==' or '*' in pins[0].version:
            raise ValueError('cli_not_pinned')
        return pins[0].version

    def prepare(self,target,progress):
        target=str(Version(target));self.root.mkdir(parents=True,exist_ok=True)
        destination=self._cache(target)
        if (destination/'verified.json').is_file():
            try:
                bundle=json.loads((destination/'verified.json').read_bytes())
                assert bundle['target']==target
                self._verify_files(destination,bundle)
            except (OSError,ValueError,KeyError,AssertionError):
                destination.rename(self.root/(destination.name+'.invalid-'+os.urandom(6).hex()))
            else:
                progress(phase='verification',cache_reused=True,cleanup_complete=True,expected_cli=bundle['cli_version'])
                return {**bundle,'directory':str(destination)}
        stage=Path(tempfile.mkdtemp(prefix='download-',dir=self.root))
        started=self.clock()
        self.run(['download','--disable-pip-version-check','--no-cache-dir','--index-url','https://pypi.org/simple',
                  '--only-binary=:all:','--progress-bar','raw','--timeout','15','--retries','0','--resume-retries','0',
                  '--dest',str(stage),f'openai-codex=={target}'],phase='downloading',seconds=self.download_seconds,
                 idle_seconds=self.idle_seconds,progress=progress)
        progress(phase='verification',filename=None,downloaded_bytes=None,total_bytes=None)
        wheels=list(stage.iterdir())
        if not wheels or len(wheels)>128 or any(p.suffix!='.whl' or p.is_symlink() for p in wheels):
            raise failure('sdk_download_integrity_failed','verification')
        files={}
        try:
            with self.client_factory() as client:
                for path in wheels:
                    if self.clock()-started>=self.download_seconds:
                        raise failure('sdk_download_timeout','verification')
                    name,version,_,_=parse_wheel_filename(path.name)
                    response=client.get(f'https://pypi.org/pypi/{quote(name,safe="")}/{version}/json')
                    response.raise_for_status()
                    match=next((x for x in response.json()['urls'] if x['filename']==path.name),None)
                    if not match or match.get('yanked') or match['size']!=path.stat().st_size or match['digests']['sha256']!=sha(path):
                        raise failure('sdk_download_integrity_failed','verification')
                    files[path.name]={'name':name,'version':str(version),'size':match['size'],'sha256':match['digests']['sha256']}
        except (httpx.HTTPError,KeyError,ValueError) as exc:
            raise failure('sdk_download_verification_failed','verification',reason=type(exc).__name__) from None
        sdk=next((p for p in wheels if files[p.name]['name']=='openai-codex' and files[p.name]['version']==target),None)
        if sdk is None: raise failure('sdk_download_integrity_failed','verification')
        try:cli_version=self._cli_pin(sdk)
        except (OSError,ValueError,zipfile.BadZipFile):
            raise failure('sdk_runtime_dependency_unpinned','verification') from None
        if not any(f['name']=='openai-codex-cli-bin' and Version(f['version'])==Version(cli_version) for f in files.values()):
            raise failure('sdk_download_integrity_failed','verification')
        bundle={'package':'openai-codex','target':target,'cli_version':cli_version,'files':files}
        progress(phase='verification',expected_cli=cli_version)
        atomic(stage/'verified.json',bundle)
        self._verify_files(stage,bundle)
        if destination.exists():
            destination.rename(self.root/(destination.name+'.incomplete-'+os.urandom(6).hex()))
        stage.rename(destination)
        return {**bundle,'directory':str(destination)}

    def install(self,bundle,progress):
        directory=Path(bundle['directory']).resolve()
        if directory!=self._cache(bundle['target']):
            raise failure('sdk_download_integrity_failed','verification')
        self._verify_files(directory,bundle)
        transaction=Path(tempfile.mkdtemp(prefix='install-',dir=self.root))
        started=self.clock()
        def remaining():
            value=self.install_seconds-(self.clock()-started)
            if value<=0:raise failure('sdk_update_timeout','installation')
            return value
        progress(phase='local_resolution',filename=None,downloaded_bytes=None,total_bytes=None)
        report=transaction/'plan.json'
        # Same pip resolver/update policy as before, but offline, against the
        # current environment *inside* the original exclusive maintenance gate.
        self.run(['install','--disable-pip-version-check','--no-cache-dir','--no-index','--find-links',str(directory),
                  '--only-binary=:all:','--upgrade','--dry-run','--report',str(report),f'openai-codex=={bundle["target"]}'],
                 phase='local_resolution',seconds=remaining(),progress=progress)
        try:
            plan=json.loads(report.read_bytes())['install']
            if not plan:raise ValueError('empty_plan')
            lines=[]
            for item in plan:
                parsed=urlsplit(item['download_info']['url'])
                path=Path(url2pathname(parsed.path)).resolve()
                record=bundle['files'].get(path.name)
                if parsed.scheme!='file' or parsed.netloc not in {'','localhost'} or path.parent!=directory or record is None:
                    raise ValueError('nonlocal_plan')
                archive_hash=item['download_info']['archive_info']['hashes']['sha256']
                if (archive_hash!=record['sha256'] or canonicalize_name(item['metadata']['name'])!=record['name']
                        or Version(item['metadata']['version'])!=Version(record['version'])):
                    raise ValueError('plan_binding_mismatch')
                lines.append(f'{record["name"]}=={record["version"]} --hash=sha256:{record["sha256"]}')
            if not any(canonicalize_name(i['metadata']['name'])=='openai-codex' and Version(i['metadata']['version'])==Version(bundle['target']) for i in plan):
                raise ValueError('target_missing')
        except (OSError,KeyError,ValueError,TypeError):
            raise failure('sdk_update_plan_invalid','local_resolution') from None
        requirements=transaction/'requirements.txt'
        requirements.write_text('\n'.join(lines)+'\n',encoding='utf-8')
        progress(phase='installation')
        # Installing only pip's validated transaction avoids unnecessarily
        # upgrading already-satisfied dependencies. Hashes also close the gap
        # between cache verification and the installer's actual file reads.
        self.run(['install','--disable-pip-version-check','--no-cache-dir','--no-index','--find-links',str(directory),
                  '--only-binary=:all:','--require-hashes','--no-deps','--upgrade','-r',str(requirements)],
                 phase='installation',seconds=remaining(),progress=progress)
        return {'expected_sdk':bundle['target'],'expected_cli':bundle['cli_version']}
