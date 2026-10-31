"""API through scheduler, original coordinator and report checkers; synthetic adapters only."""
import time
from fastapi.testclient import TestClient
import pytest
from tests.runtime_admission_fixture import fixture_app,QUERY


def wait(client,run_id,statuses):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        run=client.get('/api/runs/'+run_id).json()
        if run['status'] in statuses:return run
        time.sleep(.02)
    raise AssertionError('Offline query did not reach its expected platform state')


@pytest.mark.parametrize('provider',['codex','workbuddy'])
def test_real_api_submission_clarification_resume_and_persistence(tmp_path,provider):
    app=fixture_app(tmp_path,provider)
    with TestClient(app) as client:
        response=client.post('/api/runs',json={'mode':'free_query','query':QUERY})
        assert response.status_code==202,response.text
        assert 'Server-Timing' in response.headers
        assert len(response.headers['X-Runtime-Trace'])==32
        run_id=response.json()['run_id']
        waiting=wait(client,run_id,{'waiting_input','failed','inconclusive'})
        assert waiting['status']=='waiting_input',waiting.get('error')
        assert waiting['progress']['hard_limit_seconds']==1800
        control=waiting['error']['detail']['input_request_id']
        answer=client.post('/api/runs/'+run_id+'/input',json={'input':'公司代码1710','inputRequestId':control})
        assert answer.status_code==202,answer.text
        finished=wait(client,run_id,{'completed','failed','inconclusive'})
        assert finished['status']=='completed',finished.get('error')
        assert finished['result']['completeness']['source_complete'] is True
        assert finished['result']['presentation']['validation_ref']
        assert finished['result']['harness']['stop_reason']!='limit_reached'
        assert finished['progress']['elapsed_seconds']>=waiting['progress']['elapsed_seconds']
        assert app.state.store.get_run(run_id).result is not None
        assert app.state.offline_fixture['real_model_calls']==app.state.offline_fixture['real_SAP_calls']==0
