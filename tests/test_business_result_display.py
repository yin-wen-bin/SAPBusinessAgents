import copy
import json

import pytest

from sap_business_agents_platform.business_result_display import project, table_page, rule_record_summary
from sap_business_agents_platform.engine import _step_execution_envelope
from sap_business_agents_platform.models import RunPresentation


def fixture():
    status = {"type": "string", "title": {"zh": "状态", "en": "State"},
              "enum": ["open", "closed", "unknown"], "x-sapba-display": {"format": "enum", "labels": {
                  "open": {"zh": "未清", "en": "Open"}, "closed": {"zh": "已清", "en": "Cleared"},
                  "unknown": {"zh": "未知", "en": "Unknown"}}}}
    schemas = {"document": {"type": "string", "title": {"en": "Document"}}, "state": status}
    records = [{"document": str(i), "state": "open" if i % 2 else "closed"} for i in range(250)]
    records.append({"document": "unknown", "state": "unknown"})
    columns = [{"key": k, "label": s["title"]} for k, s in schemas.items()]
    report = {"records": records, "business_status": "open", "evidence_tables": [
        {"title": {"en": "Items"}, "columns": columns, "rows": records}], "metrics": [{"id": "count", "value": 251}]}
    schema = {"type": "object", "properties": {"records": {"type": "array", "items": {"type": "object", "properties": schemas}},
        "business_status": status, "totals": {"type": "array", "title": {"zh": "分币种金额", "en": "Amounts by currency"},
        "items": {"type": "object", "properties": {"currency": {"type": "string", "title": {"en": "Currency"}},
            "value": {"type": ["string", "null"], "title": {"en": "Signed amount"}},
            "hidden": {"type": "string", "x-sapba-display": {"visible": False}}}}}}}
    output = {"records": records, "business_status": "open", "business_report": report, "totals": [
        {"currency": "USD", "value": "-1.2300", "hidden": "DO_NOT_DISPLAY"}, {"currency": "EUR", "value": None}], "source_complete": True}
    result = {"workflow_output": output, "rule_results": [{"source_complete": True}, output],
        "steps": [{"step_id": "check", "executor": "rule", "status": "completed", "execution": {"record_count": 0, "evidence_status": "not_found"}},
                  {"step_id": "compute", "executor": "rule", "status": "completed", "execution": {"record_count": 0}}],
        "presentation": {"schema_version": "1.0", "title": {"zh": "结果", "en": "Result"}, "blocks": [
            {"type": "table", "title": {"zh": "Items", "en": "Items"}, "columns": columns, "rows": [], "total_rows": 251,
             "source_complete": True}]}}
    return result, schema


@pytest.mark.parametrize("provider", ["codex", "workbuddy", "other"])
def test_provider_independent_projection_is_read_only_and_valid(provider):
    result, schema = fixture(); result["runtime"] = {"provider_id": provider}
    before = copy.deepcopy(result)
    view = project(result, schema)
    assert result == before
    RunPresentation.model_validate(view["result"]["presentation"])
    assert [b["type"] for b in view["result"]["presentation"]["blocks"]] == ["table", "key_value", "table"]
    currency = view["result"]["presentation"]["blocks"][2]
    assert currency["rows"][0]["values"][1]["en"] == "-1.2300"
    assert currency["rows"][1]["values"][1]["en"] == "—"
    assert "DO_NOT_DISPLAY" not in json.dumps(currency)
    assert view["result"]["steps"][0]["execution"]["record_count"] is None
    assert view["result"]["steps"][0]["execution"]["evidence_status"] == "not_applicable"
    assert view["result"]["steps"][1]["execution"]["record_count"] == 251


def test_groups_page_full_records_and_never_drop_unknown_or_mix_currencies():
    result, schema = fixture(); view = project(result, schema)
    groups = view["result"]["business_display"]["groups"]["0"]
    assert sum(g["count"] for g in groups["choices"]) == 251
    assert {g["label"]["en"]:g["count"] for g in groups["choices"]} == {"Open": 125, "Cleared": 125, "Unknown": 1}
    page = table_page(view, 0, offset=120, limit=20, group='"open"')
    assert len(page["rows"]) == 5 and page["total_rows"] == 125
    assert all(r["values"][1]["en"] == "Open" for r in page["rows"])
    assert table_page(view, 0, offset=0, limit=20, group='"unknown"')["total_rows"] == 1
    assert table_page(view, 2, offset=1, limit=20)["rows"][0]["values"][0]["en"] == "EUR"
    for token in ('"not-declared"', '{}', 'null'):
        with pytest.raises(ValueError): table_page(view, 0, offset=0, limit=20, group=token)
    with pytest.raises(ValueError): table_page(view, 2, offset=0, limit=20, group='"USD"')


