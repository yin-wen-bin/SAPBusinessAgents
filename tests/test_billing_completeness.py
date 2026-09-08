from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import validate

from sap_business_agents_platform.agent_rules import evaluate_business_agent
from sap_business_agents_platform.engine import _business_markdown_report, _default_presentation
from sap_business_agents_platform.manifests import AgentRepository, validator_schema
from sap_business_agents_platform.models import Completeness, RunMode, RunResult


ROOT = Path(__file__).resolve().parents[1]


def item(number="10", source_item="10", document="910001", source="810001"):
    return {
        "BillingDocument": document, "BillingDocumentItem": number,
        "ReferenceSDDocument": source, "ReferenceSDDocumentItem": source_item,
        "BillingQuantity": "2", "NetAmount": "100.00", "TransactionCurrency": "USD",
    }


def payload(items=None, headers=None, complete=True):
    # Synthetic evidence only; no SAP or local runtime calls.
    rows = {
        "billing_headers": headers if headers is not None else [{
            "BillingDocument": "910001", "BillingDocumentIsCancelled": False,
            "AccountingPostingStatus": "C",
        }],
        "billing_items": items if items is not None else [item(), item("11", "20")],
        "source_sales_items": [], "source_delivery_items": [],
    }
    return {
        "agent_id": "billing-completeness-check", "run_input": {"billing_document": "910001"},
        "evidence": {"collect_billing_completeness_evidence": {
            "ok": True, "source_complete": complete, "source_truncated": False,
            "step_results": {key: {"results": value, "source_complete": complete} for key, value in rows.items()},
        }},
    }


def evaluate(data):
    before = deepcopy(data)
    result = evaluate_business_agent(data)
    assert data == before, "Evaluation must not mutate stored evidence"
    return result


def codes(result):
    return {finding["code"] for finding in result["findings"]}


def records(result):
    return result["business_report"]["records"]


def test_different_items_of_one_source_document_are_not_duplicates():
    result = evaluate(payload())
    assert result["rule_id"] == "billing_completeness_check_deterministic_v2"
    assert result["business_status"] == "normal"
    assert result["findings"] == []
    assert result["source_complete"] is result["business_complete"] is True
    assert result["missing_evidence"] == []
    assert [row["business_status"] for row in records(result)] == ["normal", "normal"]
    assert {m["id"]: m["value"] for m in result["metrics"]} == {"billing_items": 2, "source_rows": 0, "finding_count": 0}


def test_only_repeated_source_items_need_review_not_unrelated_rows():
    result = evaluate(payload([item(), item("11", "10"), item("12", "20")]))
    assert result["business_status"] == "attention"
    assert codes(result) == {"DUPLICATE_REFERENCE"}
    assert [row["business_status"] for row in records(result)] == ["attention", "attention", "normal"]
    finding = result["findings"][0]
    assert finding["billing_document_items"] == ["10", "11"]
    assert finding["reference_document_item"] == "10"
    assert "810001/10" in finding["detail"]["zh"]
    assert "这不等于已确认重复开票" in finding["detail"]["zh"]
    assert "does not establish duplicate billing" in finding["detail"]["en"]
    assert records(result)[2]["finding_codes"] == []


def test_split_quantity_is_review_only_not_confirmed_duplicate_billing():
    rows = [item(), item("11", "10")]
    rows[0]["BillingQuantity"], rows[1]["BillingQuantity"] = "0.5", "1.5"
    result = evaluate(payload(rows))
    assert "项目拆分" in records(result)[0]["recommended_action"]["zh"]
    assert "待复核" in result["findings"][0]["detail"]["zh"]


def test_distinct_billing_documents_or_source_documents_are_not_conflated():
    rows = [item(), item("11", "10", source="810002"), item("10", "10", document="910002")]
    headers = [{"BillingDocument": d, "AccountingPostingStatus": "C", "BillingDocumentIsCancelled": False} for d in ("910001", "910002")]
    result = evaluate(payload(rows, headers))
    assert result["business_status"] == "normal"
    assert not result["findings"]


def test_identical_business_key_rows_and_transport_metadata_do_not_raise_alerts():
    rows = [item(), item()]
    rows[1]["__metadata"] = {"uri": "transport-only"}
    headers = payload()["evidence"]["collect_billing_completeness_evidence"]["step_results"]["billing_headers"]["results"]
    result = evaluate(payload(rows, headers + deepcopy(headers)))
    assert result["business_status"] == "normal"
    assert len(records(result)) == 1
    assert result["business_report"]["stages"][0]["evidence_count"] == 2


@pytest.mark.parametrize("field", ["ReferenceSDDocumentItem", "BillingQuantity", "NetAmount"])
def test_conflicting_item_business_keys_fail_closed_without_selecting_a_version(field):
    rows = [item(), item()]
    rows[1][field] = "99"
    result = evaluate(payload(rows))
    assert result["business_status"] == "inconclusive"
    assert not result["business_complete"]
    assert codes(result) == {"BILLING_ITEM_KEY_INVALID"}
    assert result["source_complete"] is True
    assert records(result)[0]["reference_document"] == ""
    assert records(result)[0]["business_status"] == "inconclusive"


