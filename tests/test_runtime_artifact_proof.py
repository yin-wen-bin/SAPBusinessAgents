"""Synthetic vendor artifact audit only; never import or install an SDK."""
import base64
import hashlib
import json
from pathlib import Path
import zipfile
from types import SimpleNamespace

import pytest

from sap_business_agents_platform import runtime_environment as env
from tests.test_runtime_environment_activation import fake_environment
from sap_business_agents_platform.codex_environment_update import CodexEnvironmentUpdate
from sap_business_agents_platform.sdk_manager import SDKManagerError


def rebind(binding):
    body={k:v for k,v in binding.items() if k!='digest'}
    return {**body,'digest':env.digest(body)}


def vendor(tmp_path,monkeypatch,*,bad_member=False,duplicate=False,identity=False):
    binding=fake_environment(tmp_path/'environment')
    site=Path(binding['site']);info='openai_codex-1.0.0.dist-info'
    members={info+'/METADATA':(site/info/'METADATA').read_bytes(),
             info+'/WHEEL':b'official platform wheel\n','fixture_sdk.py':b'fixture, never executed\n'}
    if identity:members[info+'/METADATA']=b'Name: different\nVersion: 1.0.0\n'
    if bad_member:members['../outside.py']=b'not allowed'
    rows=[]
    for name,data in members.items():
        hashed=base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
        rows.append(f'{name},sha256={hashed},{len(data)}')
    # Exact original vendor error, not a platform rewrite or repaired RECORD.
    rows[1]=info+'/WHEEL,sha256='+base64.urlsafe_b64encode(hashlib.sha256(b'old').digest()).decode().rstrip('=')+',3'
    record='\n'.join(rows+[info+'/RECORD,,'])+'\n'
    for name,data in members.items():
        if '..' not in Path(name).parts:
            (site/name).write_bytes(data)
    (site/info/'RECORD').write_text(record)
    artifact=tmp_path/'original.whl'
    with zipfile.ZipFile(artifact,'w') as archive:
        for name,data in members.items():archive.writestr(name,data)
        archive.writestr(info+'/RECORD',record)
        if duplicate:archive.writestr(info+'/wheel',b'case alias')
    files,packages=env._inventory(site)
    binding.update(files=files,packages=packages,payload_files=env.payload_files(Path(binding['path'])))
    binding=rebind(binding)
    monkeypatch.setattr(env,'REVIEWED_ARTIFACTS',{('openai-codex','1.0.0'):env.sha(artifact)})
    return binding,artifact,info


def attach(binding,artifact):
    return rebind({**binding,'artifact_proofs':[env.prove_official_artifact(binding,artifact)]})


def test_official_bytes_prove_vendor_record_error_without_mutation(tmp_path,monkeypatch):
    binding,artifact,info=vendor(tmp_path,monkeypatch)
    before={p:p.read_bytes() for p in Path(binding['path']).rglob('*') if p.is_file()}
    with pytest.raises(env.EnvironmentError):env.verify(binding)
    bound=attach(binding,artifact)
    assert env.verify(bound)==bound
    assert all(path.read_bytes()==value for path,value in before.items())
    assert bound['digest']!=binding['digest'] and 'artifact_proofs' not in binding


@pytest.mark.parametrize('fault',['missing','hash','unknown','identity','payload','record','duplicate_proof','unreviewed_hash'])
def test_artifact_proof_failure_remains_closed(tmp_path,monkeypatch,fault):
    binding,artifact,info=vendor(tmp_path,monkeypatch);bound=attach(binding,artifact)
    if fault=='missing':artifact.unlink()
    elif fault=='hash':artifact.write_bytes(artifact.read_bytes()+b'changed')
    elif fault=='unknown':monkeypatch.setattr(env,'REVIEWED_ARTIFACTS',{})
    elif fault=='identity':bound['artifact_proofs'][0]['version']='2.0.0';bound=rebind(bound)
    elif fault=='payload':(Path(binding['site'])/'fixture_sdk.py').write_text('changed')
    elif fault=='record':
        record=Path(binding['site'])/info/'RECORD';record.write_text(record.read_text().replace(',3\n',',4\n'))
        bound.update(files=env._inventory(Path(bound['site']))[0],payload_files=env.payload_files(Path(bound['path'])))
        bound=rebind(bound)  # Re-capture cannot justify a different erroneous row.
    elif fault=='duplicate_proof':bound['artifact_proofs']*=2;bound=rebind(bound)
    else:
        artifact.write_bytes(artifact.read_bytes()+b'changed')
        bound['artifact_proofs'][0]['sha256']=env.sha(artifact);bound=rebind(bound)
    with pytest.raises(env.EnvironmentError):env.verify(bound)


