"""An SDK pip update must not silently become an approved startup binding."""
import copy
from pathlib import Path

import pytest

from tests.test_startup_versioning import launcher, orchestrator


def approved_pair(tmp_path):
    dependencies={'sdk/METADATA':'version-162','sdk/RECORD':'content-162'}
    manifest={'version':1,'files':{},'dependencies':dependencies}
    manifest['digest']=launcher.control.digest(manifest)
    archive=tmp_path/'.local-data/startup-releases'/manifest['digest']
    (archive/'source').mkdir(parents=True)
    launcher.control.atomic(archive/'manifest.json',manifest)
    fingerprint='f'*64
    dist=tmp_path/'.local-data/site-builds'/fingerprint/'dist'
    for name in ('zh/index.html','index.html'):
        p=dist/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture')
    launcher.control.atomic(dist/'sapba-build.json',{'fingerprint':fingerprint})
    api={'archive':str(archive),'source_digest':manifest['digest']}
    site={'dist_path':str(dist),'fingerprint':fingerprint,'mode':'preview'}
    launcher.control.atomic(tmp_path/'.local-data/startup-api-current.json',api)
    launcher.control.atomic(tmp_path/'.local-data/site-builds/current.json',site)
    return manifest,archive,dist


def test_exact_dependencies_allow_only_recorded_approved_pair(tmp_path):
    manifest,archive,dist=approved_pair(tmp_path)
    result=launcher.approved_target(tmp_path,manifest)
    assert result['archive']==archive
    assert result['manifest']==manifest
    assert result['site_fingerprint']=='f'*64


@pytest.mark.parametrize('kind',['version','record','interpreter','settings'])
def test_changed_dependencies_are_explicit_and_never_rewritten(tmp_path,kind):
    manifest,archive,dist=approved_pair(tmp_path)
    requested=copy.deepcopy(manifest)
    if kind=='version':
        requested['dependencies']={'sdk-163/METADATA':'new','sdk-163/RECORD':'new'}
    else:requested['dependencies'][kind]='new-private-digest'
    original={p:p.read_bytes() for p in [archive/'manifest.json',tmp_path/'.local-data/startup-api-current.json',tmp_path/'.local-data/site-builds/current.json']}
    with pytest.raises(launcher.control.StartupBlocked,match='startup_approved_dependencies_changed') as error:
        launcher.approved_target(tmp_path,requested)
    assert error.value.reason=='installed_dependencies_differ_from_approved_release'
    assert error.value.changes['dependencies']
    assert 'new-private-digest' not in str(error.value.changes)
    assert all(p.read_bytes()==data for p,data in original.items())


def test_invalid_source_or_site_still_fails_closed(tmp_path):
    manifest,archive,dist=approved_pair(tmp_path)
    (dist/'sapba-build.json').write_text('{}')
    with pytest.raises(launcher.control.StartupBlocked,match='startup_approved_release_unavailable'):
        launcher.approved_target(tmp_path,manifest)
    (archive/'manifest.json').write_text('{}')
    with pytest.raises(launcher.control.StartupBlocked):
        launcher.approved_target(tmp_path,manifest)


def test_invalid_negative_cache_does_not_block_a_now_valid_workspace(orchestrator,monkeypatch):
    args,state,manifest,data=orchestrator
    launcher.control.atomic(data/'startup/deferred-update.json',{
        'key':launcher.deferral_key(args.root,manifest,'ui'),'approved_source_digest':'old'})
    def unavailable(*_):raise launcher.control.StartupBlocked('startup_approved_dependencies_changed')
    monkeypatch.setattr(launcher,'approved_target',unavailable)
    result=launcher.ensure(args)
    assert result['source_digest']=='target-source'
    assert state.launches==[str(args.root/'target-source')]


def test_rejected_workspace_and_changed_approved_dependencies_never_stop_services(orchestrator,monkeypatch):
    args,state,manifest,data=orchestrator
    def rejected(*_):raise launcher.control.StartupBlocked('workbuddy_sdk_integration_required')
    def unavailable(*_):raise launcher.control.StartupBlocked('startup_approved_dependencies_changed')
    monkeypatch.setattr(launcher,'check_admission',rejected)
    monkeypatch.setattr(launcher,'approved_target',unavailable)
    with pytest.raises(launcher.control.StartupBlocked,match='startup_approved_dependencies_changed'):
        launcher.ensure(args)
    assert not state.stopped and not state.launches
    assert not (data/'startup-maintenance.json').exists()
