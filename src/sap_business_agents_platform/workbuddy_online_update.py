"""Explicit, durable WorkBuddy maintenance. GET never dispatches or resumes work."""
from __future__ import annotations
import asyncio
import copy
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .sdk_update import failure
from .startup_control import atomic, file_lock
from .workbuddy_sdk_update import source_binding, activate_candidate, rollback_candidate

BUSY = {'running', 'needs_review', 'cleanup_pending'}
TERMINAL = {'completed', 'failed', 'rolled_back'}


class OnlineUpdate:
    def __init__(self, registry):
        self.registry = registry
        self.root = registry.repository_root / '.local-data/sdk-update-jobs/workbuddy'
        self.path = self.root / 'current.json'
        self.task = None
        self.held_guard = None
        self.held_barrier = None
        self.record = None
        self.recover()

    def recover(self):
        if not self.path.exists(): return
        try:
            record = json.loads(self.path.read_bytes())
            if (record.get('version') != 1 or not isinstance(record.get('operation_id'), str)
                    or record.get('status') not in BUSY | TERMINAL):
                raise ValueError('invalid_receipt')
            if record['status'] in BUSY:
                # An interrupted model submission is consumed, never retried.
                if record.get('model_submitted') or record.get('phase') in {'installation','authentication_verification','infrastructure_validation','model_verification','switching','health_check','rollback'}:
                    record['cleanup_complete']=False
                record.update(status='needs_review', error={'code':'sdk_update_outcome_unconfirmed',
                    'phase':record.get('phase','version_confirmation')})
                atomic(self.path, record)
            self.record = record
            self.publish()
        except (ValueError, OSError, TypeError):
            self.registry._updates['codebuddy-agent-sdk'] = {'operation_id':'review-required',
                'status':'needs_review','phase':'version_confirmation',
                'error':{'code':'sdk_update_outcome_unconfirmed','phase':'version_confirmation'}}

    def publish(self):
        if self.record is None: return
        public = {k:copy.deepcopy(v) for k,v in self.record.items() if k in {
            'operation_id','status','phase','started_at','updated_at','target_version','before_version',
            'candidate_environment_digest','error','outcome','cleanup_complete','observed_versions',
            'downloaded_bytes','total_bytes','filename','cache_reused','model_submitted','model_check_limit'}}
        with self.registry._updates_lock:
            self.registry._updates['codebuddy-agent-sdk'] = public

    def emit(self, **change):
        self.record.update(change, updated_at=datetime.fromtimestamp(time.time(),timezone.utc).isoformat())
        atomic(self.path,self.record)
        atomic(self.root/(self.record['operation_id']+'.json'),self.record)
        self.publish()

    def bind_authentication_service(self):
        if (self.record and self.record.get('status') in BUSY
                and (self.record.get('cleanup_complete') is False or self.record.get('switch_intent'))):
            self.registry.authentication_service.sdk_maintenance_active.add('workbuddy')

    async def submit(self, definition, target):
        registry = self.registry
        if not definition.update_enabled: raise failure('sdk_update_disabled','version_check')
        prior=registry.maintenance_status('workbuddy') or {}
        if prior.get('status') in BUSY:
            raise failure('sdk_update_busy' if prior['status']=='running' else 'sdk_update_outcome_unconfirmed','version_check')
        if not isinstance(target,str) or not target:
            raise failure('sdk_update_target_required','version_check')
        from packaging.version import Version, InvalidVersion
        try: version=Version(target)
        except InvalidVersion: raise failure('workbuddy_update_target_unsupported','version_check') from None
        if version.is_prerelease or version.is_devrelease or version.local:
            raise failure('workbuddy_update_target_unsupported','version_check')
        with file_lock(self.root/'submission.lock',wait=0):
            if self.task is not None and not self.task.done(): raise failure('sdk_update_busy','version_check')
            if self.record is None: self.recover()
            prior=registry.maintenance_status('workbuddy') or {}
            if prior.get('status') in BUSY:
                raise failure('sdk_update_busy' if prior['status']=='running' else 'sdk_update_outcome_unconfirmed','version_check')
            if self.task is not None and not self.task.done(): raise failure('sdk_update_busy','version_check')
            state=registry.workbuddy.environment.state()
            release=await asyncio.to_thread(registry.workbuddy.environment.release)
            if Version(target)<=Version(release['sdk_version']): raise failure('sdk_already_current','version_check')
            now=time.time()
            self.record={'version':1,'operation_id':'update_'+uuid.uuid4().hex,'status':'running',
                'phase':'version_check','target_version':str(version),'before_version':release['sdk_version'],
                'source_binding':source_binding(state),'source_environment_digest':release['environment_digest'],
                'started_at':datetime.fromtimestamp(now,timezone.utc).isoformat(),
                'deadline_at':now+3600,'model_submitted':False,'model_check_limit':1,'cleanup_complete':True}
            self.emit()
            self.task=asyncio.create_task(self.run(definition),name=self.record['operation_id'])
        return {'provider_id':'workbuddy','sdk_id':definition.sdk_id,'operation_id':self.record['operation_id'],
            'current_version':release['sdk_version'],'cli_version':release['cli_version'],
            'environment_digest':release['environment_digest'],'update_status':registry.maintenance_status('workbuddy'),
            'maintenance_kind':'online_upgrade','activated':False,'restart_required':False}

    def check_binding(self):
        if time.time()>=self.record['deadline_at']:raise failure('sdk_update_deadline_exceeded',self.record['phase'])
        if source_binding(self.registry.workbuddy.environment.state())!=self.record['source_binding']:
            raise failure('sdk_update_binding_changed',self.record['phase'])

    def claim_model(self):
        if self.record['model_submitted']:raise failure('sdk_update_model_already_submitted','model_verification')
        self.emit(model_submitted=True,phase='model_verification')  # durable before dispatch

    async def run(self, definition):
        registry=self.registry; backend=None;guard=None;reserved=False;activated=False;barrier=None
        try:
            async with asyncio.timeout(max(0,self.record['deadline_at']-time.time())):
                versions=await registry._workbuddy_versions(definition)
                if versions['version_check']['status']!='checked':
                    raise failure(versions['version_check']['error']['code'],'version_check')
                if versions['latest_version']!=self.record['target_version']:
                    raise failure('sdk_update_binding_changed','version_check')
                self.check_binding()
                updater=await asyncio.to_thread(registry._workbuddy_updater)
                original_runner=getattr(updater,'run',None)
                if callable(original_runner):
                    def bounded_runner(*args,**kwargs):
                        remaining=max(0,self.record['deadline_at']-time.time())
                        if remaining<=0:raise failure('sdk_update_deadline_exceeded',kwargs.get('phase','installation'))
                        kwargs['seconds']=min(kwargs['seconds'],remaining)
                        return original_runner(*args,**kwargs)
                    updater.run=bounded_runner
                self.emit(phase='downloading')
                bundle=await registry._await_update(asyncio.to_thread(updater.prepare,self.record['target_version'],self.emit))
                self.emit(phase='waiting_maintenance')
                if registry.authentication_service is None:raise failure('workbuddy_update_evidence_required','authentication_verification')
                guard=registry.authentication_service.sdk_maintenance('workbuddy');await guard.__aenter__();reserved=True;self.held_guard=guard
                self.check_binding()
                backend=UpdateBackend(registry,self.record)
                await asyncio.to_thread(backend.preflight)
                receipt=await registry._await_update(asyncio.to_thread(updater.prepare_candidate,bundle,
                    self.record['source_binding'],self.emit,operation_id=self.record['operation_id']))
                self.emit(candidate_environment_digest=receipt['environment_digest'])
                self.check_binding()
                target=await backend.verify(receipt,self.emit,self.claim_model)
                self.check_binding()
                # No await between final clearance, atomic environment switch and account publication.
                self.emit(phase='switching',switch_intent=True)
                barrier=await asyncio.to_thread(backend.enter_switch)
                self.held_barrier=barrier
                try:
                    await asyncio.to_thread(backend.activate,receipt,target)
                    activated=True
                    self.emit(phase='health_check',activated=True)
                    await asyncio.to_thread(backend.health,receipt,target)
                    if not backend.cleanup_complete():raise failure('runtime_cleanup_incomplete','health_check')
                except BaseException:
                    if not backend.is_target(receipt) and registry.workbuddy.environment.state()['environment_digest']==self.record['source_environment_digest']:
                        backend.switch_safe=True
                    if backend.is_target(receipt):
                        self.emit(phase='rollback')
                        await backend.rollback(self.emit)
                        activated=False
                        self.emit(status='rolled_back',phase='rolled_back',outcome='old_environment_restored',cleanup_complete=True)
                    raise
                finally:
                    if backend.switch_safe and backend.cleanup_complete():
                        await asyncio.to_thread(backend.leave_switch,barrier)
                        self.held_barrier=None
                self.emit(status='completed',phase='completed',outcome='activated',activated=True,
                    cleanup_complete=True,observed_versions={'sdk':receipt['sdk_version'],'cli':receipt['cli_version']},error=None)
        except BaseException as exc:
            phase=self.record.get('phase','version_check')
            code=getattr(exc,'code','sdk_update_interrupted' if isinstance(exc,asyncio.CancelledError) else
                'sdk_update_deadline_exceeded' if isinstance(exc,TimeoutError) else 'sdk_update_failed')
            try: clean=(backend.cleanup_complete() if backend else True) and self.record.get('cleanup_complete') is not False
            except Exception: clean=False
            state='cleanup_pending' if not clean else 'rolled_back' if self.record.get('status')=='rolled_back' else 'needs_review' if activated or backend is not None and not backend.switch_safe or phase in {'installation','cleanup','version_confirmation'} or isinstance(exc,(asyncio.CancelledError,TimeoutError)) and self.record.get('model_submitted') else 'failed'
            self.emit(status=state,phase=phase,cleanup_complete=clean,error={'code':code,'phase':phase})
        finally:
            if backend:
                try: await backend.close()
                except BaseException:
                    self.emit(status='cleanup_pending',cleanup_complete=False,error={'code':'runtime_cleanup_incomplete','phase':'cleanup'})
            if backend and self.record.get('cleanup_complete') is False and backend.switch_safe:
                try:
                    if backend.cleanup_complete(): self.emit(status='needs_review',cleanup_complete=True)
                except Exception: pass
            if reserved and self.record.get('cleanup_complete') is True and (backend is None or backend.switch_safe):
                await guard.__aexit__(None,None,None)
                self.held_guard=None

    async def close(self):
        if self.task is not None and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)


