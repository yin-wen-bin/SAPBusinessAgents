"""General PO record proofs with owned live-schema fixtures; no SAP or SDK."""
import asyncio
import copy

import pytest

from tests.test_runtime_report_contract import make_broker, report


def setup(tmp_path, *, metadata=True, complete=True, authority=True, query="查询采购订单 4500000001"):
    broker, store, run, token = make_broker(tmp_path, query=query)
    plan = {"service_name":"API_PURCHASEORDER_PROCESS_SRV","odata_version":"2.0","entity_set":"A_PurchaseOrderItem",
            "select_fields":["PurchaseOrder","PurchaseOrderItem","OrderQuantity","PurchaseOrderQuantityUnit"],
            "filters":[{"field":"PurchaseOrder","operator":"eq","value":"4500000001"}]}
    if metadata:
        store.begin_harness_tool_call(call_id="schema",run_id=run,tool_name="sap_schema_get",request_hash="schema",safe_input={})
        store.complete_harness_tool_call("schema",status="completed",output={"ok":True,"data":{
            "schema_authority":authority,"fields_truncated":False,
            "entities":[{**{k:plan[k] for k in ("service_name","odata_version","entity_set")},
                         "key_fields":["PurchaseOrder","PurchaseOrderItem"],"runtime_available":True,"executable":True}],
            "fields":[{**{k:plan[k] for k in ("service_name","odata_version","entity_set")},
                       "field_name":field,"data_type":"Edm.String","runtime_available":True,"executable":True}
                      for field in plan["select_fields"]]}})
    rows=[{"PurchaseOrder":"4500000001","PurchaseOrderItem":"00010","OrderQuantity":"2.000","PurchaseOrderQuantityUnit":"EA"}]
    ref=broker._save_evidence(run,"sap_live",{"rows":rows,"source_complete":complete})
    store.begin_harness_tool_call(call_id="read",run_id=run,tool_name="sap_query_execute",request_hash="read",safe_input={"plan":plan})
    store.complete_harness_tool_call("read",status="completed",output={"ok":True,"source_complete":complete,
        "source_type":"sap_live","row_count":len(rows),"evidence_ref":ref},evidence_ref=ref)
    block={"type":"table","claim_scope":"customer_business_fact","evidence_refs":[ref],"row_set_binding":None,
           "columns":[{"key":field,"label":{"zh":field,"en":field},"format":"text"} for field in plan["select_fields"]],
           "rows":[{"values":[{"zh":row[field],"en":row[field]} for field in plan["select_fields"]],"evidence_refs":[ref],
                    "value_bindings":{field:{"kind":"source","evidence_ref":ref,
                        "source_key":{key:row[key] for key in ("PurchaseOrder","PurchaseOrderItem")},"source_field":field}
                        for field in plan["select_fields"]}} for row in rows]}
    return broker,store,run,token,block


def validate(broker,run,token,block):
    return asyncio.run(broker.handle(run,token,"sap_final_report_validate",{"report":report(schema_version="2.0",blocks=[block])}))


def test_literal_record_table_is_verified_with_owned_live_keys(tmp_path):
    broker,store,run,token,block=setup(tmp_path)
    result=validate(broker,run,token,block)
    assert result["ok"], result
    assert result["verification"]["business_facts"]["status"]=="verified"
    assert result["verification"]["coverage"]["status"]=="verified"
    assert result["verification"]["summary_amounts"]["status"]=="not_requested"


@pytest.mark.parametrize("change",["no_metadata","not_authoritative","incomplete","wrong_key","tampered_value","currency"])
def test_technical_proof_does_not_exempt_existing_gates(tmp_path,change):
    broker,store,run,token,block=setup(tmp_path,metadata=change!="no_metadata",authority=change!="not_authoritative",
                                     complete=change!="incomplete")
    if change=="wrong_key":block["rows"][0]["value_bindings"]["OrderQuantity"]["source_key"].pop("PurchaseOrderItem")
    if change=="tampered_value":block["rows"][0]["values"][2]={"zh":"999","en":"999"}
    if change=="currency":block["columns"][2]["format"]="currency"
    result=validate(broker,run,token,block)
    assert not result.get("ok") or any(d.get("status")=="needs_evidence" for d in result.get("verification",{}).values() if isinstance(d,dict)),result
    assert result.get("code")!="tool_execution_failed"


def test_technical_keys_do_not_establish_financial_classification(tmp_path):
    broker,store,run,token,block=setup(tmp_path)
    store.update_run(run,input_json={"answer_scope":{"categories":["posted_normal"]}})
    result=validate(broker,run,token,block)
    assert result["verification"]["coverage"]["status"]=="needs_evidence"
    assert any(i["code"]=="source_business_completeness_unproven" for i in result["verification"]["coverage"]["issues"])


@pytest.mark.parametrize("display,accepted",[("2017-10-08",True),("2017-10-09",False)])
def test_live_date_only_metadata_controls_exact_calendar_value(tmp_path,display,accepted):
    from sap_business_agents_platform.report_verification import resolve
    from sap_business_agents_platform.models import ValueBinding
    broker,store,run,token,block=setup(tmp_path)
    calls=store.list_harness_tool_calls(run)
    schema=next(c for c in calls if c["tool_name"]=="sap_schema_get")["output"]
    schema["data"]["fields"].append({**{k:schema["data"]["entities"][0][k] for k in ("service_name","odata_version","entity_set")},
        "field_name":"PurchaseOrderDate","data_type":"Edm.DateTime","display_format":"Date","runtime_available":True,"executable":True})
    store.complete_harness_tool_call("schema",status="completed",output=schema)
    plan=next(c for c in calls if c["tool_name"]=="sap_query_execute")["safe_input"]["plan"]
    plan["select_fields"].append("PurchaseOrderDate")
    record={"PurchaseOrder":"4500000001","PurchaseOrderItem":"00010","OrderQuantity":"2.000",
            "PurchaseOrderQuantityUnit":"EA","PurchaseOrderDate":"/Date(1507420800000)/"}
    ref=broker._save_evidence(run,"sap_live",{"rows":[record],"source_complete":True})
    store.begin_harness_tool_call(call_id="date-read",run_id=run,tool_name="sap_query_execute",request_hash="date",safe_input={"plan":plan})
    store.complete_harness_tool_call("date-read",status="completed",output={"ok":True,"source_type":"sap_live","source_complete":True,"row_count":1,"evidence_ref":ref},evidence_ref=ref)
    for row in block["rows"]:
        row["evidence_refs"]=[ref]
        for binding in row["value_bindings"].values():binding["evidence_ref"]=ref
        row["values"].append({"zh":display,"en":display})
        row["value_bindings"]["PurchaseOrderDate"]={"kind":"source","evidence_ref":ref,
            "source_key":{"PurchaseOrder":"4500000001","PurchaseOrderItem":"00010"},"source_field":"PurchaseOrderDate"}
    block["evidence_refs"]=[ref]
    block["columns"].append({"key":"PurchaseOrderDate","label":{"zh":"日期","en":"Date"},"format":"date"})
    result=validate(broker,run,token,block)
    assert bool(result.get("ok")) is accepted,result
