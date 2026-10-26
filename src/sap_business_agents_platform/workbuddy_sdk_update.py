"""WorkBuddy verified package candidates. Preparation never activates a release."""
from email.parser import BytesParser
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import re
import uuid
import zipfile

from packaging.version import Version
from packaging.utils import canonicalize_name

from .sdk_update import CodexPackageUpdate, OwnedPip, failure, sha
from .startup_control import atomic
from .workbuddy_environment import digest


def source_binding(state):
    """Only execution/account/configuration facts, never diagnostics or quality."""
    names=('environment_digest','enabled','default_model_id','configuration_revision','service_site',
           'service_site_revision','service_site_digest','authentication_revision','authentication_identity_digest')
    return {name:copy.deepcopy(state.get(name)) for name in names}


class WorkBuddyPackageUpdate(CodexPackageUpdate):
    package='codebuddy-agent-sdk'
    cli_member='codebuddy_agent_sdk/bin/codebuddy-headless.exe'

    def __init__(self,root,*,repository,python,worker=None,run=None,**kwargs):
        self.repository=Path(repository).resolve();self.python=Path(python).resolve(strict=True)
        if self.python.is_relative_to(self.repository/'.venv'):
            raise failure('workbuddy_platform_interpreter_forbidden','preparing')
        self.bootstrap_hash=sha(self.python)
        self.worker=Path(worker or self.repository/'src/sap_business_agents_platform/workbuddy_worker.py').resolve(strict=True)
        self.worker_hash=sha(self.worker)
        super().__init__(root,run=run or OwnedPip(python=self.python).run,**kwargs)

    def _cache(self,target):
        return self.root/digest([self.package,target,os.name,self.bootstrap_hash])

    def prepare(self,target,progress):
        version=Version(target)
        if version.is_prerelease or version.is_devrelease or version.local:
            raise failure('workbuddy_update_target_unsupported','version_check')
        if sha(self.python)!=self.bootstrap_hash:
            raise failure('sdk_update_binding_changed','verification')
        return super().prepare(str(version),progress)

    def install(self,bundle,progress):
        # Never inherit Codex's in-place installation into the bootstrap.
        raise failure('workbuddy_update_controlled_activation_required','installation')

    def _cli_contract(self,sdk,files):
        # Support the currently reviewed bundled-CLI distribution. Unknown new
        # layouts fail explicitly, rather than guessing a CLI package/version.
        with zipfile.ZipFile(sdk) as archive:
            metadata=[n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
            if len(metadata)!=1:raise ValueError('metadata_ambiguous')
            data=BytesParser().parsebytes(archive.read(metadata[0]))
            from packaging.utils import parse_wheel_filename
            if (canonicalize_name(data['Name'])!=self.package
                    or Version(data['Version'])!=parse_wheel_filename(sdk.name)[1]):
                raise ValueError('metadata_mismatch')
            entries=[i for i in archive.infolist() if i.filename==self.cli_member]
            if len(entries)!=1 or not 0<entries[0].file_size<=1_000_000_000:
                raise failure('workbuddy_update_target_unsupported','verification',reason='bundled_cli_layout_unreviewed')
            with archive.open(self.cli_member) as stream:
                cli_hash=hashlib.file_digest(stream,'sha256').hexdigest()
        return {'cli_version':None,'cli_binding':{'kind':'bundled','member':self.cli_member,'sha256':cli_hash}}

    def _verify_cli(self,sdk,bundle):
        if self._cli_contract(sdk,bundle['files'])!={'cli_version':bundle['cli_version'],'cli_binding':bundle['cli_binding']}:
            raise ValueError('cli_mismatch')

    def prepare_candidate(self,bundle,binding,progress,*,operation_id=None):
        if sha(self.python)!=self.bootstrap_hash:raise failure('sdk_update_binding_changed','preparing')
        directory=Path(bundle['directory']).resolve()
        if directory!=self._cache(bundle['target']):raise failure('sdk_download_integrity_failed','verification')
        self._verify_files(directory,bundle)
        operation_id=operation_id or 'update_'+uuid.uuid4().hex
        if not re.fullmatch(r'update_[a-f0-9]{32}',operation_id):
            raise failure('sdk_update_plan_invalid','preparing')
        transaction=self.root/('candidate-'+operation_id)
        if sha(self.worker)!=self.worker_hash:raise failure('sdk_update_binding_changed','preparing')
        request={'repository':str(self.repository),'python':str(self.python),'python_sha256':self.bootstrap_hash,
                 'worker':str(self.worker),'worker_sha256':self.worker_hash,'bundle':bundle,'source_binding':binding,'operation_id':operation_id}
        if transaction.exists():
            if (json.loads((transaction/'request.json').read_bytes())!=request or not (transaction/'result.json').is_file()):
                raise failure('sdk_update_outcome_unconfirmed','preparing')
            receipt=json.loads((transaction/'result.json').read_bytes())
            self._verify_prepared_receipt(receipt,bundle,request,binding)
            return receipt
        transaction.mkdir()
        path=transaction/'request.json';atomic(path,request)
        helper=Path(__file__).parents[2]/'scripts/prepare-workbuddy-package.py'
        progress(phase='installation')
        self.run([],command=[str(self.python),'-I','-B',str(helper),'--request',str(path),
                 '--request-sha256',sha(path)],phase='installation',seconds=700,progress=progress)
        receipt=json.loads((transaction/'result.json').read_bytes())
        self._verify_prepared_receipt(receipt,bundle,request,binding)
        return receipt

    def _verify_prepared_receipt(self,receipt,bundle,request,binding):
        if (receipt.get('source_binding')!=binding or receipt.get('sdk_version')!=bundle['target']
                or receipt.get('activated') is not False or receipt.get('cleanup_complete') is not True
                or receipt.get('operation_id')!=request['operation_id']):
            raise failure('sdk_update_not_applied','version_confirmation')
        release=self.verify_candidate(receipt)
        if (release['sdk_wheel_digest']!=next(v['sha256'] for v in bundle['files'].values() if v['name']==self.package)
                or release['files'][release['cli']]!=bundle['cli_binding']['sha256']
                or release['files']['worker.py']!=request['worker_sha256']
                or release['python_bootstrap_digest']!=self.bootstrap_hash):
            raise failure('sdk_download_integrity_failed','version_confirmation')
        return release

    def verify_candidate(self,receipt):
        from .workbuddy_environment import WorkBuddyEnvironment
        env=WorkBuddyEnvironment(self.repository)
        key=receipt.get('environment_digest')
        if not isinstance(key,str) or not re.fullmatch(r'[a-f0-9]{64}',key):raise failure('sdk_update_plan_invalid','version_confirmation')
        release=env.release(key)
        if release['sdk_version']!=receipt.get('sdk_version') or release['cli_version']!=receipt.get('cli_version'):
            raise failure('sdk_update_not_applied','version_confirmation')
        return release


def activate_candidate(environment,receipt,*,verify_target,clearance):
    """Private controlled-maintenance primitive, never the update REST action.

    verify_target is the trusted platform's target-specific authentication,
    integration and model admission auditor, not a request-supplied boolean.
    Absence of that auditor is a hard block. Offline tests inject synthetic
    auditors; this module cannot manufacture target qualification evidence.
    """
    if not callable(verify_target) or not callable(clearance):
        raise failure('workbuddy_update_evidence_required','version_confirmation')
    with environment.maintenance():
        state=environment.state();key=receipt['environment_digest']
        if source_binding(state)!=receipt['source_binding']:
            raise failure('sdk_update_binding_changed','version_confirmation')
        release=environment.release(key)
        proof=clearance()
        if (proof.get('state_digest')!=digest(state) or not proof.get('all_writers_checked')
                or any(proof.get(k)!=[] for k in ('old_writers','active_jobs','cleanup_pending'))
                or not proof.get('process_inventory_digest') or not time.time()-30<=proof.get('checked_at',0)<=time.time()+1):
            raise failure('workbuddy_integration_writer_clearance_required','version_confirmation')
        # Existing platform validators own all credentials/qualification. This
        # hook must raise unless the exact target and saved model are admitted.
        target=verify_target(release,copy.deepcopy(state))
        from . import workbuddy_integration as integration
        certificate=target['certificate']
        if integration.audit(environment,release,certificate['evidence'])!=certificate:
            raise failure('workbuddy_update_evidence_required','version_confirmation')
        snapshot=target['snapshot'];check=target['model_check']
        candidate_state=copy.deepcopy(state)
        candidate_state.update(environment_digest=key,authentication_environment=key)
        candidate_state.setdefault('models',{})[state['default_model_id']]=check
        # Reuse the real candidate-environment binding checker. The injected
        # audit must independently prove authentication for the target SDK;
        # copying old authentication metadata is not that proof.
        if target.get('authentication_environment')!=key:
            raise failure('workbuddy_update_evidence_required','version_confirmation')
        facade=copy.copy(environment);facade.state=lambda:candidate_state
        facade.assert_verification_binding(snapshot)
        if (snapshot.get('environment_digest')!=key
                or snapshot.get('model')!=state.get('default_model_id')
                or snapshot.get('authentication_binding_revision')!=state.get('authentication_revision')
                or snapshot.get('service_site_digest')!=state.get('service_site_digest')
                or target.get('admitted') is not True):
            raise failure('workbuddy_update_evidence_required','version_confirmation')
        final_proof=clearance()
        if (final_proof.get('state_digest')!=digest(state) or not final_proof.get('all_writers_checked')
                or any(final_proof.get(k)!=[] for k in ('old_writers','active_jobs','cleanup_pending'))
                or not final_proof.get('process_inventory_digest')
                or not time.time()-30<=final_proof.get('checked_at',0)<=time.time()+1):
            raise failure('workbuddy_integration_writer_clearance_required','version_confirmation')
        def change(value):
            if value!=state:raise failure('sdk_update_binding_changed','version_confirmation')
            value.setdefault('sdk_integration_records',{})[certificate['id']]=certificate
            model=state['default_model_id']
            value.setdefault('model_check_history',[]).append(copy.deepcopy(state.get('models',{}).get(model,{})))
            value.setdefault('models',{})[model]=copy.deepcopy(check)
            value.setdefault('environment_switch_history',[]).append({'from':state['environment_digest'],'to':key,
                'source_binding':receipt['source_binding'],'certificate':certificate['id']})
            value.update(environment_digest=key,authentication_environment=key)
        environment.mutate(change)
        return {'status':'activated','environment_digest':key,'previous_environment_digest':state['environment_digest']}


def rollback_candidate(environment,previous_environment_digest,*,verify_target,clearance):
    """Rollback uses the same target audit, never restores a whole old ledger."""
    old=environment.release(previous_environment_digest)
    receipt={'environment_digest':previous_environment_digest,'sdk_version':old['sdk_version'],
             'cli_version':old['cli_version'],'source_binding':source_binding(environment.state())}
    return activate_candidate(environment,receipt,verify_target=verify_target,clearance=clearance)
