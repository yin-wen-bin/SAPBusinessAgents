import json
import subprocess

import pytest

from scripts import acceptance_source_anchors as anchors
from scripts import run_three_stage_campaign as campaign
from scripts.direct_ar_adt_snapshot import compile_query, parse_xml, read_metadata


def fixture_source():
    rows = [{"Document": "0001", "Amount": "123.45"}]
    manifest = {"source_id": "items", "schema_hash": "schema", "query_hash": "query",
                "rows_hash": anchors._hash_json(rows), "row_count": 1, "stable_order_by": ["Document"],
                "source_complete": True, "paging_complete": True}
    source = {**manifest, "source_snapshot_hash": manifest["rows_hash"]}
    return source, manifest, rows


def test_anchor_detects_value_change_even_when_count_and_business_key_unchanged():
    source, manifest, rows = fixture_source()
    anchors.validate_frozen_source(source, manifest, rows)
    rows[0]["Amount"] = "123.46"
    with pytest.raises(ValueError, match="digest_mismatch"):
        anchors.validate_frozen_source(source, manifest, rows)


@pytest.mark.parametrize("field", ["schema_hash", "query_hash", "row_count", "stable_order_by"])
def test_anchor_rejects_metadata_change(field):
    source, manifest, rows = fixture_source()
    source[field] = "changed"
    with pytest.raises(ValueError, match="metadata_mismatch"):
        anchors.validate_frozen_source(source, manifest, rows)


def test_partial_or_unsupported_source_cannot_be_silently_omitted(tmp_path, monkeypatch):
    monkeypatch.setattr(anchors, "direct_read", lambda *a, **kw: pytest.fail("no SAP calls allowed"))
    with pytest.raises(ValueError, match="method_not_supported"):
        anchors.capture({"sources": [{"access_method": "gui_export", "http_method": "POST"}]}, {}, tmp_path / "out")
    with pytest.raises(ValueError, match="incomplete"):
        anchors.anchor_value({"source_complete": False})


def test_anchor_summary_requires_matching_baseline_and_both_checks():
    baseline = {"sources": []}
    check = {"baseline_hash": anchors._hash_json(baseline), "observed": "one", "expected": "one", "verdict": "PASS"}
    assert anchors.summarize(baseline, check, check)["verdict"] == "PASS"
    assert anchors.summarize(baseline, check, {**check, "observed": "two"})["verdict"] == "CHANGED"
    assert anchors.summarize({"sources": [1]}, check, check)["verdict"] == "CHANGED"


def test_campaign_never_saves_unlabelled_child_output_as_plaintext(tmp_path, monkeypatch):
    from scripts import direct_sap_read
    captured = []
    monkeypatch.setattr(direct_sap_read, "write_encrypted_rows", lambda path, rows: captured.append((path, rows)))
    raw = 'unlabelled customer name and ref: SensitiveValue123'
    result = campaign._record_failure_log(tmp_path / "state.json", "case", subprocess.CompletedProcess([], 2, raw, ""))
    assert captured[0][1][0]["diagnostic"].startswith(raw)
    assert raw not in json.dumps(result)
    assert not list(tmp_path.rglob("*.log"))
    assert result["private_log_path"].endswith(".aesgcm")


@pytest.mark.parametrize("counts", [('totalRows="1" numberOfRows="2"', 'x'), ('totalRows="0"', 'x')])
def test_adt_conflicting_or_impossible_counts_are_not_complete(counts):
    attrs, value = counts
    xml = f'<table {attrs}><columns><metadata name="KEY"/><dataSet><data>{value}</data></dataSet></columns></table>'.encode()
    with pytest.raises(ValueError, match="count_unproven"):
        parse_xml(xml)


def test_adt_quoted_value_cannot_become_sql_and_full_key_required():
    fields = ["CompanyCode", "BankStatementShortID", "BankStatementItem"]
    spec = {"object": "I_ARBANKSTATEMENTITEM", "fields": fields, "row_limit": 5,
            "filters": [{"field": "CompanyCode", "operator": "=", "value": "x' OR '1'='1"}]}
    assert "'x'' OR ''1''=''1'" in compile_query(spec)
    with pytest.raises(ValueError, match="full_key"):
        compile_query({**spec, "fields": fields[:1]})


def test_direct_adt_metadata_uses_live_source_and_requires_stable_key(monkeypatch):
    class Response:
        status_code = 200
        content = b"define view I_ArBankStatementItem as select { CompanyCode, BankStatementShortID, BankStatementItem }"

    class Session:
        trust_env = True
        auth = None

        def get(self, *_args, **_kwargs):
            return Response()

        def close(self):
            return None

    monkeypatch.setattr("scripts.direct_ar_adt_snapshot.requests.Session", Session)
    payload, path = read_metadata(
        {"base_url": "https://sap.invalid", "username": "u", "password": "p"},
        "I_ARBANKSTATEMENTITEM",
    )
    assert b"BankStatementItem" in payload
    assert path.endswith("/i_arbankstatementitem/source/main")


def test_anchor_replays_adt_snapshot_with_frozen_spec(tmp_path, monkeypatch):
    rows = [{"MANDT": "100", "LAUFD": "20260901", "LAUFI": "A"}]
    rows_hash = anchors._hash_json(rows)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "manifest.json").write_text(
        json.dumps(
            {
                "source_id": "adt_mhnk",
                "schema_hash": "sha256:" + "a" * 64,
                "query_hash": "sha256:" + "b" * 64,
                "rows_hash": rows_hash,
                "row_count": 1,
                "stable_order_by": ["MANDT", "LAUFD", "LAUFI"],
                "source_complete": True,
                "paging_complete": True,
                "spec": {"object": "MHNK", "fields": ["MANDT"], "filters": [], "row_limit": 1},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(anchors, "read_encrypted_rows", lambda path: rows)
    monkeypatch.setattr(
        anchors,
        "direct_adt_read",
        lambda profile, spec, output: {
            "source_id": "adt_mhnk",
            "schema_hash": "sha256:" + "a" * 64,
            "query_hash": "sha256:" + "b" * 64,
            "rows_hash": rows_hash,
            "row_count": 1,
            "stable_order_by": ["MANDT", "LAUFD", "LAUFI"],
            "source_complete": True,
            "paging_complete": True,
        },
    )
    baseline = {
        "sources": [
            {
                "source_id": "adt_mhnk",
                "access_method": "adt_data_preview",
                "http_method": "POST",
                "schema_hash": "sha256:" + "a" * 64,
                "query_hash": "sha256:" + "b" * 64,
                "rows_hash": rows_hash,
                "row_count": 1,
                "stable_order_by": ["MANDT", "LAUFD", "LAUFI"],
                "source_complete": True,
                "paging_complete": True,
                "source_snapshot_ref": str(snapshot),
                "source_snapshot_hash": rows_hash,
            }
        ]
    }
    result = anchors.capture(baseline, {}, tmp_path / "anchor")
    assert result["verdict"] == "PASS"
