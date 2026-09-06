from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .config import Settings
from .database import RunStore
from .manifests import ManifestError, validate_execution
from .models import DraftRecord, RunMode, RunStatus, utc_now


class DraftError(RuntimeError):
    pass


class AgentDraftService:
    def __init__(self, settings: Settings, store: RunStore, author: Any = None) -> None:
        self.settings = settings
        self.store = store
        self.author = author
        self._creation_locks: dict[str, asyncio.Lock] = {}

    async def create_from_run(
        self,
        run_id: str,
        correction: str = "",
        *,
        origin: dict[str, Any] | None = None,
        execution_plan: dict[str, Any] | None = None,
    ) -> DraftRecord:
        key = _content_digest({"run_id": run_id, "correction": correction, "origin": origin or {}, "plan": execution_plan})
        async with self._creation_locks.setdefault(key, asyncio.Lock()):
            draft_id, claimed = self.store.reserve_draft_creation(key, f"draft_{uuid.uuid4().hex[:12]}")
            if not claimed:
                try:
                    return self.store.get_draft(draft_id)
                except KeyError as exc:
                    raise DraftError("Draft generation is already pending; retry the same request later.") from exc
            try:
                return await self._create_from_run(run_id, correction, origin=origin, execution_plan=execution_plan, draft_id=draft_id)
            except Exception:
                if not (self.settings.draft_root / draft_id).exists():
                    self.store.release_draft_creation(key, draft_id)
                raise

    async def _create_from_run(
        self, run_id: str, correction: str, *, origin: dict[str, Any] | None,
        execution_plan: dict[str, Any] | None, draft_id: str,
    ) -> DraftRecord:
        run = self.store.get_run(run_id)
        if run.mode != RunMode.free_query:
            raise DraftError("Only a free_query run can become an Agent draft.")
        if run.status not in {RunStatus.completed, RunStatus.inconclusive} or not run.result or not run.plan:
            raise DraftError("The free query must finish with a validated plan before drafting an Agent.")
        slug = f"free-query-{draft_id[-8:]}"
        draft_dir = (self.settings.draft_root / draft_id).resolve()
        if self.settings.draft_root.resolve() not in draft_dir.parents:
            raise DraftError("Draft path escaped the configured draft root.")
        query = str(run.query or "SAP free query")
        draft_plan = json.loads(json.dumps(execution_plan or run.plan))
        manifest = _manifest_from_run(slug, query, draft_plan, correction)
        origin = json.loads(json.dumps(origin or {}))
        if origin:
            if origin.get("workflow_draft_id") or origin.get("gap_id"):
                manifest.setdefault("authoring", {})["workflowGap"] = {
                    "workflowDraftId": origin.get("workflow_draft_id"),
                    "gapId": origin.get("gap_id"),
                }
            if isinstance(origin.get("free_query_session"), dict):
                manifest.setdefault("authoring", {})["freeQuerySession"] = json.loads(
                    json.dumps(origin["free_query_session"])
                )
        authored = {
            "content_zh": "只读 Agent 草稿。发布前必须复核业务语义、规则与证据完整性。",
            "content_en": "Read-only Agent draft. Review semantics, rules, and evidence completeness before publishing.",
            "rule_notes": ["Business owner must define deterministic completion criteria."],
        }
        author_draft = getattr(self.author, "author_draft", None)
        supports = getattr(self.author, "supports", None)
        authoring_supported = not callable(supports) or bool(supports("author_draft"))
        if callable(author_draft) and authoring_supported and run.thread_id:
            try:
                pin = getattr(self.author, "pin", None)
                provider_id = run.runtime.provider_id if run.runtime else "codex"
                model_id = run.runtime.model if run.runtime else None
                context = pin(provider_id, model_id) if callable(pin) else nullcontext()
                with context:
                    authored = await author_draft(
                        thread_id=run.thread_id,
                        query=query,
                        plan=draft_plan,
                        evidence=run.result.evidence,
                        completeness=run.result.completeness.model_dump(mode="json"),
                        correction=correction,
                    )
            except Exception as exc:
                raise DraftError(
                    "The selected Agent Runtime could not author the bilingual draft content; "
                    "no draft files were written."
                ) from exc
        draft_dir.mkdir(parents=True, exist_ok=False)
        (draft_dir / "agent.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (draft_dir / "README.md").write_text(
            _readme(slug, query, correction), encoding="utf-8"
        )
        (draft_dir / "content.zh.md").write_text(
            f"# {query[:80]}\n\n{authored['content_zh'].strip()}\n",
            encoding="utf-8",
        )
        (draft_dir / "content.en.md").write_text(
            f"# {query[:80]}\n\n{authored['content_en'].strip()}\n",
            encoding="utf-8",
        )
        fixture_dir = draft_dir / "fixtures"
        fixture_dir.mkdir()
        (fixture_dir / "validated-run.json").write_text(
            json.dumps(
                {
                    "source_run_id": run_id,
                    "query": run.query,
                    "plan": draft_plan,
                    "completeness": run.result.completeness.model_dump(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        test_dir = draft_dir / "tests"
        test_dir.mkdir()
        (test_dir / "test_manifest_contract.py").write_text(
            """import json\nfrom pathlib import Path\n\n\ndef test_generated_manifest_is_read_only():\n    manifest = json.loads((Path(__file__).parents[1] / 'agent.json').read_text(encoding='utf-8'))\n    assert manifest['schemaVersion'] == 2\n    assert all(step.get('readOnly', True) for step in manifest['execution']['steps'])\n""",
            encoding="utf-8",
        )
        source_dir = draft_dir / "src"
        source_dir.mkdir()
        (source_dir / "rules.py").write_text(
            """\"\"\"Business-rule skeleton generated by Agent Factory.\n\nReplace this placeholder only with deterministic, reviewable rules.\n\"\"\"\n\n\ndef evaluate(evidence: dict) -> dict:\n    return {\n        \"status\": \"inconclusive\",\n        \"reason\": \"Business rule requires owner review.\",\n        \"evidence_count\": len(evidence),\n    }\n""",
            encoding="utf-8",
        )
        (source_dir / "rule-review-notes.json").write_text(
            json.dumps(authored["rule_notes"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        docs_dir = draft_dir / "docs"
        docs_dir.mkdir()
        (docs_dir / "data-contract.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "input_schema": manifest["execution"]["inputSchema"],
                    "evidence_sources": [
                        {
                            "step_id": step["id"],
                            "executor": step["executor"],
                            "read_only": step.get("readOnly", True),
                        }
                        for step in manifest["execution"]["steps"]
                        if step["executor"] != "rule"
                    ],
                    "completeness_policy": "Never infer source_complete=true without an explicit source assertion.",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if origin.get("gap_contract"):
            (docs_dir / "gap-contract.json").write_text(
                json.dumps(origin["gap_contract"], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        draft = DraftRecord(
            draft_id=draft_id,
            run_id=run_id,
            status="generated",
            path=str(draft_dir),
            origin=origin,
            created_at=utc_now(),
        )
        self.store.save_draft(draft)
        return self.validate(draft_id)

    async def create_from_session(self, session_id: str) -> DraftRecord:
        async with self._creation_locks.setdefault(f"session:{session_id}", asyncio.Lock()):
            return await self._create_from_session(session_id)

    async def _create_from_session(self, session_id: str) -> DraftRecord:
        session = self.store.get_free_query_session(session_id)
        if session.get("status") == "draft_created" and session.get("draft_id"):
            existing = self.store.get_draft(session["draft_id"])
            source = existing.origin.get("free_query_session") or {}
            if source.get("accepted_iteration") == session.get("accepted_iteration") and source.get("result_digest") == session.get("accepted_result_digest"):
                return existing
        if session.get("status") != "satisfied" or not session.get("accepted_iteration"):
            raise DraftError("The latest free-query result must be accepted before drafting an Agent.")
        iteration = self.store.get_free_query_iteration(
            session_id, int(session["accepted_iteration"])
        )
        if iteration.get("result_digest") != session.get("accepted_result_digest"):
            raise DraftError("The accepted free-query result digest no longer matches.")
        run = self.store.get_run(iteration["run_id"])
        if run.result is None:
            raise DraftError("The accepted free-query result is unavailable.")
        execution_run = self._resolve_session_execution_run(session_id, iteration)
        execution_plan = self._replay_plan(execution_run)
        feedback_history = [
            {
                "iteration": item["iteration"],
                "feedback_type": item.get("feedback_type"),
                "execution_action": item.get("execution_action"),
                "feedback": item.get("feedback"),
                "decision": item.get("decision"),
            }
            for item in self.store.list_free_query_iterations(session_id)
            if item.get("feedback")
        ]
        origin = {
            "free_query_session": {
                "source_session_id": session_id,
                "accepted_iteration": session["accepted_iteration"],
                "result_digest": session["accepted_result_digest"],
                "feedback_digest": _content_digest(feedback_history),
                "feedback_trace": [
                    {
                        "iteration": item["iteration"],
                        "feedback_type": item.get("feedback_type"),
                        "execution_action": item.get("execution_action"),
                    }
                    for item in feedback_history
                ],
                "plan_digest": _content_digest(execution_plan),
                "accepted_iteration_plan_digest": iteration.get("plan_digest"),
                "execution_source_run_id": execution_run.run_id,
                "runtime": session.get("runtime"),
                "thread_id": session.get("thread_id"),
                "expectation_statuses": [
                    expectation.get("status")
                    for item in feedback_history
                    for expectation in (item.get("decision") or {}).get("candidate_expectations", [])
                ],
                "evidence_gaps": run.result.completeness.missing_evidence,
                "source_complete": run.result.completeness.source_complete,
                "business_complete": run.result.completeness.business_complete,
                "requires_parameterization_review": True,
            }
        }
        correction = "\n".join(
            f"Iteration {item['iteration']}: {item['feedback']}"
            for item in feedback_history
        )
        draft = await self.create_from_run(
            iteration["run_id"],
            correction,
            origin=origin,
            execution_plan=execution_plan,
        )
        _parameterize_session_draft(Path(draft.path), execution_plan)
        draft = self.validate(draft.draft_id)
        self.store.update_free_query_session(
            session_id, status="draft_created", draft_id=draft.draft_id
        )
        return draft

    def _resolve_session_execution_run(
        self, session_id: str, iteration: dict[str, Any]
    ) -> Any:
        """Follow presentation-only lineage to the latest real query run."""

        current = iteration
        visited: set[str] = set()
        while str(current.get("execution_action") or "") == "reinterpret":
            source_run_id = str(current.get("source_run_id") or "")
            if not source_run_id or source_run_id in visited:
                raise DraftError("The accepted result has invalid evidence-reuse lineage.")
            visited.add(source_run_id)
            source_iteration = self.store.get_free_query_iteration_by_run(source_run_id)
            if source_iteration is None or source_iteration.get("session_id") != session_id:
                raise DraftError("The accepted result references evidence outside its session.")
            current = source_iteration
        source_run = self.store.get_run(str(current.get("run_id") or ""))
        if (
            source_run.status not in {RunStatus.completed, RunStatus.inconclusive}
            or source_run.result is None
            or source_run.plan is None
        ):
            raise DraftError("The final SAP query plan is unavailable for Agent generation.")
        return source_run

    def _replay_plan(self, run: Any) -> dict[str, Any]:
        """Restore full validated requests from run-scoped harness call records."""

        plan = json.loads(json.dumps(run.plan or {}))
        if plan.get("kind") != "sap_business_agents_harness":
            return plan
        evidence_refs = {
            str(item.get("evidence_ref") or "")
            for item in (run.result.evidence if run.result else [])
            if isinstance(item, dict) and item.get("evidence_ref")
        }
        steps: list[dict[str, Any]] = []
        for call in self.store.list_harness_tool_calls(run.run_id):
            if call.get("status") != "completed":
                continue
            evidence_ref = str(call.get("evidence_ref") or "")
            if evidence_refs and evidence_ref not in evidence_refs:
                continue
            safe_input = call.get("safe_input")
            if not isinstance(safe_input, dict):
                continue
            if call.get("tool_name") == "sap_query_execute":
                query_plan = safe_input.get("plan")
                if not isinstance(query_plan, dict) or _contains_write_operation(query_plan):
                    continue
                steps.append(
                    {
                        "id": f"sap_query_{len(steps) + 1}",
                        "tool": "sap_read",
                        "plan": json.loads(json.dumps(query_plan)),
                        "source_evidence_ref": evidence_ref,
                    }
                )
            elif call.get("tool_name") == "sap_skill_execute":
                skill_id = str(safe_input.get("skill_id") or "")
                skill_input = safe_input.get("input")
                if skill_id and isinstance(skill_input, dict):
                    steps.append(
                        {
                            "id": f"skill_{len(steps) + 1}",
                            "tool": "skill",
                            "skill_id": skill_id,
                            "input": json.loads(json.dumps(skill_input)),
                            "source_evidence_ref": evidence_ref,
                        }
                    )
        if not steps:
            raise DraftError(
                "The accepted harness result has no replayable GET-only SAP or Skill plan."
            )
        return {
            "kind": "sap_business_agents_harness",
            "runtime": "deterministic_replay",
            "source_run_id": run.run_id,
            "steps": steps,
        }

    def _assert_not_imported(self, draft_id: str) -> None:
        imported = self.store.get_draft_import(draft_id)
        if imported:
            raise DraftError(f"Continue editing in Agent management: {imported['managed_draft_id']}")

    def validate(self, draft_id: str) -> DraftRecord:
        draft = self.store.get_draft(draft_id)
        path = Path(draft.path)
        issues: list[str] = []
        review_issues: list[str] = []
        try:
            manifest = json.loads((path / "agent.json").read_text(encoding="utf-8"))
            validate_execution(manifest, str(path / "agent.json"))
            if _contains_write_operation(manifest):
                issues.append("Draft contains a write-like SAP operation.")
            review_issues.extend(
                _gap_contract_issues(manifest, (draft.origin or {}).get("gap_contract"))
            )
            session_origin = (draft.origin or {}).get("free_query_session")
            if isinstance(session_origin, dict):
                if session_origin.get("requires_parameterization_review"):
                    review_issues.append(
                        "Free-query sample identifiers must be reviewed and mapped to Agent inputs."
                    )
                for gap in session_origin.get("evidence_gaps") or []:
                    review_issues.append(f"Accepted free-query evidence gap: {gap}.")
        except (OSError, json.JSONDecodeError, ManifestError) as exc:
            issues.append(str(exc))
        draft.status = "invalid" if issues else "needs_review" if review_issues else "validated"
        draft.validation = {
            "valid": not issues and not review_issues,
            "issues": [*issues, *review_issues],
            "review_required": bool(review_issues),
        }
        self.store.save_draft(draft)
        return draft

    def add_review_input(self, draft_id: str, review_input: str) -> DraftRecord:
        self._assert_not_imported(draft_id)
        draft = self.store.get_draft(draft_id)
        if draft.status == "applied":
            raise DraftError("An applied draft can no longer be revised in the isolation directory.")
        path = Path(draft.path)
        manifest_path = path / "agent.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        authoring = manifest.setdefault("authoring", {})
        authoring.setdefault("reviewInputs", []).append(review_input)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        with (path / "review-notes.md").open("a", encoding="utf-8") as notes:
            notes.write(f"- {review_input}\n")
        draft.status = "generated"
        self.store.save_draft(draft)
        return self.validate(draft_id)

    def apply(self, draft_id: str) -> DraftRecord:
        self._assert_not_imported(draft_id)
        draft = self.validate(draft_id)
        if draft.status != "validated":
            raise DraftError("Draft validation failed and cannot be applied.")
        manifest = json.loads((Path(draft.path) / "agent.json").read_text(encoding="utf-8"))
        target = self.settings.repository_root / "agents" / "Common" / manifest["slug"]
        if target.exists():
            raise DraftError(f"Agent target already exists: {target}")
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.settings.repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
        if status.stdout.strip():
            raise DraftError("Apply requires a clean Git worktree.")
        branch = f"codex/agent-{manifest['slug']}"
        subprocess.run(
            ["git", "switch", "-c", branch],
            cwd=self.settings.repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
        shutil.copytree(draft.path, target)
        draft.status = "applied"
        draft.validation["branch"] = branch
        draft.validation["target"] = str(target)
        self.store.save_draft(draft)
        return draft


def _manifest_from_run(
    slug: str, query: str, plan: dict[str, Any], correction: str
) -> dict[str, Any]:
    title = query[:80]
    plan_copy = json.loads(json.dumps(plan))
    execution_steps = _execution_steps_from_plan(plan_copy)
    return {
        "schemaVersion": 2,
        "slug": slug,
        "module": "Common",
        "title": {"zh": title, "en": title},
        "summary": {
            "zh": "由已验证的自由 SAP 查询生成的只读 Agent 草稿。",
            "en": "Read-only Agent draft generated from a validated free SAP query.",
        },
        "status": "Draft",
        "version": "0.1.0",
        "owner": "Unassigned",
        "tags": ["Generated draft", "Read-only"],
        "sapModules": ["Common"],
        "transactions": [],
        "tables": [str(plan.get("entity_set") or "SAP OData entity")],
        "systems": ["SAP S/4HANA"],
        "inputs": {"zh": ["原始自然语言查询"], "en": ["Original natural-language query"]},
        "outputs": {
            "zh": ["执行状态", "查询源完整性", "成功数据源数量"],
            "en": ["Execution status", "Query-source completeness", "Successful source count"],
        },
        "guardrails": {
            "zh": ["严格只读；发布前必须由业务与SAP负责人复核。"],
            "en": ["Strictly read-only; requires business and SAP review before publication."],
        },
        "workflow": [
            {
                "id": "execute-validated-plan",
                "title": {"zh": "执行已验证查询计划", "en": "Execute validated query plan"},
                "description": {
                    "zh": "通过已选择的 SAP 只读 Provider 验证并执行从成功自由查询保存的 GET-only 计划。",
                    "en": "Validate and execute the saved GET-only plan through the selected SAP read Provider.",
                },
                "tools": [
                    {
                        "name": "sap_read_execute_plan",
                        "kind": "SAP Read Provider",
                        "purpose": {"zh": "执行只读 SAP 查询。", "en": "Execute the read-only SAP query."},
                    }
                ],
                "executionStepIds": [step["id"] for step in execution_steps],
            },
            {
                "id": "validate-evidence",
                "title": {"zh": "验证证据完整性", "en": "Validate evidence completeness"},
                "description": {
                    "zh": "执行本地确定性完整性规则，不访问或修改 SAP。",
                    "en": "Run the local deterministic completeness rule without accessing or changing SAP.",
                },
                "tools": [
                    {
                        "name": "evidence_summary",
                        "kind": "Local deterministic rule",
                        "purpose": {"zh": "汇总证据完整性。", "en": "Summarize evidence completeness."},
                    }
                ],
                "executionStepIds": ["validate_evidence"],
            },
        ],
        "execution": {
            "mode": "deterministic",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "default": query,
                        "title": {"zh": "原始自然语言查询", "en": "Original natural-language query"},
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            "steps": [
                *execution_steps,
                {
                    "id": "validate_evidence",
                    "executor": "rule",
                    "operation": "evidence_summary",
                    "inputMapping": {
                        step["id"]: f"{{{{steps.{step['id']}.output}}}}"
                        for step in execution_steps
                    },
                },
            ],
            "outputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "title": {"zh": "执行状态", "en": "Execution status"}},
                    "source_complete": {"type": "boolean", "title": {"zh": "查询源完整性", "en": "Query-source completeness"}},
                    "successful_source_count": {"type": "integer", "title": {"zh": "成功数据源数量", "en": "Successful source count"}},
                },
                "required": ["status", "source_complete", "successful_source_count"],
                "additionalProperties": False,
            },
            "outputMapping": {
                "status": "{{steps.validate_evidence.output.status}}",
                "source_complete": "{{steps.validate_evidence.output.source_complete}}",
                "successful_source_count": "{{steps.validate_evidence.output.successful_source_count}}",
            },
            "acceptance": {
                "comparisonMode": "business_semantic",
                "businessKeys": ["query"],
                "facts": ["status"],
                "metrics": ["successful_source_count"],
                "currencyAndUnitPolicy": "compare_only_when_same_or_conversion_validated",
                "requiredLimitations": ["source_completeness_not_overstated"],
            },
        },
        "authoring": {"source": "validated_free_query", "correction": correction},
    }


def _execution_steps_from_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    if plan.get("kind") != "sap_business_agents_harness":
        return [
            {
                "id": "execute_plan",
                "executor": "sap_read",
                "operation": "execute_plan",
                "readOnly": True,
                "request": {"plan": plan},
            }
        ]
    execution_steps: list[dict[str, Any]] = []
    for index, step in enumerate(plan.get("steps") or [], start=1):
        step_id = str(step.get("id") or f"step_{index}")
        if step.get("tool") == "sap_read":
            execution_steps.append(
                {
                    "id": step_id,
                    "executor": "sap_read",
                    "operation": "execute_plan",
                    "readOnly": True,
                    "request": {"plan": step.get("plan")},
                }
            )
        elif step.get("tool") == "skill":
            execution_steps.append(
                {
                    "id": step_id,
                    "executor": "skill",
                    "operation": "execute",
                    "skillId": step.get("skill_id"),
                    "readOnly": True,
                    "request": step.get("input") or {},
                }
            )
    if not execution_steps:
        raise DraftError("The validated harness plan has no executable steps.")
    return execution_steps


def _parameterize_session_draft(path: Path, source_plan: dict[str, Any]) -> None:
    """Remove discoverable sample keys from executable requests in a session draft.

    This is deliberately conservative. Literals whose business meaning cannot be
    identified remain visible to the reviewer and the draft stays needs_review.
    """

    manifest_path = path / "agent.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    execution = manifest.get("execution") or {}
    specs: dict[str, dict[str, Any]] = {}
    source_keys: dict[tuple[str, str], str] = {}
    sample_literals: dict[str, str] = {}
    for step in execution.get("steps") or []:
        request = step.get("request")
        if isinstance(request, dict):
            _parameterize_plan_filters(request, specs, source_keys, sample_literals)
    for step in execution.get("steps") or []:
        if isinstance(step.get("request"), dict):
            step["request"] = _redact_parameter_samples(
                step["request"], sample_literals
            )
    if specs:
        properties = {
            name: {
                key: value
                for key, value in spec.items()
                if key not in {"sample_hash", "source_field"}
            }
            for name, spec in specs.items()
        }
        execution["inputSchema"] = {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }
        acceptance = execution.get("acceptance")
        if isinstance(acceptance, dict):
            acceptance["businessKeys"] = list(properties)
        manifest["inputs"] = {
            "zh": [str(spec["title"]["zh"]) for spec in specs.values()],
            "en": [str(spec["title"]["en"]) for spec in specs.values()],
        }
    authoring = manifest.setdefault("authoring", {})
    authoring["parameterization"] = {
        "status": "needs_review",
        "input_fields": list(specs),
        "source_plan_digest": _content_digest(source_plan),
        "sample_value_hashes": {
            name: spec["sample_hash"] for name, spec in specs.items()
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    fixture_path = path / "fixtures" / "validated-run.json"
    if fixture_path.is_file():
        fixture_path.write_text(
            json.dumps(
                {
                    "source": "accepted_free_query_iteration",
                    "parameterized_plan_digest": _content_digest(
                        [step.get("request") for step in execution.get("steps") or []]
                    ),
                    "sample_inputs": {
                        name: _fixture_placeholder(spec) for name, spec in specs.items()
                    },
                    "sample_value_hashes": {
                        name: spec["sample_hash"] for name, spec in specs.items()
                    },
                    "completeness_policy": (
                        "Missing evidence remains missing; fixture values are redacted."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    contract_path = path / "docs" / "data-contract.json"
    if contract_path.is_file():
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["input_schema"] = execution.get("inputSchema")
        contract["sample_values_redacted"] = True
        contract_path.write_text(
            json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _parameterize_plan_filters(
    value: Any,
    specs: dict[str, dict[str, Any]],
    source_keys: dict[tuple[str, str], str],
    sample_literals: dict[str, str],
) -> None:
    if isinstance(value, dict):
        filters = value.get("filters")
        if isinstance(filters, list):
            for condition in filters:
                if not isinstance(condition, dict):
                    continue
                field = str(condition.get("field") or "").strip()
                if not _is_parameterizable_sap_field(field):
                    continue
                value_key = "value" if "value" in condition else "values" if "values" in condition else ""
                if not value_key:
                    continue
                sample = condition.get(value_key)
                if _is_template_value(sample) or sample is None:
                    continue
                name = _parameter_name(
                    field,
                    str(condition.get("operator") or "eq"),
                    sample,
                    specs,
                    source_keys,
                )
                condition[value_key] = f"{{{{input.{name}}}}}"
                samples = sample if isinstance(sample, list) else [sample]
                for literal in samples:
                    if isinstance(literal, str) and literal:
                        sample_literals[literal] = f"<input:{name}>"
        for child in value.values():
            _parameterize_plan_filters(child, specs, source_keys, sample_literals)
    elif isinstance(value, list):
        for child in value:
            _parameterize_plan_filters(child, specs, source_keys, sample_literals)


def _redact_parameter_samples(value: Any, samples: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_parameter_samples(child, samples)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_parameter_samples(child, samples) for child in value]
    if isinstance(value, str) and "{{" not in value:
        for sample, replacement in sorted(
            samples.items(), key=lambda item: len(item[0]), reverse=True
        ):
            value = value.replace(sample, replacement)
    return value


def _parameter_name(
    field: str,
    operator: str,
    sample: Any,
    specs: dict[str, dict[str, Any]],
    source_keys: dict[tuple[str, str], str],
) -> str:
    sample_digest = _content_digest(sample)
    key = (field, sample_digest)
    if key in source_keys:
        return source_keys[key]
    base = re.sub(r"(?<!^)(?=[A-Z])", "_", field).lower()
    base = re.sub(r"[^a-z0-9_]+", "_", base).strip("_") or "sap_value"
    normalized_operator = operator.lower()
    if normalized_operator in {"ge", "gt"}:
        base += "_from"
    elif normalized_operator in {"le", "lt"}:
        base += "_to"
    name = base
    suffix = 2
    while name in specs:
        name = f"{base}_{suffix}"
        suffix += 1
    schema = _schema_for_sample(sample)
    schema["title"] = _localized_parameter_title(field)
    schema["source_field"] = field
    schema["sample_hash"] = sample_digest
    specs[name] = schema
    source_keys[key] = name
    return name


_PARAMETER_TITLES_ZH = {
    "accountingdocument": "财务凭证",
    "accountingdocumentitem": "财务凭证行项目",
    "companycode": "公司代码",
    "customer": "客户",
    "fiscalyear": "会计年度",
    "material": "物料",
    "plant": "工厂",
    "purchaseorder": "采购订单",
    "purchaseorderitem": "采购订单项目",
    "salesorder": "销售订单",
    "salesorderitem": "销售订单项目",
    "supplier": "供应商",
}


def _localized_parameter_title(field: str) -> dict[str, str]:
    normalized = re.sub(r"[^a-z0-9]+", "", field.lower())
    english = re.sub(r"(?<!^)(?=[A-Z])", " ", field).replace("_", " ").strip()
    english = english[:1].upper() + english[1:] if english else "Business field"
    return {
        "zh": _PARAMETER_TITLES_ZH.get(normalized, f"业务字段（{field}）"),
        "en": english,
    }


def _schema_for_sample(sample: Any) -> dict[str, Any]:
    if isinstance(sample, list):
        item = next((value for value in sample if value is not None), "")
        return {"type": "array", "minItems": 1, "items": _schema_for_sample(item)}
    if isinstance(sample, bool):
        return {"type": "boolean"}
    if isinstance(sample, int):
        return {"type": "integer"}
    if isinstance(sample, float):
        return {"type": "number"}
    schema: dict[str, Any] = {"type": "string", "minLength": 1}
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(sample)):
        schema["format"] = "date"
    return schema


def _fixture_placeholder(spec: dict[str, Any]) -> Any:
    value_type = spec.get("type")
    if value_type == "array":
        return ["<redacted>"]
    if value_type == "integer":
        return 1
    if value_type == "number":
        return 0
    if value_type == "boolean":
        return False
    return "<redacted>"


def _is_template_value(value: Any) -> bool:
    return isinstance(value, str) and "{{" in value and "}}" in value


def _is_parameterizable_sap_field(field: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", field.lower())
    if not normalized or any(
        token in normalized
        for token in ("status", "type", "category", "indicator", "flag")
    ):
        return False
    return any(
        token in normalized
        for token in (
            "order", "document", "material", "supplier", "customer", "companycode",
            "plant", "mrp", "date", "year", "period", "batch", "storagelocation",
            "invoice", "delivery", "organization", "costcenter", "workcenter", "wbs",
        )
    )


def _gap_contract_issues(manifest: dict[str, Any], gap_contract: Any) -> list[str]:
    if not isinstance(gap_contract, dict):
        return []
    execution = manifest.get("execution") or {}
    issues: list[str] = []
    if not (gap_contract.get("required_inputs") or gap_contract.get("required_outputs")):
        issues.append("Gap contract requires business-owner input and output definition review.")
    for label, contract_key, schema_key in (
        ("input", "required_inputs", "inputSchema"),
        ("output", "required_outputs", "outputSchema"),
    ):
        properties = ((execution.get(schema_key) or {}).get("properties") or {})
        for spec in gap_contract.get(contract_key) or []:
            if not isinstance(spec, dict):
                continue
            name = str(spec.get("name") or "")
            expected = str(spec.get("type") or "")
            actual = properties.get(name)
            if not isinstance(actual, dict):
                issues.append(f"Gap contract {label} port is not implemented: {name}.")
            elif expected and str(actual.get("type") or "") != expected:
                issues.append(
                    f"Gap contract {label} port type is unresolved: {name} expects {expected}."
                )
    return issues


def _contains_write_operation(value: Any) -> bool:
    write_terms = {"POST", "PATCH", "PUT", "DELETE", "posting", "update", "create_document"}
    if isinstance(value, dict):
        return any(
            str(child).upper() in write_terms if key in {"http_method", "method", "operation"} else _contains_write_operation(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_write_operation(child) for child in value)
    return False


def _readme(slug: str, query: str, correction: str) -> str:
    return f"""# {slug}\n\nGenerated from a validated free SAP query.\n\n- Query: {query}\n- User correction: {correction or 'None'}\n- Boundary: selected SAP Provider GET-only execution\n\nReview the business semantics, input schema, output contract, completeness requirements, and deterministic rules before publishing.\n"""


def _content_digest(value: Any) -> str:
    import hashlib

    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()