class CandidateSession:
    """Independent state/journal; immutable installed binaries stay in their original release."""
    def __init__(self,registry,record,key,*,rollback=False):
        from .workbuddy_manager import WorkBuddyManager
        from .workbuddy_environment import WorkBuddyEnvironment,atomic_json
        from .workbuddy_supervisor import WorkBuddySupervisor
        from .runtime_authentication import RuntimeAuthenticationService
        from .runtime_authentication_adapters import OfficialAuthenticationAdapter
        original=registry.workbuddy.environment
        area=registry.repository_root/'.local-data/sdk-update-jobs/workbuddy'/record['operation_id']/('rollback' if rollback else 'candidate')
        self.manager=WorkBuddyManager(area)
        self.environment=self.manager.environment
        self.environment.release=lambda environment_digest=None:original.release(environment_digest or key)
        state=copy.deepcopy(original.state());state.update(environment_digest=key,authentication_environment=None)
        atomic_json(self.environment.state_path,state)
        self.manager.supervisor=WorkBuddySupervisor(self.environment)
        self.adapter=OfficialAuthenticationAdapter(registry.repository_root,'workbuddy',self.manager)
        self.authentication=RuntimeAuthenticationService(area/'authentication',{'workbuddy':self.adapter})
        self.authentication.state={'version':1,'accounts':{'workbuddy':copy.deepcopy(registry.authentication_service._account('workbuddy'))},'operations':{}}
        self.authentication.changed=lambda _p,account:self.environment.mutate(lambda s:s.update(
            authenticated=account.get('authenticated'),authentication_revision=account['revision'],
            authentication_environment=key if account.get('authenticated') else None,
            authentication_identity_digest=(account.get('confirmed_binding') or {}).get('identity_digest'),
            authentication_site=self.adapter.context()))

    async def authenticate(self):
        from .runtime_authentication import AUTH_CHECK_SECONDS
        result=await asyncio.wait_for(self.authentication.check('workbuddy'),AUTH_CHECK_SECONDS+10)
        if result.get('authenticated') is not True or result.get('authentication_context_status')!='current':
            raise failure('workbuddy_update_authentication_failed','authentication_verification')

    async def close(self):
        pending=[entry[1] for entry in self.authentication._shared_checks.values() if not entry[1].done()]
        for task in pending:task.cancel()
        if pending:await asyncio.gather(*pending,return_exceptions=True)
        await self.authentication.close()
        await self.manager.supervisor.close()


