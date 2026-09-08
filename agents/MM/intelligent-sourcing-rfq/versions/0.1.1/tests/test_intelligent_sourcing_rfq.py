import json

from intelligent_sourcing_rfq import analyze
from intelligent_sourcing_rfq.cli import main
from intelligent_sourcing_rfq.fixture import demo_path


def test_demo_ranks_comparable_quotations() -> None:
    result = analyze(json.loads(demo_path().read_text(encoding="utf-8")))
    assert result["status"] == "complete"
    metrics = {item["id"]: item["value"] for item in result["metrics"]}
    assert metrics["ranked_quotations"] == 2
    assert metrics["ranking"][0]["supplier"] == "100001"


def test_cli(capsys) -> None:
    assert main([]) == 0
    assert "intelligent_sourcing_rfq_deterministic_v1" in capsys.readouterr().out