def test_conflicting_header_evidence_is_not_reported_as_not_cancelled():
    headers = [
        {"BillingDocument": "910001", "BillingDocumentIsCancelled": False, "AccountingPostingStatus": "C"},
        {"BillingDocument": "910001", "BillingDocumentIsCancelled": True, "AccountingPostingStatus": "C"},
    ]
    result = evaluate(payload(headers=headers))
    assert result["business_status"] == "inconclusive"
    assert "BILLING_HEADER_CONFLICT" in codes(result)
    assert all(row["cancelled"] is None for row in records(result))


@pytest.mark.parametrize("field", ["ReferenceSDDocument", "ReferenceSDDocumentItem"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_source_keys_are_unknown_not_duplicate_references(field, value):
    rows = [item(), item("11", "20")]
    for row in rows:
        row[field] = value
    result = evaluate(payload(rows))
    assert result["business_status"] == "inconclusive"
    assert "DUPLICATE_REFERENCE" not in codes(result)
    assert "source_reference_incomplete" in result["missing_evidence"]


@pytest.mark.parametrize("field", ["BillingDocument", "BillingDocumentItem"])
def test_missing_billing_business_key_is_unknown(field):
    row = item()
    del row[field]
    result = evaluate(payload([row]))
    assert result["business_status"] == "inconclusive"
    assert "BILLING_ITEM_KEY_INVALID" in codes(result)
    assert len(records(result)) == 1


def test_missing_header_does_not_implicitly_confirm_cancellation_or_posting():
    result = evaluate(payload(headers=[]))
    assert result["business_status"] == "inconclusive"
    assert "BILLING_HEADER_MISSING" in codes(result)
    assert records(result)[0]["cancelled"] is None


@pytest.mark.parametrize("cancelled,posting,code", [
    (True, "C", "CANCELLED_BILLING"), (False, "A", "ACCOUNTING_NOT_POSTED"),
])
def test_existing_header_alerts_are_preserved_and_localized(cancelled, posting, code):
    result = evaluate(payload(headers=[{
        "BillingDocument": "910001", "BillingDocumentIsCancelled": cancelled,
        "AccountingPostingStatus": posting,
    }]))
    assert codes(result) == {code}
    assert result["business_status"] == "attention"
    assert all(row["business_status"] == "attention" for row in records(result))
    assert "910001" in result["findings"][0]["detail"]["zh"]


@pytest.mark.parametrize("failure", ["incomplete", "truncated", "next_page", "failed"])
def test_incomplete_source_never_reports_normal(failure):
    data = payload()
    source = data["evidence"]["collect_billing_completeness_evidence"]
    if failure == "incomplete":
        source["step_results"]["billing_items"]["source_complete"] = False
    elif failure == "truncated":
        source["source_truncated"] = True
    elif failure == "next_page":
        source["pagination"] = {"has_next": True}
    else:
        source["ok"] = False
    result = evaluate(data)
    assert result["source_complete"] is result["business_complete"] is False
    assert result["business_status"] == "inconclusive"
    assert all(row["business_status"] == "inconclusive" for row in records(result))


def test_complete_empty_items_are_an_explicit_review_not_a_duplicate():
    result = evaluate(payload(items=[]))
    assert result["business_status"] == "attention"
    assert codes(result) == {"NO_BILLING_ITEMS"}
    assert records(result) == []


@pytest.mark.parametrize("duplicate", [False, True])
def test_public_schema_bilingual_presentation_and_markdown_use_the_same_facts(duplicate):
    result = evaluate(payload([item(), item("11", "10" if duplicate else "20")]))
    manifest = AgentRepository(ROOT / "agents").get("billing-completeness-check")
    validate(result["workflow_output"], validator_schema(manifest["execution"]["outputSchema"]))
    run = RunResult(
        run_id="run-billing-fixture", mode=RunMode.agent, agent_id=manifest["slug"],
        rule_results=[result], summary=result["summary"],
        completeness=Completeness(source_complete=True, business_complete=True, reason="fixture"),
    )
    presentation = _default_presentation(run, output_schema=manifest["execution"]["outputSchema"])
    table = next(b for b in presentation.blocks if b.title and b.title.zh == "开票项目核对明细")
    assert [c.label.zh for c in table.columns] == [
        "开票凭证号", "开票项目", "来源凭证", "来源项目", "财务过账状态代码",
        "是否取消", "业务状态", "检查结论及原因", "建议动作",
    ]
    assert table.rows[0].values[6].zh == ("需要关注" if duplicate else "正常")
    assert table.rows[0].values[6].en == ("Attention required" if duplicate else "Normal")
    metrics = next(b for b in presentation.blocks if b.type == "metrics")
    assert [m.label.zh for m in metrics.metrics] == ["开票项目数", "来源证据条数", "需复核发现数"]
    markdown = _business_markdown_report(run, result["business_report"])
    assert "DUPLICATE_REFERENCE" not in markdown
    assert "检查结论及原因" in markdown
    if duplicate:
        findings = next(b for b in presentation.blocks if b.title and b.title.zh == "业务发现")
        assert "同一来源项目多次引用（待复核）" in findings.items[0].zh
        assert "Repeated source-item reference" in findings.items[0].en
        assert "810001/10" in markdown
    else:
        assert not any(b.title and b.title.zh == "业务发现" for b in presentation.blocks)