def test_recaptured_changed_installed_member_not_source_proven(tmp_path,monkeypatch):
    binding,artifact,_=vendor(tmp_path,monkeypatch);bound=attach(binding,artifact)
    (Path(binding['site'])/'fixture_sdk.py').write_text('changed')
    bound['payload_files']=env.payload_files(Path(bound['path']));bound=rebind(bound)
    with pytest.raises(env.EnvironmentError):env.verify(bound)


@pytest.mark.parametrize('kind',['bad_member','duplicate','identity'])
def test_reviewed_hash_does_not_authorize_bad_archive_structure(tmp_path,monkeypatch,kind):
    binding,artifact,_=vendor(tmp_path,monkeypatch,**{kind:True})
    with pytest.raises(env.EnvironmentError):attach(binding,artifact)


def test_arbitrary_original_hash_is_not_a_review(tmp_path,monkeypatch):
    binding,artifact,_=vendor(tmp_path,monkeypatch)
    monkeypatch.setattr(env,'REVIEWED_ARTIFACTS',{})
    with pytest.raises(env.EnvironmentError,match='not_reviewed'):attach(binding,artifact)


def test_pip_owned_extra_record_rows_still_strictly_checked(tmp_path,monkeypatch):
    binding,artifact,info=vendor(tmp_path,monkeypatch)
    site=Path(binding['site']);installer=site/info/'INSTALLER';installer.write_bytes(b'pip\n')
    encoded=base64.urlsafe_b64encode(hashlib.sha256(installer.read_bytes()).digest()).decode().rstrip('=')
    record=site/info/'RECORD';record.write_text(record.read_text()+f'{info}/INSTALLER,sha256={encoded},4\n')
    binding.update(files=env._inventory(site)[0],payload_files=env.payload_files(Path(binding['path'])))
    bound=attach(rebind(binding),artifact);assert env.verify(bound)==bound
    installer.write_bytes(b'not pip')
    with pytest.raises(env.EnvironmentError):env.verify(bound)


def test_new_candidate_reproves_same_locked_vendor_artifact(tmp_path,monkeypatch):
    binding,artifact,_=vendor(tmp_path,monkeypatch);base=attach(binding,artifact)
    update=SimpleNamespace(base={'environment':base})
    lock={'packages':{'openai-codex':{'version':'1.0.0','filename':artifact.name,'sha256':env.sha(artifact)}}}
    proofs=CodexEnvironmentUpdate._carry_artifact_proofs(update,binding,lock,tmp_path)
    assert proofs==base['artifact_proofs']
    assert env.verify(rebind({**binding,'artifact_proofs':proofs}))
    assert 'artifact_proofs' not in binding


@pytest.mark.parametrize('fault',['hash','bytes','version'])
def test_candidate_cannot_inherit_wrong_or_changed_artifact(tmp_path,monkeypatch,fault):
    binding,artifact,_=vendor(tmp_path,monkeypatch);base=attach(binding,artifact)
    update=SimpleNamespace(base={'environment':base})
    record={'version':'1.0.0','filename':artifact.name,'sha256':env.sha(artifact)}
    lock={'packages':{'openai-codex':record}}
    if fault=='version':
        record['version']='2.0.0'
        assert CodexEnvironmentUpdate._carry_artifact_proofs(update,binding,lock,tmp_path)==[]
    elif fault=='hash':
        record['sha256']='0'*64
        with pytest.raises(SDKManagerError):CodexEnvironmentUpdate._carry_artifact_proofs(update,binding,lock,tmp_path)
    else:
        artifact.write_bytes(artifact.read_bytes()+b'changed')
        with pytest.raises(env.EnvironmentError):CodexEnvironmentUpdate._carry_artifact_proofs(update,binding,lock,tmp_path)