def test_empty_incomplete_hidden_and_ambiguous_tables_do_not_invent_success():
    result, schema = fixture(); result["workflow_output"]["source_complete"] = False
    result["workflow_output"]["business_complete"] = False
    result["workflow_output"]["totals"] = []
    result["presentation"]["blocks"][0]["source_complete"] = False
    view = project(result, schema)
    assert view["result"]["workflow_output"]["business_complete"] is False
    assert view["result"]["presentation"]["blocks"][2]["rows"] == []
    assert view["result"]["business_display"]["groups"]["0"]["source_complete"] is False
    # A lookalike table is not attached to another record source.
    result["presentation"]["blocks"][0]["title"] = {"en": "Unrelated"}
    assert not project(result, schema)["result"]["business_display"]["groups"]
    schema["properties"]["totals"]["x-sapba-display"] = {"visible": False}
    assert len(project(result,schema)["result"]["presentation"]["blocks"]) == 2


@pytest.mark.parametrize("output,status,count,evidence", [
    ({"source_complete": True}, "completed", None, "not_applicable"),
    ({"records": []}, "completed", 0, "not_found"),
    ({"records": [], "source_complete": False}, "completed", 0, "partial"),
    ({"records": [{"id": "1"}], "workflow_output": {"records": [{"id": "1"}]}}, "completed", 1, "available"),
    ({"workflow_output": {"records": [{"id":"1"}]}}, "completed", 1, "available"),
    ({"records": [{"id":"1"}]}, "failed", None, "unavailable"),
])
def test_rule_steps_are_not_source_queries(output, status, count, evidence):
    value = _step_execution_envelope({"executor": "rule"}, output, execution_status=status)
    assert value["record_count"] == count and value["evidence_status"] == evidence
    assert value["execution_status"] == status and value["preview_rows"] == []


def test_sap_query_envelope_unchanged_and_ambiguous_rule_history_is_not_guessed():
    value = _step_execution_envelope({"executor": "sap_read"}, {"source_complete":True,"data":{"results":[{"id":"1"}]}}, execution_status="completed")
    assert value["record_count"] == 1 and value["evidence_status"] == "available"
    result, schema = fixture(); result["rule_results"].append({"extra":True})
    assert project(result,schema)["result"]["steps"][0]["execution"]["record_count"] == 0


def test_get_and_group_paging_use_frozen_schema_without_mutating_saved_pass(tmp_path):
    import asyncio
    import httpx
    from pathlib import Path
    from sap_business_agents_platform.app import create_app
    from sap_business_agents_platform.config import Settings
    from sap_business_agents_platform.models import RunCreate, RunMode, RunResult, RunStatus
    async def scenario():
        settings = Settings(repository_root=Path(__file__).parents[1], data_root=tmp_path/'data')
        app = create_app(settings); store = app.state.store
        result,schema = fixture(); rid = 'display-only'
        store.create_run(rid,RunCreate(mode=RunMode.agent,agentId='synthetic-only'))
        store.save_agent_run_snapshot(rid, {'slug':'synthetic-only','version':'0.1.0','execution':{'mode':'deterministic','outputSchema':schema}})
        store.update_run(rid,status=RunStatus.completed,result_json=RunResult(run_id=rid,mode=RunMode.agent,**result))
        before = store.get_run(rid).model_dump(mode='json')
        app.state.agents.get = lambda *a: (_ for _ in ()).throw(AssertionError('must_not_read_new_catalog'))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8765') as client:
            response=await client.get('/api/runs/'+rid);assert response.status_code==200,response.text
            view=response.json()['result'];assert len(view['presentation']['blocks'])==3
            assert view['steps'][1]['execution']['record_count']==251
            response=await client.get('/api/runs/'+rid+'/presentation/blocks/0/rows',params={'group':'"closed"','offset':120,'limit':20})
            assert response.status_code==200,response.text
            assert response.json()['total_rows']==125 and len(response.json()['rows'])==5
            response=await client.get('/api/runs/'+rid+'/presentation/blocks/2/rows')
            assert response.status_code==200 and response.json()['total_rows']==2
            response=await client.get('/api/runs/'+rid+'/presentation/blocks/0/rows',params={'group':'"forged"'})
            assert response.status_code==404
            # Malformed historical display metadata must remain readable, with
            # an explicit projection error, and cannot manufacture a new PASS.
            store.create_run('invalid-view',RunCreate(mode=RunMode.agent,agentId='legacy'))
            bad=copy.deepcopy(result);bad['workflow_output']['business_report']['evidence_tables']=17
            store.update_run('invalid-view',status=RunStatus.completed,result_json=RunResult(run_id='invalid-view',mode=RunMode.agent,**bad))
            store.save_agent_run_snapshot('invalid-view',{'slug':'legacy','execution':{'mode':'deterministic','outputSchema':schema}})
            response=await client.get('/api/runs/invalid-view')
            assert response.status_code==200
            assert response.json()['result']['business_display']['status']=='unavailable'
            store.create_run('legacy',RunCreate(mode=RunMode.agent,agentId='legacy'))
            store.update_run('legacy',status=RunStatus.completed,result_json=RunResult(run_id='legacy',mode=RunMode.agent,**result))
            legacy=(await client.get('/api/runs/legacy')).json()['result']
            assert 'business_display' not in legacy
        assert store.get_run(rid).model_dump(mode='json')==before
    asyncio.run(scenario())
