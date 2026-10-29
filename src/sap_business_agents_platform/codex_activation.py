"""Durable Codex update receipts and handoff to the existing launcher.

The operation record is not a second active-release pointer. Only the launcher
commits startup-api-current.json, after health and ownership verification.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import runpy
import uuid

from . import runtime_environment as environments
from .codex_environment_update import CodexEnvironmentUpdate
from .sdk_update import failure
from .startup_control import atomic, digest, file_lock, sha, startup_projection


def now():
    return datetime.now(timezone.utc).isoformat()


def configuration_binding(registry):
    paths=[registry.selection_path,registry.repository_root/'.env']
    if registry.authentication_service:
        # Check account/authorization revisions, not volatile check timestamps.
        auth=registry.authentication_service
        with auth.lock:
            accounts={k:{n:v.get(n) for n in ('revision','binding_revision','status','authenticated',
                'identity_digest','authentication_source','site_digest')} for k,v in auth.state['accounts'].items()}
    else:accounts={}
    return {'files':{str(p):sha(p) if p.exists() else None for p in paths},'accounts':accounts}


def read_base(root):
    root=Path(root)
    record=json.loads((root/'.local-data/startup-api-current.json').read_text(encoding='utf-8-sig'))
    archive=Path(record['archive'])
    if archive.resolve()!=root.resolve()/'.local-data/startup-releases'/record['source_digest']:
        raise failure('sdk_update_binding_changed','preparing')
    boot=runpy.run_path(str(archive/'source/scripts/service-startup.py'))
    manifest=json.loads((archive/'manifest.json').read_text(encoding='utf-8-sig'))
    boot['verify_archive'](archive,manifest)
    if manifest['digest']!=record['source_digest']:
        raise failure('sdk_update_binding_changed','preparing')
    binding=record.get('environment') or manifest.get('dependencies',{}).get('environment')
    if not binding:binding=environments.legacy_binding(root,manifest)
    environments.verify(binding)
    running=startup_projection()
    if not running.get('verified') or running.get('source_digest')!=record['source_digest']:
        raise failure('sdk_running_release_unconfirmed','preparing')
    if running.get('environment_digest') not in {None,binding['digest']}:
        raise failure('sdk_update_binding_changed','preparing')
    if (any(type(record.get(k)) is not int or not 0<record[k]<65536 for k in ('api_port','site_port'))
            or running.get('api_port')!=record['api_port']):
        raise failure('sdk_running_release_unconfirmed','preparing')
    return {**record,'environment':binding}


def candidate_release(root,base,receipt):
    """Same frozen source, new dependency binding; no dirty checkout capture."""
    archive=Path(base['archive']); manifest=json.loads((archive/'manifest.json').read_bytes())
    boot=runpy.run_path(str(archive/'source/scripts/service-startup.py'))
    boot['verify_archive'](archive,manifest)
    deps={k:v for k,v in manifest['dependencies'].items() if not k.startswith('.venv/') and k!='interpreter'}
    deps['environment']=receipt['environment']
    body={**manifest,'dependencies':deps};body.pop('digest',None)
    manifest={**body,'digest':digest(body)}
    target=Path(root)/'.local-data/startup-releases'/manifest['digest']
    with file_lock(target.parent/'release.lock',wait=60):
        if not target.exists():
            temporary=target.with_name(target.name+'.tmp-'+uuid.uuid4().hex)
            for name,expected in manifest['files'].items():
                source=archive/'source'/name
                if source.is_symlink() or sha(source)!=expected:
                    raise failure('sdk_update_binding_changed','preparing')
                destination=temporary/'source'/name;destination.parent.mkdir(parents=True,exist_ok=True)
                destination.write_bytes(source.read_bytes())
            atomic(temporary/'manifest.json',manifest)
            boot['verify_archive'](temporary,manifest)
            boot['publish_archive'](temporary,target)
        boot['verify_archive'](target,manifest)
    return {'archive':str(target),'source_digest':manifest['digest'],'environment':receipt['environment'],
            'api_port':base['api_port'],'site_port':base['site_port']}


class CodexActivation:
    def __init__(self,registry,*,installer=CodexEnvironmentUpdate,handoff=None):
        self.registry=registry;self.root=registry.repository_root/'.local-data/sdk-update-jobs/codex'
        self.installer=installer;self.task=None;self.handoff=handoff

    def read(self):
        path=self.root/'current.json'
        if not path.exists():return None
        try:
            pointer=json.loads(path.read_bytes());operation=pointer['operation_id']
            if not re.fullmatch(r'update_[a-f0-9]{32}',operation):raise ValueError('id')
            value=json.loads((self.root/(operation+'.json')).read_bytes())
            if value['operation_id']!=operation:raise ValueError('binding')
            # Reading cannot dispatch, reconcile, resume or rewrite an operation.
            return value
        except (OSError,KeyError,ValueError,TypeError):
            return {'operation_id':'review-required','status':'needs_review','phase':'recovery',
                    'error':{'code':'sdk_update_outcome_unconfirmed'}}

    def status(self):
        value=self.read()
        if value is None:return None
        allowed={'operation_id','status','phase','started_at','updated_at','target_version','before_version',
                 'cleanup_complete','error','outcome','observed_versions','candidate_environment_digest','request_id',
                 'downloaded_bytes','total_bytes','filename','process_id','process_identity'}
        result={k:v for k,v in value.items() if k in allowed}
        restarted=(value.get('status')=='running' and value.get('owner_boot_id') is not None
                   and value['owner_boot_id']!=startup_projection().get('boot_id'))
        if restarted:
            # Projection only. An independent executor may still be completing
            # the same operation; GET must neither resume it nor report success.
            result.update(status='needs_review',recorded_status='running',
                error={'code':'sdk_update_outcome_unconfirmed','phase':value.get('phase')})
        if value.get('target') and (value.get('status')=='needs_review' or restarted):
            result['recovery']={'available':True,'parent_operation_id':value['operation_id'],
                                'target_version':value['target_version']}
        return result

    async def recover(self,definition,operation,target):
        with file_lock(self.root/'operation.lock'):
            record=self.read()
            if (not record or record.get('operation_id')!=operation or record.get('status') not in {'needs_review','running'}
                    or not record.get('target') or target!=record['target_version']):
                raise failure('sdk_update_recovery_unsupported','recovery')
            if self.task and not self.task.done():raise failure('sdk_update_busy','recovery')
            if configuration_binding(self.registry)!=record['configuration']:
                raise failure('sdk_update_binding_changed','recovery')
            # A crashed in-service switch must be reconciled by the existing
            # launcher before a web recovery can hand off a candidate again.
            if (self.registry.repository_root/'.local-data/startup/pending-switch.json').exists():
                raise failure('startup_switch_recovery_required','recovery')
            ticket=self.root/operation/'handoff.json'
            if ticket.exists():
                from .sdk_activation_handoff import inventory
                command=json.loads(ticket.read_bytes())['command']
                if any(p.get('command')==command for p in inventory()['processes']):
                    raise failure('sdk_update_busy','recovery')
            environments.verify(record['base']['environment'])
            environments.verify(record['target']['environment'],deep=True)
            self.emit(record,status='running',phase='awaiting_review',error=None)
            self.task=asyncio.create_task(self.finish(record),name=operation)
        return self.response(record)

    async def finish(self,record):
        try:
            target=record['target']
            environments.verify(target['environment'],deep=True)
            boot=runpy.run_path(str(Path(target['archive'])/'source/scripts/service-startup.py'))
            await asyncio.to_thread(boot['check_admission'],self.registry.repository_root,Path(target['archive']))
            if configuration_binding(self.registry)!=record['configuration']:
                raise failure('sdk_update_binding_changed','awaiting_review')
            self.emit(record,phase='waiting_maintenance')
            handoff=self.handoff
            if handoff is None:
                from .sdk_activation_handoff import handoff as launch
                handoff=launch
            await asyncio.to_thread(handoff,self.registry.repository_root,record)
        except BaseException as exc:
            # Once the gated handoff is accepted, the independent executor owns
            # this receipt. API shutdown must not overwrite its switch result.
            area=self.root/record['operation_id']
            try:
                ticket=json.loads((area/'handoff.json').read_bytes())
                accepted=json.loads((area/'executor-accepted.json').read_bytes())
                if (ticket.get('target')==record['target'] and accepted.get('ticket_digest')==ticket.get('digest')
                        and ticket.get('digest')==digest({k:v for k,v in ticket.items() if k!='digest'})):
                    return
            except (OSError,ValueError,TypeError):pass
            code=getattr(exc,'code',None) or ('sdk_target_evidence_required' if 'integration' in str(exc)
                                            else 'sdk_update_outcome_unconfirmed')
            self.emit(record,status='needs_review',error={'code':code,'phase':record['phase']})

    def emit(self,record,**values):
        # OwnedPip emits from its reader and supervisor threads. Serialize the
        # record and its atomic-write temporary, not only the UI projection.
        with self.registry._updates_lock:
            record.update(values,updated_at=now())
            atomic(self.root/(record['operation_id']+'.json'),record)
            self.registry._updates['codex-python-sdk']=self.status()

    async def submit(self,definition,target,request_id=None):
        if request_id is not None and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',request_id):
            raise failure('sdk_update_request_invalid','preparing')
        request_id=request_id or uuid.uuid4().hex
        body={'sdk_id':definition.sdk_id,'target_version':target}
        fingerprint=digest(body)
        with file_lock(self.root/'operation.lock'):
            # Stable per request, including a crash before publishing current or
            # the request index. An orphan receipt must not create a second job.
            operation='update_'+digest(request_id)[:32]
            original_path=self.root/(operation+'.json')
            if original_path.exists():
                original=json.loads(original_path.read_bytes())
                if original.get('request_id')!=request_id or original.get('request_fingerprint')!=fingerprint:
                    raise failure('sdk_update_request_conflict','preparing')
                return self.response(original)
            request=self.root/'requests'/(digest(request_id)+'.json')
            if request.exists():
                saved=json.loads(request.read_bytes())
                if saved['fingerprint']!=fingerprint:raise failure('sdk_update_request_conflict','preparing')
                record=json.loads((self.root/(saved['operation_id']+'.json')).read_bytes())
                return self.response(record)
            prior=self.read()
            if prior and prior.get('request_id')==request_id:
                if prior.get('request_fingerprint')!=fingerprint:raise failure('sdk_update_request_conflict','preparing')
                return self.response(prior)
            if prior and prior['status'] not in {'completed','failed'}:
                raise failure('sdk_update_busy','preparing')
            if not definition.update_enabled or not target:
                raise failure('sdk_update_target_required','preparing')
            base=await asyncio.to_thread(read_base,self.registry.repository_root)
            from packaging.version import Version
            if Version(target)<=Version(base['environment']['packages']['openai-codex']):
                raise failure('sdk_already_current','preparing')
            record={'version':1,'operation_id':operation,'request_id':request_id,
                    'request_fingerprint':fingerprint,'status':'running','phase':'preparing','started_at':now(),
                    'target_version':target,'before_version':base['environment']['packages']['openai-codex'],
                    'base':base,'configuration':configuration_binding(self.registry),'cleanup_complete':True,
                    'owner_pid':os.getpid(),'owner_boot_id':startup_projection().get('boot_id')}
            self.emit(record)
            atomic(self.root/'current.json',{'operation_id':record['operation_id']})
            atomic(request,{'fingerprint':fingerprint,'operation_id':record['operation_id']})
            self.task=asyncio.create_task(self.run(record),name=record['operation_id'])
        return self.response(record)

    def response(self,record):
        return {'provider_id':'codex','sdk_id':'codex-python-sdk','operation_id':record['operation_id'],
                'maintenance_kind':'online_upgrade','activated':record.get('outcome')=='activated',
                'current_version':record.get('target_version') if record.get('outcome')=='activated' else record.get('before_version'),'restart_required':False,
                'update_status':{k:v for k,v in record.items() if k in {
                    'operation_id','status','phase','started_at','updated_at','target_version','before_version',
                    'error','cleanup_complete','outcome','candidate_environment_digest','request_id','observed_versions'}}}

    async def run(self,record):
        try:
            pipeline=self.installer(self.registry.repository_root,record['base'],record['operation_id'])
            progress=lambda **v:self.emit(record,**v)
            lock=await self.registry._await_update(asyncio.to_thread(pipeline.prepare,record['target_version'],progress))
            if configuration_binding(self.registry)!=record['configuration']:
                raise failure('sdk_update_binding_changed','preparing')
            receipt=await self.registry._await_update(asyncio.to_thread(pipeline.install,lock,progress))
            target=await asyncio.to_thread(candidate_release,self.registry.repository_root,record['base'],receipt)
            self.emit(record,phase='awaiting_review',outcome='candidate_prepared',target=target,
                      candidate_environment_digest=receipt['environment']['digest'],cleanup_complete=True)
            # Use the frozen target's existing integration checker; no login or
            # model probe is allowed to manufacture missing evidence.
            await self.finish(record)
            # The executor now owns the durable record. Do not overwrite it.
        except BaseException as exc:
            code=getattr(exc,'code',None) or ('sdk_target_evidence_required' if 'integration' in str(exc)
                                            else 'sdk_update_outcome_unconfirmed')
            self.emit(record,status='needs_review' if record.get('target') or code=='sdk_target_evidence_required' else 'failed',
                phase='awaiting_review' if code=='sdk_target_evidence_required' else record['phase'],
                error={'code':code,'phase':record['phase']})
