import pytest
from sap_business_agents_platform.runtime_stage import role_stage, bind_stage_events, role_observation_seconds
from sap_business_agents_platform.runtime_contract import current_deadline


def test_stage_deadline_does_not_renew_and_other_budgets_unchanged(monkeypatch):
    import sap_business_agents_platform.runtime_stage as module
    from sap_business_agents_platform.runtime_policy import operation_seconds
    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    events = []
    with bind_stage_events(lambda kind, data: events.append((kind, data))):
        with role_stage("role_understanding", schema={}, operation="analyze_role_matching"):
            assert current_deadline() == 3700
            now[0] += 120
            assert current_deadline() == 3700
        with pytest.raises(TimeoutError) as error:
            with role_stage("role_catalog_page", schema={}, operation="review_role_matching_feedback", page_index=2):
                now[0] += 3600
                raise TimeoutError()
    assert error.value.runtime_diagnostic["failure_category"] == "timeout"
    assert error.value.runtime_diagnostic["elapsed_ms"] == 3600000
    assert events[-1][1]["phase"] == "role_catalog_page"
    assert current_deadline() is None
    assert role_observation_seconds(8) > 10 * 3600
    assert operation_seconds("codex", "review_workflow") == 180
    assert operation_seconds("workbuddy", "free_query") == 1800
