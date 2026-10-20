"""Presentation-only output changes, exercised without any SDK or SAP call."""
import asyncio
import copy
import json
from pathlib import Path

import pytest

from sap_business_agents_platform.runtime_agent_authoring import feedback_prompt, decode_feedback
from sap_business_agents_platform.runtime_workflow_authoring import instructions, decode, OUTPUT_SCHEMA
from sap_business_agents_platform.runtime_prompts import BUSINESS_SUMMARY_INSTRUCTIONS, BUSINESS_SUMMARY_VERSION


def test_both_authoring_prompts_share_final_reply_policy_only(tmp_path):
    package = {"manifest": {"slug": "sample", "module": "MM", "version": "1"}, "files": {}}
    for intent in ("revise", "explain"):
        text = feedback_prompt(tmp_path, feedback="name", locale="zh", package=package, intent=intent)
        assert text.endswith(BUSINESS_SUMMARY_INSTRUCTIONS)
        assert "Do not make a separate" in text
        assert "investigation, editing, tests, context, tools or safety checks" in text
        if intent == "explain":
            assert "MUST choose reply or clarify" in text
    assert instructions().endswith(BUSINESS_SUMMARY_INSTRUCTIONS)
    assert set(OUTPUT_SCHEMA["properties"]) == {"action", "answer", "question", "workflow_json"}


def test_agent_decoder_adds_internal_version_without_model_schema_or_payload_changes():
    raw = {"action": "reply", "summary": {"zh": "业务解释", "en": "Business explanation"}, "clarification_json": ""}
    before = copy.deepcopy(raw)
    value = decode_feedback(raw, {}, "thread", explain_only=True)
    assert value["presentation_version"] == BUSINESS_SUMMARY_VERSION
    assert value["summary"] == raw["summary"] and raw == before
    assert "package" not in value and "edits" not in value
    with pytest.raises(ValueError, match="agent_explanation_write_rejected"):
        decode_feedback({**raw, "action": "revise_agent"}, {}, "thread", explain_only=True)


def test_workflow_schema_remains_strict_and_explanation_cannot_edit():
    raw = {"action": "explain", "answer": "Business explanation", "question": "", "workflow_json": "null"}
    assert decode(json.dumps(raw), intent="explain")["presentation_version"] == BUSINESS_SUMMARY_VERSION
    with pytest.raises(ValueError, match="workflow_explain_cannot_modify"):
        decode(json.dumps({**raw, "action": "revise", "workflow_json": "{}"}), intent="explain")
    from sap_business_agents_platform.runtime_contract import RuntimeContractError
    with pytest.raises(RuntimeContractError):
        decode(json.dumps({**raw, "presentation_version": BUSINESS_SUMMARY_VERSION}), intent="explain")


def test_agent_persists_version_only_when_returned_and_keeps_old_turns(tmp_path):
    from tests.test_agent_authoring import Runtime, _service, create, feedback_payload, send_and_wait
    service, _, _ = _service(tmp_path)
    draft = create(service)
    history = copy.deepcopy(draft["conversation"])
    class SummaryRuntime(Runtime):
        async def review_agent_feedback(self, **kwargs):
            return {**await super().review_agent_feedback(**kwargs), "presentation_version": BUSINESS_SUMMARY_VERSION}
    service.runtime = SummaryRuntime("reply")
    asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    saved = service.get_draft(draft["draft_id"])
    assert saved["conversation"][:-1] == history
    assert saved["conversation"][-1]["decision"]["presentation_version"] == BUSINESS_SUMMARY_VERSION
    assert saved["revision"] == draft["revision"]


def test_workflow_persists_version_and_preserves_saved_binding(tmp_path, monkeypatch):
    from tests.test_workflow_assistant import setup, payload, finished
    from sap_business_agents_platform.workflow_assistant import state
    service, author, draft = setup(tmp_path)
    old = copy.deepcopy(draft.workflow)
    original = author.author_workflow_v2
    async def reply(**kwargs):
        return {**await original(**kwargs), "presentation_version": BUSINESS_SUMMARY_VERSION}
    monkeypatch.setattr(author, "author_workflow_v2", reply)
    async def run():
        service.assistant.submit(draft.draft_id, payload())
        await finished(service.assistant)
    asyncio.run(run())
    current = service.get(draft.draft_id)
    assert state(current)["rounds"][-1]["presentation_version"] == BUSINESS_SUMMARY_VERSION
    assert current.workflow == old and current.revision == draft.revision
