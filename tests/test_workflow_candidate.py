"""Candidate gates do not normalize or mutate historic workflow definitions."""
from copy import deepcopy
import pytest

from sap_business_agents_platform.workflow_candidate import check_candidate, checked_scope, change_paths
from sap_business_agents_platform.runtime_contract import RuntimeContractError
from sap_business_agents_platform.workflow_assistant import fingerprint


def source():
    return {"schemaVersion": 2, "title": {"zh": "旧", "en": "Old"},
            "nodes": [{"id": "a", "position": {"x": 1}}, {"id": "b", "position": {"x": 2}}]}


def test_title_revision_and_historical_extension_compatibility():
    old = {**source(), "legacyExtension": {"unchanged": True}, "validation": {"valid": True}}
    new = deepcopy(old)
    new["title"]["zh"] = "新"
    check_candidate(old, new, allowed_paths=["/title"])
    for field in ("references", "history", "intent", "publishedAt", "unknown"):
        invalid = {**new, field: []}
        with pytest.raises(RuntimeContractError, match="workflow_candidate_contract_invalid") as error:
            check_candidate(old, invalid)
        assert error.value.detail["validation_issues"][0]["path"] == "/" + field
    for field in ("legacyExtension", "validation"):
        invalid = deepcopy(new)
        del invalid[field]
        with pytest.raises(RuntimeContractError):
            check_candidate(old, invalid)


def test_array_leaf_scope_not_coarse_display_diff():
    old = source()
    new = deepcopy(old)
    new["nodes"][0]["position"]["x"] = 100
    assert change_paths(old, new) == [("nodes", "0", "position", "x")]
    check_candidate(old, new, allowed_paths=["/nodes/0"])
    new["nodes"][1]["position"]["x"] = 200
    with pytest.raises(RuntimeContractError) as error:
        check_candidate(old, new, allowed_paths=["/nodes/0"])
    assert error.value.detail["validation_issues"] == [{"path": "/nodes/1/position/x", "constraint": "outside_allowed_paths"}]
    with pytest.raises(RuntimeContractError):
        check_candidate(old, {**old, "nodes": old["nodes"][:1]}, allowed_paths=["/nodes/0"])


@pytest.mark.parametrize("path", ["/", "", "title", "/nodes/*", "/title~2", "/titleExtra", "/validation", "/id"])
def test_scope_is_verified_reference_not_model_authority(path):
    with pytest.raises(RuntimeContractError):
        checked_scope([path], [{"kind": "workflow_field", "path": "/title"}], intent="revise")


def test_legacy_fingerprint_and_new_scope_binding():
    old = {"requestId": "r", "feedback": "rename"}
    assert fingerprint(old) == fingerprint({**old, "allowedChangePaths": None})
    assert fingerprint(old) != fingerprint({**old, "allowedChangePaths": ["/title"]})
    assert checked_scope(None, [], intent="explain") is None
    with pytest.raises(RuntimeContractError):
        checked_scope(["/title"], [{"kind": "workflow_field", "path": "/title"}], intent="explain")
