from sap_business_agents_platform.rules import assess_api_evidence, classify_control_object


def test_complete_empty_api_result_does_not_trigger_adt() -> None:
    result = assess_api_evidence(
        {"checks": {"actual": {"ok": True, "source_complete": True, "results": []}}}
    )
    assert result["status"] == "complete"
    assert result["needs_adt"] == {"actual": False}


def test_schema_capability_gap_triggers_adt() -> None:
    result = assess_api_evidence(
        {
            "checks": {
                "plan": {
                    "ok": False,
                    "source_complete": False,
                    "error": {"code": "schema_drift_field_unavailable"},
                }
            }
        }
    )
    assert result["status"] == "fallback_required"
    assert result["needs_adt"] == {"plan": True}
    assert result["capability_gaps"] == ["plan"]


def test_timeout_truncation_and_auth_errors_do_not_trigger_adt() -> None:
    for code in ("sap_read_timeout", "sap_read_http_error", "source_truncated"):
        result = assess_api_evidence(
            {
                "checks": {
                    "actual": {
                        "ok": False,
                        "source_complete": False,
                        "error": {"code": code},
                    }
                }
            }
        )
        assert result["status"] == "inconclusive"
        assert result["needs_adt"] == {"actual": False}
        assert result["operational_gaps"] == ["actual"]


def test_trusted_manifest_can_declare_verified_api_capability_gap() -> None:
    result = assess_api_evidence(
        {
            "checks": {"actual": {"ok": True, "source_complete": True}},
            "capability_gaps": ["settlement_rule"],
        }
    )
    assert result["needs_adt"] == {"actual": False, "settlement_rule": True}
    assert result["capability_gaps"] == ["settlement_rule"]


def test_control_object_classifier_selects_exactly_one_adt_master_path() -> None:
    order = classify_control_object({"object_type": "INTERNAL_ORDER"})
    wbs = classify_control_object({"object_type": "WBS"})
    assert (order["is_internal_order"], order["is_wbs"]) == (True, False)
    assert (wbs["is_internal_order"], wbs["is_wbs"]) == (False, True)
