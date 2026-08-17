from __future__ import annotations

import pytest

from sap_business_agents_platform.engine import _when_matches
from sap_business_agents_platform.manifests import ManifestError, validate_execution


def _manifest(source: str = "{{steps.assess.output.needs_adt.stock}}") -> dict[str, object]:
    return {
        "execution": {
            "mode": "deterministic",
            "inputSchema": {"type": "object", "properties": {}},
            "steps": [
                {
                    "id": "assess",
                    "executor": "rule",
                    "operation": "assess_api_evidence",
                    "inputMapping": {"checks": {}},
                },
                {
                    "id": "adt",
                    "executor": "skill",
                    "operation": "execute",
                    "skillId": "sap-adt-table-export",
                    "readOnly": True,
                    "failurePolicy": "record_gap",
                    "when": {"source": source, "equals": True},
                    "inputMapping": {},
                },
            ],
        }
    }


def test_boolean_step_condition_true_and_false() -> None:
    context = {"steps": {"assess": {"output": {"needs_adt": {"stock": True}}}}}
    assert _when_matches({"source": "{{steps.assess.output.needs_adt.stock}}", "equals": True}, context)
    assert not _when_matches(
        {"source": "{{steps.assess.output.needs_adt.stock}}", "equals": False}, context
    )


def test_condition_rejects_non_boolean_and_non_prior_reference() -> None:
    with pytest.raises(ValueError, match="booleans"):
        _when_matches(
            {"source": "{{steps.assess.output.needs_adt.stock}}", "equals": True},
            {"steps": {"assess": {"output": {"needs_adt": {"stock": "yes"}}}}},
        )
    with pytest.raises(ManifestError, match="prior step"):
        validate_execution(_manifest("{{steps.adt.output.needs_adt.stock}}"))
