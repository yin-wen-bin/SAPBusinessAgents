import csv
import json
from pathlib import Path

from grir_clearing.cli import main


FIXTURE = Path(__file__).parents[1] / "fixtures" / "grir_sample.json"


def test_cli_writes_machine_readable_json(tmp_path, capsys):
    output = tmp_path / "report.json"
    result = main([
        "--fixture", str(FIXTURE),
        "--as-of", "2026-07-22",
        "--format", "json",
        "--output", str(output),
    ])

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert result == 0
    assert payload["summary"]["exception_count"] == 6
    assert {item["po"]["key"]["po_number"] for item in payload["items"]} == {
        "4500000001", "4500000002", "4500000003",
        "4500000004", "4500000005", "4500000006",
    }
    assert "exceptions=6" in capsys.readouterr().out


def test_cli_writes_excel_friendly_csv(tmp_path):
    output = tmp_path / "report.csv"
    main([
        "--fixture", str(FIXTURE),
        "--as-of", "2026-07-22",
        "--format", "csv",
        "--output", str(output),
    ])

    with output.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 6
    assert set(rows[0]) >= {"primary_reason", "age_days", "responsibility", "recommendation"}
