"""Target-bound infrastructure receipts from actual checks, never business PASS."""
import copy
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from .sdk_update import OwnedPip, failure
from .workbuddy_environment import atomic_json, digest, file_hash
from . import workbuddy_integration as integration


def infrastructure(registry,record,emit):
    root=Path(__file__).parents[2]
    area=registry.repository_root/'.local-data/sdk-update-jobs/workbuddy'/record['operation_id']/'infrastructure'
    area.mkdir(parents=True,exist_ok=True)
    report=area/'results.xml'
    if report.exists():raise failure('sdk_update_outcome_unconfirmed','infrastructure_validation')
    helper=root/'scripts/verify-workbuddy-update-infrastructure.py'
    OwnedPip().run([],command=[sys.executable,'-I','-B',str(helper),'--output',str(area)],
        phase='infrastructure_validation',seconds=300,progress=emit)
    cases=ET.parse(report).findall('.//testcase')
    if not cases or any(c.findall('failure') or c.findall('error') or c.findall('skipped') for c in cases):
        raise failure('workbuddy_update_infrastructure_failed','infrastructure_validation')
    for module,name in integration.CORE_TESTS.values():
        if not any(c.get('classname','').split('.')[-1]==module and c.get('name','').split('[',1)[0]==name for c in cases):
            raise failure('workbuddy_update_infrastructure_failed','infrastructure_validation')
    return report


def certificate(session,source_certificate,report,*,operations=None,supports=None):
    env=session.environment;state=env.state();release=env.release();target=integration.binding(release)
    check=state['models'][state['default_model_id']]
    from .workbuddy_sdk_target_audit import target_model_origin
    if not target_model_origin(env,check,state['default_model_id']):
        raise failure('workbuddy_update_evidence_required','model_verification')
    area='integration-evidence/online-update-'+release['environment_digest']
    def save(name,data):
        path=env.root/area/name;path.parent.mkdir(parents=True,exist_ok=True)
        raw=data if isinstance(data,bytes) else json.dumps(data,sort_keys=True,ensure_ascii=False).encode()
        if path.exists() and path.read_bytes()!=raw:raise failure('sdk_update_binding_changed','infrastructure_validation')
        if not path.exists():path.write_bytes(raw)
        return {'path':path.relative_to(env.root).as_posix(),'sha256':file_hash(path)}
    report_ref=save('checks.xml',Path(report).read_bytes())
    checker_files=integration.checker_manifest()
    sources={name:save('checkers/'+name,(Path(__file__).parents[2]/'tests'/name).read_bytes()) for name in checker_files}
    core={}
    for name in integration.CORE:
        core[name]=save('core-'+name+'.json',{'target':target,'kind':'sdk_infrastructure_check',
            'complete':True,'assertions':{name:True},'checker_contract':integration.CERTIFICATE,
            'checker_files':checker_files,'checker_sources':sources,'test_report':report_ref,
            'scope':'Exact platform supervision, authorization and cleanup checks; target native transport independently observed in the single compatibility job.',
            'not_business_pass':True})
    job=env.root/'jobs'/(digest(check['source_job_id'])+'.json')
    origin={'path':job.relative_to(env.root).as_posix(),'sha256':file_hash(job)}
    supported_operations=operations or source_certificate['operations']
    operations={}
    for operation,modes in supported_operations.items():
        operations[operation]={}
        for mode in modes:
            origins=[c['id'] for c in (supports or [source_certificate]) if mode in c['operations'].get(operation,[])]
            if not origins:raise failure('workbuddy_update_evidence_required','infrastructure_validation')
            operations[operation][mode]=save(operation+'-'+mode+'.json',{'target':target,
                'operation':operation,'permission_mode':mode,'kind':'sdk_infrastructure_check',
                'complete':True,'cleanup_complete':True,'test_report':report_ref,
                'original_operation_support':origins,
                'worker_sha256':release['files'][release['worker']],
                'native_transport_origin':origin,'not_business_pass':True})
    evidence={'target':target,'transport_origin':origin,'core':core,'operations':operations}
    return integration.audit(env,release,evidence)


def copy_job(source,destination,check):
    relative='jobs/'+digest(check['source_job_id'])+'.json'
    path=source.root/relative;target=destination.root/relative
    data=json.loads(path.read_bytes())
    if data.get('cleanup_complete') is not True:raise failure('runtime_cleanup_incomplete','model_verification')
    if target.exists():
        if target.read_bytes()!=path.read_bytes():raise failure('sdk_update_binding_changed','model_verification')
    else:atomic_json(target,data)


def copy_evidence(source,destination,certificate,check):
    seen=set()
    def visit(value):
        if isinstance(value,dict):
            if set(value)=={'path','sha256'}:
                if digest(value) in seen:return
                if len(seen)>=512:raise failure('workbuddy_update_evidence_required','version_confirmation')
                seen.add(digest(value))
                relative=value['path'];src=(source.root/relative).resolve();dst=(destination.root/relative).resolve()
                if not src.is_relative_to(source.root.resolve()) or not dst.is_relative_to(destination.root.resolve()) or file_hash(src)!=value['sha256']:
                    raise failure('workbuddy_update_evidence_required','version_confirmation')
                if relative.startswith('jobs/'):
                    copy_job(source,destination,check)
                else:
                    dst.parent.mkdir(parents=True,exist_ok=True)
                    if dst.exists() and dst.read_bytes()!=src.read_bytes():raise failure('sdk_update_binding_changed','version_confirmation')
                    if not dst.exists():dst.write_bytes(src.read_bytes())
                if src.suffix=='.json':visit(json.loads(src.read_bytes()))
            else:
                for item in value.values():visit(item)
        elif isinstance(value,list):
            for item in value:visit(item)
    visit(certificate['evidence'])
    copy_job(source,destination,check)
