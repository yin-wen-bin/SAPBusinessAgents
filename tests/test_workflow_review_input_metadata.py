"""Offline regression: review gets availability, real inputs keep their original gates."""
import json

import pytest
from fastapi.testclient import TestClient

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.workflow_review_input import (
    INPUT_METADATA_CONTRACT, missing_input, validation_input_metadata,
)
from tests.test_workflow_factory import (
    WorkflowPlanner, BlockingWorkflowPlanner, WorkflowSapProvider,
    _settings, _p2p_ap_workflow, _wait,
)


@pytest.mark.parametrize('value', [None, '', '  ', [], {}, (), set()])
def test_existing_missing_input_semantics_are_preserved(value):
    assert missing_input(value)


@pytest.mark.parametrize('value', [False, 0, '0', ['4500000001']])
def test_false_zero_and_nonempty_values_are_supplied(value):
    assert not missing_input(value)


def test_metadata_distinguishes_pending_missing_and_literal_user_string():
    schema={'type':'object','properties':{'purchase_orders':{'type':'array','minItems':1,'maxItems':1}},'required':['purchase_orders']}
    pending=validation_input_metadata(schema,{'purchase_orders':[]},['purchase_orders'],auto_discover=True)
    assert pending['contract']==INPUT_METADATA_CONTRACT
    assert pending['fields']['purchase_orders']=={
        'availability':'pending_auto_discovery','required':True,
        'declared_schema_pointer':'/inputSchema/properties/purchase_orders'}
    missing=validation_input_metadata(schema,{},['purchase_orders'],auto_discover=False)
    assert missing['fields']['purchase_orders']['availability']=='missing'
    literal=validation_input_metadata(schema,{'purchase_orders':'<auto_discover>'},['purchase_orders'],auto_discover=True)
    assert literal['fields']['purchase_orders']['availability']=='provided'
    assert literal['fields']['purchase_orders']['supplied_shape']=={'json_type':'string','length':15}


def test_supplied_shapes_keep_cardinality_and_types_without_business_values():
    supplied={'purchase_orders':['4500000001','4500000002'],'scope':{'supplier':'PRIVATE_SUPPLIER','flag':False,'amount':123.45,'PRIVATE_OBJECT_KEY':0}}
    data=validation_input_metadata({'properties':{}},supplied,['purchase_orders'],auto_discover=False)
    serialized=json.dumps(data)
    assert all(value not in serialized for value in ['4500000001','4500000002','PRIVATE_SUPPLIER','123.45','PRIVATE_OBJECT_KEY'])
    shape=data['fields']['purchase_orders']['supplied_shape']
    assert shape['json_type']=='array' and shape['length']==2
    assert shape['items']==[{'json_type':'string','length':10}]*2
    assert data['fields']['scope']['supplied_shape']['value_types']==['boolean','integer','number','string']


def test_optional_empty_value_is_not_promised_as_a_discovery_sample():
    data=validation_input_metadata({'properties':{'optional':{'type':'string'}}},{'optional':''},[],auto_discover=True)
    assert data['fields']['optional']['availability']=='provided'
    assert data['fields']['optional']['supplied_shape']=={'json_type':'string','length':0}


def test_deep_business_object_is_not_expanded_into_review_context():
    value='PRIVATE_VALUE'
    for _ in range(1100):
        value={'PRIVATE_KEY':value}
    data=validation_input_metadata({'properties':{}},{'scope':value},[],auto_discover=False)
    serialized=json.dumps(data)
    assert 'PRIVATE_KEY' not in serialized and 'PRIVATE_VALUE' not in serialized
    assert len(serialized)<2500


class CaptureReview(WorkflowPlanner):
    def __init__(self, provider, *, block=False):
        self.provider=provider; self.block=block; self.inputs=[]

    async def review_workflow(self, *args, **kwargs):
        assert self.provider.execute_count==0, 'SAP must not execute before a passing review'
        metadata=kwargs['validation_input']
        assert metadata['contract']==INPUT_METADATA_CONTRACT
        assert metadata['contains_business_values'] is False
        self.inputs.append(metadata)
        if self.block:
            return await BlockingWorkflowPlanner().review_workflow(*args,**kwargs)
        return await super().review_workflow(*args,**kwargs)


def _draft(client):
    workflow=_p2p_ap_workflow()
    workflow['inputSchema']['properties']['purchase_orders']['maxItems']=1
    response=client.post('/api/authoring/workflows',json={'workflow':workflow})
    assert response.status_code==201,response.text
    return response.json()['draft_id']


def test_auto_discovery_runs_after_review_then_validates_real_array(tmp_path):
    provider=WorkflowSapProvider(); reviewer=CaptureReview(provider)
    with TestClient(create_app(_settings(tmp_path),planner=reviewer,embedded_provider=provider)) as client:
        draft=_draft(client)
        response=client.post(f'/api/authoring/workflows/{draft}/validate',json={'autoDiscover':True,'input':{'purchase_orders':[]}})
        assert response.status_code==202,response.text
        result=_wait(client,response.json()['validation_run_id'])
        assert result['status']=='inconclusive'
        assert reviewer.inputs[0]['fields']['purchase_orders']['availability']=='pending_auto_discovery'
        assert result['result']['node_results'][0]['input']['purchase_orders']==['4500000001']
        assert provider.execute_count>0


def test_manual_supplied_array_keeps_its_scope_and_redacts_values(tmp_path):
    provider=WorkflowSapProvider(); reviewer=CaptureReview(provider)
    with TestClient(create_app(_settings(tmp_path),planner=reviewer,embedded_provider=provider)) as client:
        draft=_draft(client)
        response=client.post(f'/api/authoring/workflows/{draft}/validate',json={'autoDiscover':False,
            'input':{'purchase_orders':['4500000002'],'as_of':'2026-08-17'}})
        assert response.status_code==202,response.text
        result=_wait(client,response.json()['validation_run_id'])
        assert result['result']['node_results'][0]['input']['purchase_orders']==['4500000002']
        assert '4500000002' not in json.dumps(reviewer.inputs)
        assert reviewer.inputs[0]['fields']['purchase_orders']['supplied_shape']['length']==1


@pytest.mark.parametrize('orders', ['<auto_discover>',['4500000001','4500000002']])
def test_invalid_manual_type_or_cardinality_is_still_rejected_without_sap(tmp_path,orders):
    provider=WorkflowSapProvider(); reviewer=CaptureReview(provider)
    with TestClient(create_app(_settings(tmp_path),planner=reviewer,embedded_provider=provider)) as client:
        draft=_draft(client)
        response=client.post(f'/api/authoring/workflows/{draft}/validate',json={'autoDiscover':False,
            'input':{'purchase_orders':orders,'as_of':'2026-08-17'}})
        assert response.status_code==409,response.text
        assert response.json()['detail']['code']=='agent_input_invalid'
        assert response.json()['detail']['detail']['constraint'] in {'type','max_items'}
        assert provider.execute_count==0
        assert client.get('/api/runs').json()==[]


def test_runtime_block_still_prevents_discovery_and_execution(tmp_path):
    provider=WorkflowSapProvider(); reviewer=CaptureReview(provider,block=True)
    with TestClient(create_app(_settings(tmp_path),planner=reviewer,embedded_provider=provider)) as client:
        draft=_draft(client)
        response=client.post(f'/api/authoring/workflows/{draft}/validate',json={'autoDiscover':True,'input':{}})
        assert response.status_code==409,response.text
        assert response.json()['detail']['code']=='workflow_runtime_review_blocked'
        assert provider.execute_count==0
        assert client.get('/api/runs').json()==[]