class UpdateBackend:
    def __init__(self,registry,record):
        self.registry=registry;self.record=record;self.session=None;self.rollback_session=None;self.switch_safe=True
        self.old_account=copy.deepcopy(registry.authentication_service._account('workbuddy'))
        self.old_state=copy.deepcopy(registry.workbuddy.environment.state())
        self.old_snapshot={**registry.workbuddy.runtime_snapshot(require_enabled=False),'authentication_binding_revision':self.old_account['revision']}
        integration=registry.workbuddy.integration(self.old_state,registry.workbuddy.environment.release())
        self.old_certificates=[self.old_state['sdk_integration_records'][key] for key in integration['certificate_ids']]
        self.old_operations=copy.deepcopy(integration['operations'])
        self.old_certificate=self.old_certificates[-1]
        self.old_check=self.old_state['models'][self.old_state['default_model_id']]

    def preflight(self):
        supervisor=self.registry.workbuddy.supervisor
        if supervisor.active or supervisor.queued or supervisor.pending():raise failure('runtime_authentication_busy','waiting_maintenance')
        for session in (self.session,self.rollback_session):
            if session and (session.manager.supervisor.active or session.manager.supervisor.pending() or session.authentication.checking or not session.adapter.cleanup_complete):
                raise failure('runtime_cleanup_incomplete','cleanup')

    async def verify(self,receipt,emit,claim_model):
        from .workbuddy_online_evidence import infrastructure,certificate,copy_evidence
        from .workbuddy_sdk_target_audit import TargetAdmissionAudit
        self.session=CandidateSession(self.registry,self.record,receipt['environment_digest'])
        emit(phase='authentication_verification');await self.session.authenticate()
        # Compare the real candidate account before any model dispatch.
        account=self.session.authentication._account('workbuddy')
        source=self.old_account.get('confirmed_binding') or {}
        if (account['revision']!=self.old_account['revision'] or account.get('confirmed_binding')!=source):
            raise failure('sdk_update_binding_changed','authentication_verification')
        emit(phase='infrastructure_validation')
        report=await self.registry._await_update(asyncio.to_thread(infrastructure,self.registry,self.record,emit))
        claim_model()
        result=await self.session.manager.check_model(self.old_state['default_model_id'],request_id=self.record['operation_id'])
        check=result.get('check') or {}
        if check.get('compatible') is not True or not check.get('identity_known'):
            raise failure('workbuddy_update_model_failed','model_verification')
        self.preflight()
        if self.session.authentication.view('workbuddy').get('authentication_context_status')!='current':
            emit(phase='authentication_verification');await self.session.authenticate()
        cert=await asyncio.to_thread(certificate,self.session,self.old_certificate,report,operations=self.old_operations,supports=self.old_certificates)
        self.session.environment.mutate(lambda s:s.setdefault('sdk_integration_records',{}).__setitem__(cert['id'],cert))
        snapshot=self.session.authentication.bind('workbuddy',self.session.manager.runtime_snapshot(require_enabled=False))
        audit=TargetAdmissionAudit(self.session.authentication,cert,check,snapshot)
        audit(self.registry.workbuddy.environment.release(receipt['environment_digest']),self.old_state)
        await asyncio.to_thread(copy_evidence,self.session.environment,self.registry.workbuddy.environment,cert,check)
        return audit

    def clearance(self):
        from .workbuddy_environment import digest
        from .startup_control import activity
        from .config import Settings
        import subprocess
        data=self.registry.selection_path.parent.parent
        self.preflight()
        if activity(data):raise failure('runtime_authentication_busy','switching')
        # The existing launcher owns cross-process writer detection.
        import runpy
        boot=runpy.run_path(str(Path(__file__).parents[2]/'scripts/service-startup.py'))
        pids=[os.getpid()]
        pointer=self.registry.repository_root/'.local-data/site-builds/current.json'
        if pointer.exists():pids.append(json.loads(pointer.read_bytes())['pid'])
        boot['assert_writers'](self.registry.repository_root,pids)
        rows=subprocess.check_output(['powershell.exe','-NoProfile','-Command',
            'Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CreationDate,Name | ConvertTo-Json -Compress'],text=True)
        return {'all_writers_checked':True,'old_writers':[],'active_jobs':[],'cleanup_pending':[],
            'state_digest':digest(self.registry.workbuddy.environment.state()),'process_inventory_digest':digest(rows),'checked_at':time.time()}

    def enter_switch(self):
        from contextlib import ExitStack
        from .config import Settings
        data=self.registry.selection_path.parent.parent
        stack=ExitStack()
        try:
            stack.enter_context(file_lock(data/'deployment.lock',wait=0))
            stack.enter_context(file_lock(data/'startup-admission.lock',wait=0))
            self.clearance()
            marker=data/'startup-maintenance.json'
            if marker.exists():raise failure('sdk_update_busy','switching')
            atomic(marker,{'pid':os.getpid(),'operation':self.record['operation_id'],'kind':'workbuddy_online_update'})
            self.switch_safe=False
            return stack,marker
        except BaseException:
            stack.close();raise

    def leave_switch(self,barrier):
        stack,marker=barrier
        try:
            if json.loads(marker.read_bytes()).get('operation')==self.record['operation_id']:marker.unlink()
        finally:stack.close()

    def sync_account(self,session):
        auth=self.registry.authentication_service
        with auth.lock:
            auth.state['accounts']['workbuddy']=copy.deepcopy(session.authentication._account('workbuddy'))
            auth._save()

    def activate(self,receipt,audit):
        activate_candidate(self.registry.workbuddy.environment,receipt,verify_target=audit,clearance=self.clearance)
        self.sync_account(self.session)

    def is_target(self,receipt):
        return self.registry.workbuddy.environment.state()['environment_digest']==receipt['environment_digest']

    def health(self,receipt,audit):
        registry=self.registry;env=registry.workbuddy.environment
        if not self.is_target(receipt):raise failure('sdk_update_not_applied','health_check')
        release=env.release()
        if release['sdk_version']!=receipt['sdk_version'] or release['cli_version']!=receipt['cli_version']:
            raise failure('sdk_update_not_applied','health_check')
        snapshot=registry.workbuddy.runtime_snapshot(require_enabled=False)
        audit.authentication.assert_binding('workbuddy',snapshot)
        account=registry.authentication_service.view('workbuddy')
        if account.get('authenticated') is not True or account.get('authentication_context_status')!='current':
            raise failure('workbuddy_update_authentication_failed','health_check')
        if self.registry.workbuddy.supervisor.pending():raise failure('runtime_cleanup_incomplete','health_check')
        self.switch_safe=True

    async def rollback(self,emit):
        from .workbuddy_sdk_target_audit import TargetAdmissionAudit
        self.rollback_session=CandidateSession(self.registry,self.record,self.old_state['environment_digest'],rollback=True)
        from .workbuddy_online_evidence import copy_job
        copy_job(self.registry.workbuddy.environment,self.rollback_session.environment,self.old_check)
        self.rollback_session.environment.mutate(lambda s:s.setdefault('models',{}).__setitem__(s['default_model_id'],self.old_check))
        await self.rollback_session.authenticate()
        env=self.registry.workbuddy.environment
        audit=TargetAdmissionAudit(self.rollback_session.authentication,self.old_certificate,self.old_check,self.old_snapshot)
        await asyncio.to_thread(rollback_candidate,env,self.old_state['environment_digest'],verify_target=audit,clearance=self.clearance)
        self.sync_account(self.rollback_session)
        env.assert_verification_binding(self.old_snapshot)
        self.rollback_session.authentication.assert_binding('workbuddy',self.old_snapshot)
        self.switch_safe=True

    def cleanup_complete(self):
        for session in (self.session,self.rollback_session):
            if session and (session.manager.supervisor.active or session.manager.supervisor.pending() or session.authentication.checking or not session.adapter.cleanup_complete):return False
        return not self.registry.workbuddy.supervisor.pending()

    async def close(self):
        for session in (self.session,self.rollback_session):
            if session:await session.close()
