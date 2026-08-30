from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Iterator

from jsonschema import ValidationError, validate

from .codex_planner import (
    AUTHOR_OUTPUT_SCHEMA,
    PLANNER_OUTPUT_SCHEMA,
    SUMMARY_OUTPUT_SCHEMA,
    WORKFLOW_COMPOSITION_OUTPUT_SCHEMA,
    WORKFLOW_REPAIR_OUTPUT_SCHEMA,
    WORKFLOW_REVIEW_OUTPUT_SCHEMA,
    _decode_plan_json,
    _grounding_prompt,
    _planner_prompt,
    _safe_json,
    _workflow_composition_prompt,
)
from .models import PlannerDecision


class WorkBuddyRuntimeError(RuntimeError):
    def __init__(self, message: str, *, code: str = "workbuddy_runtime_error") -> None:
        super().__init__(message)
        self.code = code


class WorkBuddyPlanner:
    """CodeBuddy Agent SDK adapter with all built-in tools denied.

    WorkBuddy produces a bounded structured plan. SAPBusinessAgents validates that
    plan against live metadata and executes it through the embedded GET-only
    provider, so the SDK never receives SAP credentials or direct network access.
    """

    def __init__(
        self,
        repository_root: Path,
        model: str | None = None,
        *,
        request_timeout_ms: int = 120_000,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.model = model
        self.request_timeout_ms = request_timeout_ms
        self._active_clients: set[Any] = set()
        self._event_sink: ContextVar[
            Callable[[str, dict[str, Any]], None] | None
        ] = ContextVar("workbuddy_event_sink", default=None)

    @contextmanager
    def bind_events(
        self, sink: Callable[[str, dict[str, Any]], None]
    ) -> Iterator[None]:
        token = self._event_sink.set(sink)
        try:
            yield
        finally:
            self._event_sink.reset(token)

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        sink = self._event_sink.get()
        if sink is not None:
            sink(event_type, {"provider_id": "workbuddy", **data})

    async def plan(
        self,
        query: str,
        catalog: dict[str, Any],
        guidance: dict[str, Any],
        skills: list[dict[str, Any]],
        thread_id: str | None = None,
    ) -> PlannerDecision:
        prompt = _planner_prompt(
            query, catalog, guidance, skills, continuing=bool(thread_id)
        )
        raw, session_id = await self._structured_turn(
            prompt,
            PLANNER_OUTPUT_SCHEMA,
            thread_id=thread_id,
            system_prompt=(
                "You are a read-only SAP query planner. Do not call tools, execute shell "
                "commands, edit files, or invent SAP services. Return only JSON."
            ),
        )
        try:
            plan = _decode_plan_json(raw)
        except json.JSONDecodeError as exc:
            raw, session_id = await self._structured_turn(
                (
                    "The previous plan_json string was invalid JSON: "
                    f"{exc.msg} at character {exc.pos}. Re-emit the same plan and change "
                    "only JSON syntax. Return only the required JSON object."
                ),
                PLANNER_OUTPUT_SCHEMA,
                thread_id=session_id,
            )
            plan = _decode_plan_json(raw)
        return PlannerDecision(
            intent=str(raw.get("intent") or query),
            needs_clarification=bool(raw.get("needs_clarification")),
            clarification_question=str(raw.get("clarification_question") or ""),
            plan=plan,
            thread_id=session_id,
        )

    async def ground_plan(
        self,
        *,
        query: str,
        decision: PlannerDecision,
        schemas: list[dict[str, Any]],
        relationships: dict[str, Any] | None = None,
        validation_failures: list[dict[str, Any]] | None = None,
        repair_attempt: int = 0,
    ) -> PlannerDecision:
        if not decision.thread_id or not decision.plan:
            raise ValueError("A resumable WorkBuddy session and candidate plan are required.")
        raw, session_id = await self._structured_turn(
            _grounding_prompt(
                query,
                decision.plan,
                schemas,
                relationships or {},
                validation_failures or [],
                repair_attempt=repair_attempt,
            ),
            PLANNER_OUTPUT_SCHEMA,
            thread_id=decision.thread_id,
        )
        plan = _decode_plan_json(raw)
        return PlannerDecision(
            intent=str(raw.get("intent") or decision.intent or query),
            needs_clarification=bool(raw.get("needs_clarification")),
            clarification_question=str(raw.get("clarification_question") or ""),
            plan=plan,
            thread_id=session_id,
        )

    async def summarize(
        self,
        *,
        thread_id: str,
        query: str,
        plan: dict[str, Any],
        evidence: list[dict[str, Any]],
        rule_results: list[dict[str, Any]],
    ) -> dict[str, str]:
        prompt = f"""
Explain the validated read-only SAP evidence in concise Chinese and English.

Question: {query}
Executed plan: {_safe_json(plan, limit=20_000)}
Evidence: {_safe_json(evidence, limit=80_000)}
Deterministic rule results: {_safe_json(rule_results, limit=20_000)}

Never override a deterministic rule. State every evidence or completeness limit.
Do not call tools. Return only the required JSON object.
""".strip()
        raw, _session_id = await self._structured_turn(
            prompt, SUMMARY_OUTPUT_SCHEMA, thread_id=thread_id
        )
        return {"zh": str(raw["zh"]), "en": str(raw["en"])}

    async def author_draft(
        self,
        *,
        thread_id: str,
        query: str,
        plan: dict[str, Any],
        evidence: list[dict[str, Any]],
        completeness: dict[str, Any],
        correction: str,
    ) -> dict[str, Any]:
        prompt = f"""
Prepare bilingual Agent detail content and deterministic-rule review notes from this
completed read-only SAP query. Do not generate executable code or new tools.

Question: {query}
Validated plan: {_safe_json(plan, limit=30_000)}
Evidence shape: {_safe_json(evidence, limit=50_000)}
Completeness: {_safe_json(completeness, limit=5_000)}
User correction: {correction or 'None'}

Explain purpose, inputs, fixed steps, evidence provenance and limitations. Return JSON only.
""".strip()
        raw, _session_id = await self._structured_turn(
            prompt, AUTHOR_OUTPUT_SCHEMA, thread_id=thread_id
        )
        return {
            "content_zh": str(raw["content_zh"]),
            "content_en": str(raw["content_en"]),
            "rule_notes": [str(item) for item in raw["rule_notes"]],
        }

    async def compose_workflow(
        self,
        *,
        requirement: str,
        catalog: dict[str, Any],
        locale: str,
        thread_id: str | None = None,
        clarification_input: str | None = None,
        previous: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raw, session_id = await self._structured_turn(
            _workflow_composition_prompt(
                requirement=requirement,
                catalog=catalog,
                locale=locale,
                clarification_input=clarification_input,
                previous=previous or {},
            ),
            WORKFLOW_COMPOSITION_OUTPUT_SCHEMA,
            thread_id=thread_id,
            system_prompt=(
                "Compose deterministic read-only workflows only from the supplied Agent "
                "catalog. Do not call tools, inspect files, execute SAP, or edit files. "
                "Request only business-result and completeness output ports; omit input-context echoes unless a later stage consumes them."
            ),
        )
        proposal = json.loads(str(raw.get("proposal_json") or "{}"))
        if not isinstance(proposal, dict):
            raise WorkBuddyRuntimeError(
                "WorkBuddy workflow proposal is not a JSON object.",
                code="workbuddy_structured_output_invalid",
            )
        return {
            "needs_clarification": bool(raw.get("needs_clarification")),
            "clarification_question": str(raw.get("clarification_question") or ""),
            "proposal": proposal,
            "thread_id": session_id,
        }

    async def review_workflow(
        self,
        *,
        workflow: dict[str, Any],
        agent_contracts: list[dict[str, Any]],
        validation_input: dict[str, Any],
        review_contract: dict[str, Any],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        prompt = f"""
Review this strictly read-only deterministic workflow. Check graph intent, declared
ports, mappings and completeness propagation. Do not call tools or execute SAP.
Return verdict=block for ambiguous branches, implicit mode selection, incompatible
cardinality, missing mappings, unsafe operations, optional terminal outputs, missing conditional
onSkip outputs, or incomplete completeness propagation. Use issue codes
workflow_terminal_output_optional, workflow_conditional_skip_output_missing, and
workflow_completeness_propagation_missing for those contract failures. Return verdict=pass only
when no blocking issue remains.

Workflow: {_safe_json(workflow, limit=40_000)}
Pinned Agent contracts: {_safe_json(agent_contracts, limit=30_000)}
Validation input shape: {_safe_json(validation_input, limit=5_000)}
Authoritative platform review contract: {_safe_json(review_contract, limit=15_000)}

The authoritative platform review contract defines the only Agent output ports that a conditional
skip path must synthesize. Do not require unconsumed Agent execution-context outputs merely because
they are required by the full Agent output schema.
""".strip()
        raw, session_id = await self._structured_turn(
            prompt, WORKFLOW_REVIEW_OUTPUT_SCHEMA, thread_id=thread_id
        )
        return {
            "verdict": str(raw["verdict"]),
            "issues": list(raw["issues"]),
            "summary": dict(raw["summary"]),
            "thread_id": session_id,
        }

    async def repair_workflow(
        self,
        *,
        workflow: dict[str, Any],
        agent_contracts: list[dict[str, Any]],
        error: dict[str, Any],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        if not thread_id:
            raise ValueError("Workflow repair requires the existing WorkBuddy session.")
        prompt = f"""
Repair only the connections array of this read-only deterministic workflow.
Keep the same nodes, Agent IDs, versions, digests, schemas, tools and SAP operations.

Workflow: {_safe_json(workflow, limit=40_000)}
Pinned Agent contracts: {_safe_json(agent_contracts, limit=30_000)}
Sanitized validation error: {_safe_json(error, limit=10_000)}

Use only declared ports and approved transforms. Return JSON only.
""".strip()
        raw, session_id = await self._structured_turn(
            prompt, WORKFLOW_REPAIR_OUTPUT_SCHEMA, thread_id=thread_id
        )
        connections = json.loads(str(raw["connections_json"]))
        if not isinstance(connections, list):
            raise WorkBuddyRuntimeError(
                "WorkBuddy workflow repair did not return a connection list.",
                code="workbuddy_structured_output_invalid",
            )
        return {
            "reason": str(raw["reason"]),
            "connections": connections,
            "thread_id": session_id,
        }

    async def cancel(self, thread_id: str | None = None) -> None:
        del thread_id
        for client in list(self._active_clients):
            try:
                await client.interrupt()
            except Exception:
                pass

    async def _structured_turn(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        thread_id: str | None,
        system_prompt: str | None = None,
        allow_repair: bool = True,
    ) -> tuple[dict[str, Any], str]:
        schema_prompt = (
            prompt
            + "\n\nReturn exactly one JSON object matching this JSON Schema:\n"
            + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
            + "\nDo not use Markdown fences or add explanatory text."
        )
        text, session_id = await self._query(
            schema_prompt, thread_id=thread_id, system_prompt=system_prompt
        )
        try:
            raw = _parse_json_object(text)
            validate(instance=raw, schema=schema)
        except (ValueError, json.JSONDecodeError, ValidationError) as exc:
            if not allow_repair:
                raise WorkBuddyRuntimeError(
                    "WorkBuddy returned invalid structured output.",
                    code="workbuddy_structured_output_invalid",
                ) from exc
            return await self._structured_turn(
                (
                    "Your previous response failed JSON Schema validation. Re-emit the same "
                    "answer, changing only JSON syntax and required field shape."
                ),
                schema,
                thread_id=session_id,
                allow_repair=False,
            )
        return raw, session_id

    async def _query(
        self,
        prompt: str,
        *,
        thread_id: str | None,
        system_prompt: str | None,
    ) -> tuple[str, str]:
        try:
            from codebuddy_agent_sdk import (
                AssistantMessage,
                CodeBuddyAgentOptions,
                CodeBuddySDKClient,
                PermissionResultDeny,
                ResultMessage,
                TextBlock,
            )
        except ImportError as exc:
            raise WorkBuddyRuntimeError(
                "WorkBuddy Agent SDK is not installed.",
                code="workbuddy_sdk_not_installed",
            ) from exc

        async def deny_tool(tool_name: str, _input: dict[str, Any], _options: Any) -> Any:
            return PermissionResultDeny(
                message=f"Tool {tool_name} is not registered for this structured turn.",
                interrupt=False,
            )

        options = CodeBuddyAgentOptions(
            tools=[],
            allowed_tools=[],
            disallowed_tools=[
                "Bash",
                "Write",
                "Edit",
                "NotebookEdit",
                "WebFetch",
                "WebSearch",
                "Agent",
                "Skill",
            ],
            system_prompt=system_prompt,
            permission_mode="plan",
            resume=thread_id,
            max_turns=4,
            model=self.model,
            cwd=self.repository_root,
            setting_sources=[],
            can_use_tool=deny_tool,
            persist_session=True,
            request_timeout_ms=self.request_timeout_ms,
        )
        client = CodeBuddySDKClient(options=options)
        assistant_text: list[str] = []
        result_text = ""
        session_id = thread_id or ""
        self._active_clients.add(client)
        self._emit(
            "agent_runtime_turn_started",
            {"resumed": bool(thread_id), "tools_enabled": False},
        )
        try:
            async with client:
                await client.query(prompt)
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        self._emit(
                            "agent_runtime_response_received",
                            {"message_type": "assistant"},
                        )
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                assistant_text.append(block.text)
                    if isinstance(message, ResultMessage):
                        session_id = str(message.session_id or session_id)
                        if message.is_error:
                            raise WorkBuddyRuntimeError(
                                "; ".join(message.errors or [])
                                or message.result
                                or "WorkBuddy returned an execution error.",
                                code="workbuddy_execution_failed",
                            )
                        if message.structured_output is not None:
                            result_text = json.dumps(
                                message.structured_output, ensure_ascii=False
                            )
                        elif message.result:
                            result_text = str(message.result)
        except WorkBuddyRuntimeError:
            self._emit("agent_runtime_turn_failed", {})
            raise
        except Exception as exc:
            self._emit("agent_runtime_turn_failed", {})
            raise WorkBuddyRuntimeError(
                str(exc) or type(exc).__name__,
                code=str(getattr(exc, "code", "workbuddy_execution_failed")),
            ) from exc
        finally:
            self._active_clients.discard(client)
        final_text = result_text.strip() or "\n".join(assistant_text).strip()
        if not final_text or not session_id:
            self._emit("agent_runtime_turn_failed", {})
            raise WorkBuddyRuntimeError(
                "WorkBuddy did not return a final response and session id.",
                code="workbuddy_result_missing",
            )
        self._emit(
            "agent_runtime_turn_completed",
            {"session_id_present": True},
        )
        return final_text, session_id


def _parse_json_object(value: str) -> dict[str, Any]:
    text = value.strip()
    fence = chr(96) * 3
    if text.startswith(fence) and text.endswith(fence):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start < 0:
            raise
        payload, end = json.JSONDecoder().raw_decode(text[start:])
        if text[start + end :].strip():
            raise ValueError("Structured output contains trailing text.")
    if not isinstance(payload, dict):
        raise ValueError("Structured output is not an object.")
    return payload
