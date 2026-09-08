import json
from datetime import date
from pathlib import Path

import pytest

from sd_o2c_shared import EvidenceError, SPECS, analyze, load_evidence, run_cli


SD_ROOT = Path(__file__).parents[2]


def fixture_for(slug: str) -> Path:
    package = slug.replace("-", "_")
    return SD_ROOT / slug / "src" / package / "fixtures" / "demo.json"


@pytest.mark.parametrize("slug", sorted(SPECS))
def test_every_sd_agent_has_a_runnable_json_cli(slug, capsys):
    result = run_cli(
        slug,
        fixture_for(slug),
        ["查询当前业务状态", "--source", "fixture", "--as-of", "2026-08-10", "--json"],
    )
    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["agent"] == slug
    assert payload["read_only"] is True
    assert payload["data_sources"] == ["fixture"]


def test_live_evidence_must_explicitly_be_read_only(tmp_path):
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps({"metadata": {"schema_version": "1.0", "read_only": False}, "records": []}),
        encoding="utf-8",
    )
    with pytest.raises(EvidenceError, match="read_only=true"):
        load_evidence(evidence, live=True)


def test_delay_score_is_auditable_and_capped():
    payload = {
        "metadata": {"schema_version": "1.0", "read_only": True},
        "records": [{
            "id": "D1",
            "requested_delivery_date": "2026-08-01",
            "goods_movement_complete": False,
            "business_block": "01",
            "credit_status": "B",
            "incompletion_status": "X",
        }],
    }
    report = analyze("delivery-delay-prediction", "哪些交货会延期", payload, as_of=date(2026, 8, 10))
    assert report["score"] == 80
    assert report["details"][0]["components"] == ["已逾期 +40", "业务冻结 +20", "信用风险 +10", "不完整状态 +10"]


def test_shortage_allocation_is_stable_and_never_exceeds_stock():
    payload = load_evidence(fixture_for("shortage-allocation-advisor"))
    report = analyze("shortage-allocation-advisor", "如何分配库存", payload, as_of=date(2026, 8, 10))
    assert [row["recommended_allocation"] for row in report["details"]] == ["10", "2"]
    assert report["details"][-1]["remaining_available"] == "0"


def test_missing_output_and_dispute_evidence_are_blocked():
    metadata = {"schema_version": "1.0", "read_only": True}
    output = analyze("billing-output-monitor", "哪些发票没发出", {"metadata": metadata, "records": []}, as_of=date(2026, 8, 10))
    dispute = analyze("billing-dispute-classification", "拒票原因", {"metadata": metadata, "records": []}, as_of=date(2026, 8, 10))
    assert output["status"] == "blocked"
    assert dispute["status"] == "blocked"
