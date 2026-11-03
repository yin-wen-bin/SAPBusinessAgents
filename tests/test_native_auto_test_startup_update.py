"""Register only exact, completed auto-test applications for the next startup."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from sap_business_agents_test_agent import startup_update


@pytest.fixture
def applied(tmp_path, monkeypatch):
    root=tmp_path/'repository';source=tmp_path/'task/source'
    for folder in (root,source):
        (folder/'scripts').mkdir(parents=True)
        (folder/'scripts/startup-update.py').write_text('# matching verified implementation')
    (root/'.local-data').mkdir();(root/'.local-data/startup-api-current.json').write_text('{}')
    store=SimpleNamespace(repository=root,task_dir=lambda identifier:source.parent)
    task={'id':'task-example','phase':'completed','round':2,'final_round':2,
          'verified_patch_digest':'patch','final_patch_digest':'patch','round_source_digest':'source',
          'application':{'status':'applied'},'results':{'2':{'case':{'status':'passed','mode':'offline','evidence_manifest':[]}}}}
    calls=[]
    module={'inputs':lambda folder:{'src/app.py':'same'},
            'register':lambda *args:calls.append(args) or {'status':'verified_pending_start'}}
    monkeypatch.setattr(startup_update.runpy,'run_path',lambda path:module)
    return store,task,source,module,calls


def test_completed_exact_application_publishes_proof_without_activation(applied):
    store,task,source,module,calls=applied
    assert startup_update.register_application(store,task)['status']=='verified_pending_start'
    assert len(calls)==1 and calls[0][2]['passed'] is True
    proof=calls[0][2]['evidence']
    assert len(proof)==1 and (store.repository/next(iter(proof))).is_file()


@pytest.mark.parametrize('failure',['phase','final','patch','regression','failed_case','source','implementation'])
def test_unverified_or_mixed_content_is_not_registered(applied,failure):
    store,task,source,module,calls=applied
    if failure=='phase':task['phase']='verifying'
    if failure=='final':task['final_round']=1
    if failure=='patch':task['verified_patch_digest']='different'
    if failure=='regression':task['regressions']=['case']
    if failure=='failed_case':task['results']['2']['case']['status']='failed'
    if failure=='source':module['inputs']=lambda folder:{'src/app.py':str(folder)}
    if failure=='implementation':(source/'scripts/startup-update.py').write_text('# different')
    assert startup_update.register_application(store,task)['status']=='blocked'
    assert calls==[]
