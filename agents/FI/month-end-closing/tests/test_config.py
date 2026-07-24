from pathlib import Path

import pytest

from sap_business_agents.month_end_closing import MonthEndClosingAssistant, load_checklist
from sap_business_agents.month_end_closing.gateway import FixtureSapGateway


ROOT = Path(__file__).parents[1]


def test_all_required_sap_modules_are_configured() -> None:
    checklist = load_checklist(ROOT / "config" / "month_end_checklist.toml")

    assert {item.module for item in checklist.checks} == {
        "FI-GL",
        "FI-AP",
        "FI-AR",
        "FI-AA",
        "CO",
        "MM",
        "SD",
    }
    assert {item.tcode for item in checklist.checks} == {
        "FBL1N",
        "FBL5N",
        "FBL3N",
        "MB5S",
        "MR11",
        "AFAB",
        "F.05",
        "F.13",
        "OB52",
        "MMPV",
        "KSB1",
        "VF03",
    }
    assert {"BKPF", "BSEG", "BSIK", "BSID", "BSIS", "EKBE", "ANLC", "ANEP", "COEP"} <= {
        table for item in checklist.checks for table in item.tables
    }


def test_unknown_handler_is_rejected_at_startup() -> None:
    checklist = load_checklist(ROOT / "config" / "month_end_checklist.toml")
    gateway = FixtureSapGateway.from_file(ROOT / "fixtures" / "1010_2026_07.json")

    with pytest.raises(ValueError, match="no checker registered"):
        MonthEndClosingAssistant(checklist, gateway, checkers={})

