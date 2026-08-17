import json

from material_shortage_procurement_response import analyze
from material_shortage_procurement_response.cli import main
from material_shortage_procurement_response.fixture import demo_path


def test_demo_reports_shortage_and_pending_pr() -> None:
    result = analyze(json.loads(demo_path().read_text(encoding="utf-8")))
    assert result["status"] == "complete"
    assert result["source_complete"] is True
    assert {item["id"] for item in result["metrics"]} >= {"shortage_quantity", "pending_pr"}


def test_cli(capsys) -> None:
    assert main([]) == 0
    assert "material_shortage_procurement_response_deterministic_v1" in capsys.readouterr().out
