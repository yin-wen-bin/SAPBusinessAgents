import json
from pathlib import Path

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


def test_manifest_localizes_and_defaults_shortage_definition_inputs() -> None:
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "agent.json").read_text(encoding="utf-8")
    )
    schema = manifest["execution"]["inputSchema"]
    properties = schema["properties"]

    assert properties["mrp_area"]["title"] == {"zh": "MRP 区域", "en": "MRP Area"}
    assert properties["shortage_profile"]["title"] == {
        "zh": "短缺参数文件",
        "en": "Shortage Profile",
    }
    assert properties["shortage_counter"]["title"] == {
        "zh": "短缺定义序号",
        "en": "Shortage Definition Counter",
    }
    assert properties["shortage_profile"]["default"] == "SAP000000001"
    assert properties["shortage_counter"]["default"] == "001"
    assert properties["shortage_profile"]["x-sapba-server-default"] is True
    assert properties["shortage_counter"]["x-sapba-server-default"] is True
    assert "SAP000000001" in properties["shortage_profile"]["description"]["zh"]
    assert "001" in properties["shortage_counter"]["description"]["zh"]
    assert {"shortage_profile", "shortage_counter"}.issubset(schema["required"])
    assert manifest["inputs"]["zh"][2:6] == [
        "MRP 区域",
        "采购组织",
        "短缺参数文件",
        "短缺定义序号",
    ]
