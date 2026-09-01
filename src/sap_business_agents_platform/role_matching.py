from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import Settings
from .database import RunStore
from .models import utc_now
from .role_matching_documents import load_chunks, preflight_scan, scan_and_extract
from .scheduler import LocalRunScheduler, WorkloadClass
from .workflow_composer import (
    WorkflowCompositionError,
    compact_agent_catalog,
    compile_workflow_proposal,
)


class RoleMatchingError(RuntimeError):
    def __init__(self, message: str, *, code: str = "role_matching_error", detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class RoleMatchingService:
    def __init__(
        self,
        settings: Settings,
        store: RunStore,
        agents: Any,
        runtime: Any,
        workflow_drafts: Any,
    ) -> None:
        self.settings = settings
        self.store = store
        self.agents = agents
        self.runtime = runtime
        self.workflow_drafts = workflow_drafts
        self.root = (settings.data_root / "role-matching").resolve()
        self.scheduler = LocalRunScheduler(
            store,
            {WorkloadClass.role_matching: self._execute},
            worker_counts={WorkloadClass.role_matching: settings.local_role_matching_workers},
        )

    async def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        await self.scheduler.start()

    async def stop(self) -> None:
        await self.scheduler.stop()

    def preflight(self, paths: list[str]) -> dict[str, Any]:
        return preflight_scan(
            paths, max_files=self.settings.role_matching_max_files,
            max_file_bytes=self.settings.role_matching_max_file_bytes,
            max_total_bytes=self.settings.role_matching_max_total_bytes,
        )

    async def create(self, *, paths: list[str], locale: str, consent: bool) -> dict[str, Any]:
        if not consent:
            raise RoleMatchingError(
                "Document content sharing with the selected Runtime must be confirmed.",
                code="role_matching_runtime_consent_required",
            )
        provider_id = str(getattr(self.runtime, "current_provider_id", "codex"))
        if provider_id != "codex" or not self.runtime.supports("analyze_role_matching"):
            raise RoleMatchingError(
                "The selected Agent Runtime has not passed role-matching acceptance.",
                code="role_matching_runtime_unavailable",
                detail={"provider_id": provider_id},
            )
        snapshot = self.runtime.snapshot(provider_id)
        session_id = f"role_session_{uuid.uuid4().hex[:16]}"
        session = self.store.create_role_matching_session(
            {
                "session_id": session_id,
                "status": "queued",
                "phase": "queued",
                "locale": locale,
                "paths": paths,
                "runtime": snapshot,
            }
        )
        self.store.save_role_matching_turn(
            {
                "session_id": session_id,
                "turn": 1,
                "kind": "initial",
                "status": "queued",
                "rematch_mode": "full",
                "added_paths": paths,
            }
        )
        self._event(session_id, "session_queued", {"turn": 1})
        job = await self.scheduler.enqueue(WorkloadClass.role_matching, session_id)
        self.store.save_role_matching_job(
            session_id=session_id, job_id=job["job_id"], turn=1, mode="full", status="queued"
        )
        return self.store.update_role_matching_session(session_id, active_job_id=job["job_id"])

    def get(self, session_id: str) -> dict[str, Any]:
        session = self.store.get_role_matching_session(session_id)
        active = session.get("active_job_id")
        if active:
            try:
                job = self.store.get_execution_job(str(active))
                session["scheduler_status"] = job["status"]
                session["queue_position"] = self.scheduler.queue_position(str(active))
            except KeyError:
                session["scheduler_status"] = None
                session["queue_position"] = None
        session["turns"] = self.store.list_role_matching_turns(session_id)
        return session

    def documents(self, session_id: str) -> list[dict[str, Any]]:
        self.store.get_role_matching_session(session_id)
        return [self._public_document(item) for item in self.store.list_role_matching_documents(session_id)]

    def revisions(self, session_id: str) -> list[dict[str, Any]]:
        self.store.get_role_matching_session(session_id)
        return [
            {key: value for key, value in item.items() if key != "result"}
            for item in self.store.list_role_matching_revisions(session_id)
        ]

    def revision(self, session_id: str, revision: int) -> dict[str, Any]:
        return self.store.get_role_matching_revision(session_id, revision)

    async def feedback(
        self,
        session_id: str,
        *,
        base_revision: int,
        message: str,
        mode: str,
        added_paths: list[str],
        excluded_document_ids: list[str],
    ) -> dict[str, Any]:
        session = self.get(session_id)
        if int(session["current_revision"]) != base_revision:
            raise RoleMatchingError(
                "The role-matching revision changed before this feedback was submitted.",
                code="role_matching_revision_conflict",
            )
        if session["status"] not in {"completed", "waiting_input"}:
            raise RoleMatchingError("This session already has active work.", code="role_matching_job_active")
        turns = self.store.list_role_matching_turns(session_id)
        runtime_turns = len(turns)
        if runtime_turns >= int(self.settings.max_role_matching_turns):
            raise RoleMatchingError("The role-matching turn limit has been reached.", code="role_matching_turn_limit")
        known = {item["document_id"] for item in self.store.list_role_matching_documents(session_id)}
        unknown = sorted(set(excluded_document_ids).difference(known))
        if unknown:
            raise RoleMatchingError("An excluded document ID is unknown.", code="role_matching_document_unknown", detail=unknown)
        paths = list(session["paths"])
        for path in added_paths:
            if path not in paths:
                paths.append(path)
        requested_exclusions = sorted(set(excluded_document_ids))
        turn = runtime_turns + 1
        self.store.save_role_matching_turn(
            {
                "session_id": session_id,
                "turn": turn,
                "base_revision": base_revision,
                "kind": "feedback",
                "status": "queued",
                "message": message,
                "rematch_mode": mode,
                "added_paths": added_paths,
                "excluded_document_ids": requested_exclusions,
            }
        )
        self.store.update_role_matching_session(
            session_id, status="queued", phase="queued", paths=paths, error=None, completed_at=None
        )
        self._event(session_id, "feedback_queued", {"turn": turn, "mode": mode})
        job = await self.scheduler.enqueue(WorkloadClass.role_matching, session_id)
        self.store.save_role_matching_job(
            session_id=session_id, job_id=job["job_id"], turn=turn, mode=mode, status="queued"
        )
        return self.store.update_role_matching_session(session_id, active_job_id=job["job_id"])

    async def cancel(self, session_id: str) -> dict[str, Any]:
        session = self.get(session_id)
        if session["status"] in {"completed", "failed", "cancelled"}:
            return session
        active = session.get("active_job_id")
        if active:
            await self.scheduler.cancel(str(active))
            turns = self.store.list_role_matching_turns(session_id)
            if turns:
                self.store.save_role_matching_job(
                    session_id=session_id, job_id=str(active), turn=int(turns[-1]["turn"]),
                    mode=str(turns[-1].get("rematch_mode") or "full"), status="cancelled",
                )
        try:
            await self.runtime.cancel(session.get("thread_id"))
        except Exception:
            pass
        self._event(session_id, "session_cancelled", {})
        return self.store.update_role_matching_session(
            session_id, status="cancelled", phase="cancelled", completed_at=utc_now()
        )

    def create_workflow_draft(
        self, session_id: str, suggestion_id: str, *, revision: int, catalog_digest: str
    ) -> Any:
        item = self.store.get_role_matching_revision(session_id, revision)
        if item["catalog_digest"] != catalog_digest:
            raise RoleMatchingError("The Agent catalog changed; rematch before creating a draft.", code="role_matching_catalog_changed")
        suggestion = next(
            (value for value in item["result"].get("workflow_suggestions") or [] if value.get("suggestion_id") == suggestion_id),
            None,
        )
        if not isinstance(suggestion, dict) or not isinstance(suggestion.get("compiled_workflow"), dict):
            raise RoleMatchingError("The workflow suggestion is unavailable.", code="role_matching_workflow_suggestion_unknown")
        workflow = deepcopy(suggestion["compiled_workflow"])
        draft = self.workflow_drafts.create(
            workflow.get("title") or {"zh": suggestion_id, "en": suggestion_id},
            workflow.get("description") or {"zh": "", "en": ""},
            workflow,
        )
        draft.composition.update(
            {
                "source_role_matching_session_id": session_id,
                "source_role_matching_revision": revision,
                "source_role_matching_suggestion_id": suggestion_id,
                "catalog_digest": catalog_digest,
            }
        )
        self.store.save_workflow_draft(draft)
        return draft

    def markdown(self, session_id: str, revision: int) -> str:
        item = self.revision(session_id, revision)
        result = item["result"]
        lines = [f"# {result.get('summary', {}).get('zh') or '岗位与 Agent 匹配报告'}", ""]
        for title, key in [("岗位", "roles"), ("业务流程", "processes"), ("SAP日常操作", "operations"), ("Agent匹配", "agent_matches"), ("工作流建议", "workflow_suggestions"), ("Agent缺口", "agent_gaps")]:
            lines.extend([f"## {title}", ""])
            values = result.get(key) or []
            if not values:
                lines.append("- 无")
            else:
                for value in values:
                    name = value.get("name") or value.get("title") or value.get("required_capability") or value.get("agent_id") or value.get("operation_id")
                    lines.append(f"- {name}")
                    for ref in value.get("evidence_refs") or []:
                        locator = json.dumps(ref.get("locator") or {}, ensure_ascii=False, sort_keys=True)
                        lines.append(
                            f"  - 来源：`{ref.get('document_id')}` / `{ref.get('chunk_id')}` / `{locator}`"
                        )
            lines.append("")
        return "\n".join(lines)

    def csv(self, session_id: str, revision: int, kind: str) -> str:
        item = self.revision(session_id, revision)
        values = item["result"].get(kind) or []
        keys = sorted({key for value in values if isinstance(value, dict) for key in value if not isinstance(value[key], (dict, list))})
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=keys)
        writer.writeheader()
        for value in values:
            writer.writerow({key: value.get(key, "") for key in keys})
        return output.getvalue()

    async def delete(self, session_id: str) -> None:
        session = self.get(session_id)
        if session["status"] not in {"completed", "failed", "cancelled"}:
            raise RoleMatchingError("Cancel active analysis before deletion.", code="role_matching_delete_active")
        cache = (self.root / session_id).resolve()
        if self.root not in cache.parents:
            raise RoleMatchingError("Role-matching cache path escaped its root.")
        if cache.exists():
            shutil.rmtree(cache)
        self.store.delete_role_matching_session(session_id)

    async def _execute(self, session_id: str) -> None:
        session = self.get(session_id)
        turns = self.store.list_role_matching_turns(session_id)
        turn = turns[-1]
        mode = str(turn.get("rematch_mode") or "full")
        active_job_id = str(session.get("active_job_id") or "")
        if active_job_id:
            self.store.save_role_matching_job(
                session_id=session_id, job_id=active_job_id,
                turn=int(turn["turn"]), mode=mode, status="running",
            )
        try:
            self._phase(session_id, "scanning", "scan_started")
            cache_root = self.root / session_id / "documents"
            scanned = scan_and_extract(
                list(session["paths"]), cache_root=cache_root,
                max_files=self.settings.role_matching_max_files,
                max_file_bytes=self.settings.role_matching_max_file_bytes,
                max_total_bytes=self.settings.role_matching_max_total_bytes,
                reuse_by_hash=(
                    {item["sha256"]: item for item in self.store.list_role_matching_documents(session_id)}
                    if mode == "incremental" else None
                ),
            )
            if self.get(session_id)["status"] == "cancelled":
                return
            excluded = set(turn.get("excluded_document_ids") or [])
            self.store.save_role_matching_documents(session_id, scanned["documents"], excluded=excluded)
            self._phase(
                session_id, "extracting", "extraction_completed",
                {"documents": len(scanned["documents"]), "issues": len(scanned["issues"])},
            )
            documents = [item for item in scanned["documents"] if item["status"] == "parsed" and item["document_id"] not in excluded]
            runtime_documents: list[dict[str, Any]] = []
            total_chars = 0
            for document in documents:
                chunks = load_chunks(document)
                total_chars += sum(len(str(chunk.get("text") or "")) for chunk in chunks)
                runtime_documents.append(
                    {"document_id": document["document_id"], "name": document["name"], "chunks": chunks}
                )
            if total_chars > self.settings.role_matching_max_runtime_chars:
                raise RoleMatchingError(
                    "Extracted text exceeds the bounded Runtime context; narrow the selected paths.",
                    code="role_matching_runtime_context_limit",
                    detail={"characters": total_chars, "limit": self.settings.role_matching_max_runtime_chars},
                )
            if not runtime_documents:
                raise RoleMatchingError("No supported document text could be extracted.", code="role_matching_no_parseable_documents")
            catalog = self._catalog()
            previous = None
            if session["current_revision"]:
                previous = self.store.get_role_matching_revision(session_id, int(session["current_revision"]))["result"]
            self._phase(session_id, "understanding", "understanding_started")
            if self.get(session_id)["status"] == "cancelled":
                return
            provider_id = str((session.get("runtime") or {}).get("provider_id") or "codex")
            with self.runtime.pin(provider_id):
                method = self.runtime.review_role_matching_feedback if previous else self.runtime.analyze_role_matching
                raw = await method(
                    documents=runtime_documents,
                    agent_catalog=catalog,
                    previous_result=previous,
                    user_context=str(turn.get("message") or ""),
                    rematch_mode=mode,
                    locale=session["locale"],
                    thread_id=session.get("thread_id"),
                )
            if self.get(session_id)["status"] == "cancelled":
                return
            self.store.update_role_matching_session(session_id, thread_id=raw.get("thread_id"))
            self._phase(session_id, "matching_agents", "matching_started")
            result = self._validate_analysis(raw.get("analysis") or {}, catalog, scanned)
            self._phase(session_id, "compiling_workflows", "workflow_compilation_started")
            validated_suggestions, workflow_issues = self._compile_suggestions(
                session_id, result.get("workflow_suggestions") or [], catalog, session["locale"]
            )
            result["workflow_suggestions"] = validated_suggestions
            result["workflow_validation_issues"] = workflow_issues
            result["completeness"] = {
                "scan_complete": bool(scanned["scan_complete"]),
                "extraction_complete": bool(scanned["extraction_complete"]),
                "business_understanding_complete": True,
                "agent_catalog_complete": True,
                "matching_complete": True,
                "workflow_validation_complete": not workflow_issues,
            }
            result["document_issues"] = [
                *[item for item in result.get("document_issues") or [] if isinstance(item, dict)],
                *[self._public_issue(item) for item in scanned["issues"]],
            ]
            result["change_summary"] = _change_summary(previous, result)
            result_digest = _digest(result)
            revision = int(session["current_revision"]) + 1
            now = utc_now()
            self.store.save_role_matching_revision(
                {
                    "session_id": session_id, "revision": revision,
                    "parent_revision": int(session["current_revision"]) or None, "mode": mode,
                    "catalog_digest": catalog["digest"],
                    "document_ids": [item["document_id"] for item in documents],
                    "result": result, "result_digest": result_digest,
                    "created_at": now, "completed_at": now,
                }
            )
            turn.update({"status": "completed", "result_revision": revision, "decision": {"result_digest": result_digest, "change_summary": result["change_summary"]}, "completed_at": now})
            self.store.save_role_matching_turn(turn)
            self._phase(session_id, "reviewing", "analysis_completed", {"revision": revision})
            self.store.update_role_matching_session(
                session_id, status="completed", phase="reviewing", current_revision=revision,
                active_job_id=None, error=None, completed_at=now,
            )
            if active_job_id:
                self.store.save_role_matching_job(
                    session_id=session_id, job_id=active_job_id,
                    turn=int(turn["turn"]), mode=mode, status="completed",
                )
        except Exception as exc:
            error = {"code": str(getattr(exc, "code", "role_matching_failed")), "message": str(exc), "detail": getattr(exc, "detail", None)}
            turn.update({"status": "failed", "decision": {"error": error}, "completed_at": utc_now()})
            self.store.save_role_matching_turn(turn)
            self._event(session_id, "analysis_failed", {"code": error["code"]})
            self.store.update_role_matching_session(
                session_id, status="failed", phase="failed", active_job_id=None,
                error=error, completed_at=utc_now(),
            )
            if active_job_id:
                self.store.save_role_matching_job(
                    session_id=session_id, job_id=active_job_id,
                    turn=int(turn["turn"]), mode=mode, status="failed",
                )
            raise

    def _catalog(self) -> dict[str, Any]:
        executable = compact_agent_catalog(self.agents)
        executable_ids = {item["agent_id"] for item in executable["items"]}
        items = []
        for agent in self.agents.list():
            if agent.get("kind") == "platform_assistant":
                continue
            validation = agent.get("validation") or {}
            execution = agent.get("execution") or {}
            items.append(
                {
                    "agent_id": agent.get("slug"), "version": agent.get("version"),
                    "module": agent.get("module"), "title": agent.get("title"),
                    "summary": agent.get("summary"), "tags": agent.get("tags") or [],
                    "sap_modules": agent.get("sapModules") or [], "workflow": agent.get("workflow") or [],
                    "input_schema": execution.get("inputSchema") or {},
                    "output_schema": execution.get("outputSchema") or {},
                    "executable": agent.get("slug") in executable_ids,
                    "validation_verdict": validation.get("verdict", "NOT_TESTED"),
                }
            )
        canonical = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {"digest": "sha256:" + hashlib.sha256(canonical.encode()).hexdigest(), "items": items, "executable_catalog": executable}

    def _validate_analysis(self, raw: dict[str, Any], catalog: dict[str, Any], scanned: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise RoleMatchingError("Runtime role analysis must be an object.", code="role_matching_runtime_output_invalid")
        result = deepcopy(raw)
        for key in ("roles", "processes", "operations", "agent_matches", "workflow_suggestions", "agent_gaps", "document_issues"):
            if not isinstance(result.get(key), list):
                result[key] = []
        agents = {str(item["agent_id"]): item for item in catalog["items"]}
        valid_refs = {
            (document["document_id"], chunk["chunk_id"]): deepcopy(chunk.get("locator") or {})
            for document in scanned["documents"] for chunk in load_chunks(document)
        }
        def checked_refs(value: Any) -> list[dict[str, Any]]:
            refs = []
            for ref in value if isinstance(value, list) else []:
                if not isinstance(ref, dict):
                    continue
                pair = (str(ref.get("document_id") or ""), str(ref.get("chunk_id") or ""))
                if pair not in valid_refs:
                    raise RoleMatchingError("Runtime returned an unknown document evidence reference.", code="role_matching_evidence_ref_invalid", detail=pair)
                refs.append({"document_id": pair[0], "chunk_id": pair[1], "locator": valid_refs[pair]})
            return refs
        for collection in ("roles", "processes", "operations", "agent_matches", "workflow_suggestions", "agent_gaps"):
            for item in result[collection]:
                if isinstance(item, dict):
                    item["evidence_refs"] = checked_refs(item.get("evidence_refs"))
                    if collection in {"roles", "processes", "operations", "agent_matches", "workflow_suggestions", "agent_gaps"} and not item["evidence_refs"]:
                        raise RoleMatchingError(
                            "Every role-matching conclusion must cite document evidence.",
                            code="role_matching_evidence_required",
                            detail={"collection": collection},
                        )
        verified_matches = []
        for match in result["agent_matches"]:
            if not isinstance(match, dict) or str(match.get("agent_id") or "") not in agents:
                continue
            agent = agents[str(match["agent_id"])]
            match.update({"executable": agent["executable"], "validation_verdict": agent["validation_verdict"]})
            if match.get("coverage") not in {"full", "partial", "none"}:
                match["coverage"] = "partial"
            if match.get("confidence") not in {"high", "medium", "low"}:
                match["confidence"] = "low"
            verified_matches.append(match)
        result["agent_matches"] = verified_matches
        return result

    def _compile_suggestions(self, session_id: str, suggestions: list[dict[str, Any]], catalog: dict[str, Any], locale: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        compiled = []
        issues: list[dict[str, Any]] = []
        executable_catalog = catalog["executable_catalog"]
        for index, suggestion in enumerate(suggestions, start=1):
            if not isinstance(suggestion, dict):
                continue
            suggestion_id = str(suggestion.get("suggestion_id") or f"suggestion_{index}")
            proposal = deepcopy(suggestion)
            if not isinstance(proposal.get("stages"), list):
                issues.append({"suggestion_id": suggestion_id, "code": "workflow_stages_missing"})
                continue
            try:
                workflow, composition = compile_workflow_proposal(
                    workflow_id=f"role-{session_id[-8:]}-{index}",
                    requirement=str(suggestion.get("description") or suggestion.get("title") or suggestion_id),
                    locale=locale, proposal=proposal, catalog=executable_catalog, agents=self.agents,
                )
                if composition.get("gaps"):
                    issues.append({"suggestion_id": suggestion_id, "code": "workflow_agent_gap", "gaps": composition["gaps"]})
                    continue
                suggestion.update({"suggestion_id": suggestion_id, "validated": True, "compiled_workflow": workflow, "compiler_version": composition.get("compiler_version")})
                compiled.append(suggestion)
            except (WorkflowCompositionError, KeyError, ValueError) as exc:
                issues.append({"suggestion_id": suggestion_id, "code": str(getattr(exc, "code", "workflow_compile_failed")), "message": str(exc)})
                continue
        return compiled, issues

    def _phase(self, session_id: str, phase: str, event: str, data: dict[str, Any] | None = None) -> None:
        self.store.update_role_matching_session(session_id, status=phase, phase=phase)
        self._event(session_id, event, data or {})

    def _event(self, session_id: str, event: str, data: dict[str, Any]) -> None:
        safe = {key: value for key, value in data.items() if "path" not in key.lower() and "text" not in key.lower()}
        self.store.append_role_matching_event(session_id, event, safe)

    @staticmethod
    def _public_document(document: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in document.items() if key not in {"cache_path"}}

    @staticmethod
    def _public_issue(issue: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in issue.items() if key != "path"}


def _digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _change_summary(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    identifiers = {
        "roles": ("role_id", "name"), "processes": ("process_id", "name"),
        "operations": ("operation_id",), "agent_matches": ("operation_id", "agent_id"),
        "workflow_suggestions": ("suggestion_id",), "agent_gaps": ("gap_id",),
    }
    for collection, keys in identifiers.items():
        old_values = (previous or {}).get(collection) or []
        new_values = current.get(collection) or []
        def keyed(values: list[Any]) -> dict[str, str]:
            mapped = {}
            for index, item in enumerate(values):
                if not isinstance(item, dict):
                    continue
                identity = "|".join(str(item.get(key) or "") for key in keys).strip("|") or str(index)
                mapped[identity] = _digest(item)
            return mapped
        old, new = keyed(old_values), keyed(new_values)
        result[collection] = {
            "added": len(set(new) - set(old)), "removed": len(set(old) - set(new)),
            "changed": sum(old[key] != new[key] for key in set(old) & set(new)),
        }
    return result
