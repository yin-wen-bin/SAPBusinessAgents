"""WorkBuddy workflow output normalization never repairs business bindings."""
import asyncio
import json

import pytest

from sap_business_agents_platform.authoring_workspace import AuthoringWorkspace
from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner
from sap_business_agents_platform.workbuddy_authoring import OUTPUT_SCHEMA


@pytest.mark.parametrize("form", ["plain", "fenced", "extra_field", "trailing", "missing_field", "nested_json", "enum", "non_object", "legacy_worker"])
def test_workflow_output_uses_shared_parser_and_safe_failure_diagnostics(tmp_path, monkeypatch, form):
    monkeypatch.setattr(AuthoringWorkspace, "prepare", lambda self, *_, **__: self.source.mkdir(parents=True))
    workflow = {"id": "saved", "version": "1.0.0", "nodes": [{"agentId": "fixed", "agentVersion": "0.1.0", "agentDigest": "immutable"}]}
    output = {"action": "revise", "answer": "Done", "question": "", "workflow_json": json.dumps(workflow)}
    if form == "extra_field":
        output["private-secret-value"] = "not-to-log"
    elif form == "missing_field":
        output.pop("workflow_json")
    elif form == "nested_json":
        output["workflow_json"] = "secret invalid JSON"
    elif form == "enum":
        output["action"] = "private-invalid-action"
    text = json.dumps(output)
    if form == "fenced":
        text = "```json\n" + text + "\n```"
    elif form == "trailing":
        text += '\n{"second":"private"}'
    elif form == "non_object":
        text = '"private-value"'
    events = []
    planner = WorkBuddyPlanner(tmp_path, "model", runtime_snapshot={"provider_id": "workbuddy"})
    async def execute(**kwargs):
        assert kwargs["operation"] == "workflow_authoring.v2" and kwargs["mode"] == "trusted_local"
        assert kwargs["payload"]["output_schema"] == OUTPUT_SCHEMA
        return {"text": text, **({} if form == "legacy_worker" else {"output_format": "native_json_schema"})}
    monkeypatch.setattr(planner.supervisor, "run", execute)
    async def run():
        return await planner.author_workflow_v2(workflow=workflow, message="Edit", intent="revise",
            execution_mode="full_access", history=[], catalog={}, references=[],
            emit=lambda kind, data: events.append((kind, data)), cleanup_state={})
    # Native structured terminals must be one JSON object. A Markdown fence
    # is now rejected by the shared contract, never stripped by the driver.
    if form == "plain":
        assert asyncio.run(run())["workflow"] == workflow
        assert not any(kind == "validation_failed" for kind, _ in events)
    else:
        with pytest.raises(WorkBuddyError) as failure:
            asyncio.run(run())
        assert failure.value.code == ("workbuddy_native_schema_unavailable" if form == "legacy_worker" else "workbuddy_structured_output_invalid")
        issue = next(data for kind, data in events if kind == "validation_failed")
        assert issue["constraint"] in {"json_object", "json_value", "additionalProperties", "required", "enum", "native_json_schema"}
        assert "secret" not in json.dumps(events) and "private" not in json.dumps(events)
