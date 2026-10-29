"""Codex installation into a new API environment, never into the running one."""
from __future__ import annotations

from email.parser import BytesParser
import json
import os
from pathlib import Path
import shutil
import time
import tomllib
from urllib.parse import quote, urlsplit
from urllib.request import url2pathname
import zipfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from . import runtime_environment as environments
from .sdk_update import CodexPackageUpdate, OwnedPip, failure, sha
from .startup_control import atomic


class CodexEnvironmentUpdate:
    """Use pip's minimal change plan, then reproduce the full validated lock."""
    def __init__(self, root, base, operation_id, *, pipeline=None, owned_factory=OwnedPip,
                 capture=environments.capture, clock=time.monotonic):
        self.root, self.base, self.operation_id = Path(root).resolve(), base, operation_id
        if not __import__('re').fullmatch(r'update_[a-f0-9]{32}', operation_id):
            raise failure('sdk_update_plan_invalid', 'preparing')
        self.area = self.root/'.local-data/sdk-update-jobs/codex'/operation_id
        self.environment = self.root/'.local-data/api-environments'/operation_id
        self.owned_factory, self.capture, self.clock = owned_factory, capture, clock
        self.pipeline = pipeline or CodexPackageUpdate(self.root/'.local-data/sdk-update-cache/codex',
            run=owned_factory(python=base['environment']['python']).run)

    def _constraints(self, versions):
        # Frozen platform declarations, never the possibly edited checkout.
        archive = Path(self.base['archive'])
        project = tomllib.loads((archive/'source/pyproject.toml').read_text(encoding='utf-8'))
        support = project.get('tool', {}).get('sapba', {}).get('codex', {}).get('reviewed_versions', [])
        if versions.get('openai-codex') not in support:
            raise failure('sdk_target_evidence_required', 'awaiting_review')
        for text in project['project']['dependencies']:
            requirement = Requirement(text)
            if requirement.marker and not requirement.marker.evaluate():
                continue
            actual = versions.get(canonicalize_name(requirement.name))
            if actual is None or Version(actual) not in requirement.specifier:
                raise failure('sdk_platform_dependency_conflict', 'local_resolution', package=canonicalize_name(requirement.name))

    def _remaining(self, started, budget, phase):
        remaining = budget - (self.clock()-started)
        if remaining <= 0:
            raise failure('sdk_download_timeout' if phase == 'downloading' else 'sdk_update_timeout', phase)
        return remaining

    def prepare(self, target, progress):
        environments.verify(self.base['environment'])
        self._constraints({**self.base['environment']['packages'], 'openai-codex':target})
        self.area.mkdir(parents=True, exist_ok=True)
        started = self.clock()
        bundle = self.pipeline.prepare(target, progress)
        report = self.area/'resolution.json'
        # Dry run against the old environment retains already satisfied deps.
        self.pipeline.run(['install','--disable-pip-version-check','--no-cache-dir','--no-index',
            '--find-links',bundle['directory'],'--only-binary=:all:','--upgrade','--dry-run',
            '--report',str(report),f'openai-codex=={target}'],phase='local_resolution',
            seconds=self._remaining(started, self.pipeline.download_seconds, 'downloading'),progress=progress)
        versions = dict(self.base['environment']['packages'])
        # The platform is loaded from frozen source, never reinstalled from a
        # workstation editable package. Developer tools need not become runtime.
        versions.pop('sap-business-agents-platform', None)
        try:
            planned = json.loads(report.read_bytes())['install']
            for item in planned:
                url = urlsplit(item['download_info']['url'])
                path = Path(url2pathname(url.path)).resolve()
                package = canonicalize_name(item['metadata']['name'])
                value = bundle['files'].get(path.name)
                if (url.scheme != 'file' or url.netloc not in {'','localhost'}
                        or path.parent != Path(bundle['directory']).resolve() or not value
                        or value['name'] != package or value['version'] != item['metadata']['version']
                        or value['sha256'] != item['download_info']['archive_info']['hashes']['sha256']):
                    raise ValueError('resolution_binding')
                versions[package] = value['version']
            if versions.get('openai-codex') != target or versions.get('openai-codex-cli-bin') != bundle['cli_version']:
                raise ValueError('target')
        except (OSError, ValueError, KeyError, TypeError):
            raise failure('sdk_update_plan_invalid', 'local_resolution') from None
        self._constraints(versions)
        self._validate_closure(bundle, versions)
        directory = self.area/'wheels'; directory.mkdir(exist_ok=False)
        requirements = self.area/'resolved.txt'
        requirements.write_text(''.join(f'{name}=={version}\n' for name,version in sorted(versions.items())),encoding='utf-8')
        self.pipeline.run(['download','--disable-pip-version-check','--no-cache-dir','--index-url','https://pypi.org/simple',
            '--only-binary=:all:','--no-deps','--progress-bar','raw','--timeout','15','--retries','0',
            '--resume-retries','0','--dest',str(directory),'-r',str(requirements)],phase='downloading',
            seconds=self._remaining(started,self.pipeline.download_seconds,'downloading'),
            idle_seconds=self.pipeline.idle_seconds,progress=progress)
        files = {}
        with self.pipeline.client_factory() as client:
            for path in directory.iterdir():
                self._remaining(started,self.pipeline.download_seconds,'downloading')
                name,version,_,_ = parse_wheel_filename(path.name)
                if path.is_symlink() or name in files or versions.get(name) != str(version):
                    raise failure('sdk_download_integrity_failed','verification')
                response=client.get(f'https://pypi.org/pypi/{quote(name,safe="")}/{version}/json');response.raise_for_status()
                remote=next((v for v in response.json()['urls'] if v['filename']==path.name),None)
                if not remote or remote.get('yanked') or remote['size']!=path.stat().st_size or remote['digests']['sha256']!=sha(path):
                    raise failure('sdk_download_integrity_failed','verification')
                files[name]={'version':str(version),'filename':path.name,'sha256':sha(path),'size':path.stat().st_size}
        if set(files) != set(versions):
            raise failure('sdk_download_integrity_failed','verification')
        lock={'version':1,'packages':files,'base_environment_digest':self.base['environment']['digest'],
              'target_version':target,'cli_version':bundle['cli_version']}
        lock['digest']=environments.digest(lock)
        atomic(self.area/'dependency-lock.json',lock)
        environments.verify(self.base['environment'])
        return lock

    def _validate_closure(self, bundle, versions):
        requirements = {}
        for filename, record in bundle['files'].items():
            with zipfile.ZipFile(Path(bundle['directory'])/filename) as wheel:
                paths=[p for p in wheel.namelist() if p.endswith('.dist-info/METADATA')]
                if len(paths)!=1:raise failure('sdk_update_plan_invalid','local_resolution')
                metadata=BytesParser().parsebytes(wheel.read(paths[0]))
            requirements[record['name']] = [Requirement(r) for r in metadata.get_all('Requires-Dist',[])]
        reachable, pending = set(), ['openai-codex']
        while pending:
            name=pending.pop()
            if name in reachable:continue
            reachable.add(name)
            for requirement in requirements.get(name,[]):
                if requirement.marker is None or requirement.marker.evaluate():
                    pending.append(canonicalize_name(requirement.name))
        changed={name for name,version in versions.items() if self.base['environment']['packages'].get(name)!=version}
        if not changed <= reachable:
            raise failure('sdk_unrelated_dependency_change','local_resolution')

    def _carry_artifact_proofs(self, binding, lock, wheels):
        proofs=[]
        for source in self.base['environment'].get('artifact_proofs',[]):
            name=source['distribution'];record=lock['packages'].get(name)
            if not record or record['version']!=source['version']:
                continue  # Old provenance cannot authorize a changed dependency.
            if record['sha256']!=source['sha256']:
                raise failure('sdk_download_integrity_failed','version_confirmation')
            proofs.append(environments.prove_official_artifact(binding,wheels/record['filename']))
        return proofs

    def install(self, lock, progress):
        environments.verify(self.base['environment'])
        if (lock != json.loads((self.area/'dependency-lock.json').read_bytes())
                or environments.digest({k:v for k,v in lock.items() if k!='digest'})!=lock['digest']):
            raise failure('sdk_update_plan_invalid','installation')
        wheels=self.area/'wheels'
        expected={v['filename'] for v in lock['packages'].values()}
        if {p.name for p in wheels.iterdir()}!=expected:
            raise failure('sdk_download_integrity_failed','installation')
        expanded=0
        for record in lock['packages'].values():
            path=wheels/record['filename']
            if path.is_symlink() or sha(path)!=record['sha256']:
                raise failure('sdk_download_integrity_failed','installation')
            with zipfile.ZipFile(path) as archive:expanded+=sum(i.file_size for i in archive.infolist())
        if shutil.disk_usage(self.root).free < expanded*2 + sum(v['size'] for v in lock['packages'].values()):
            raise failure('sdk_update_disk_space_insufficient','preparing')
        if self.environment.exists():
            raise failure('sdk_candidate_already_exists','installation')
        self.environment.parent.mkdir(parents=True,exist_ok=True)
        started=self.clock(); budget=self.pipeline.install_seconds
        base=self.owned_factory(python=self.base['environment']['python'])
        base.run([],phase='environment_creation',seconds=self._remaining(started,budget,'installation'),progress=progress,
            command=[self.base['environment']['python'],'-I','-B','-m','venv',str(self.environment)])
        python=environments.python_path(self.environment)
        runner=self.owned_factory(python=python)
        requirements=self.area/'requirements.lock'
        requirements.write_text(''.join(f'{name}=={v["version"]} --hash=sha256:{v["sha256"]}\n'
            for name,v in sorted(lock['packages'].items())),encoding='utf-8')
        runner.run(['install','--disable-pip-version-check','--no-cache-dir','--no-index',
            '--find-links',str(wheels),'--only-binary=:all:','--require-hashes','--no-deps','-r',str(requirements)],
            phase='installation',seconds=self._remaining(started,budget,'installation'),progress=progress)
        runner.run(['check'],phase='version_confirmation',seconds=self._remaining(started,budget,'installation'),progress=progress)
        # Discover origins without importing/starting SDK clients or the CLI.
        inspect = ('import importlib.util,sysconfig;from pathlib import Path;'
                   'site=Path(sysconfig.get_path("purelib")).resolve();'
                   'specs=[importlib.util.find_spec(n) for n in ("openai_codex","codex_cli_bin")];'
                   'assert all(s and s.origin and Path(s.origin).resolve().is_relative_to(site) for s in specs)')
        runner.run([],phase='version_confirmation',seconds=self._remaining(started,budget,'installation'),progress=progress,
            command=[str(python),'-I','-B','-c',inspect])
        binding=self.capture(self.environment)
        proofs=self._carry_artifact_proofs(binding,lock,wheels)
        if proofs:binding['artifact_proofs']=proofs
        binding['dependency_lock_digest']=lock['digest']
        binding['digest']=environments.digest({k:v for k,v in binding.items() if k!='digest'})
        for name,record in lock['packages'].items():
            if binding['packages'].get(name)!=record['version']:
                raise failure('sdk_update_not_applied','version_confirmation')
        if set(binding['packages'])-set(lock['packages'])-{'pip','setuptools'}:
            raise failure('sdk_update_not_applied','version_confirmation')
        environments.verify(binding,deep=True)
        environments.verify(self.base['environment'])
        self._remaining(started,budget,'installation')
        receipt={'environment':binding,'lock_digest':lock['digest'],'target_version':lock['target_version'],
                 'cli_version':lock['cli_version'],'cleanup_complete':True,'activated':False}
        atomic(self.area/'candidate.json',receipt)
        return receipt
