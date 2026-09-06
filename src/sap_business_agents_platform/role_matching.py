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
from .role_matching_documents import (
    USER_DESCRIPTION_EXTENSION,
    create_user_description_document,
    empty_document_scan,
    load_chunks,
    preflight_scan,
    scan_and_extract,
)
from .scheduler import LocalRunScheduler, WorkloadClass
from .workflow_composer import (
    WorkflowCompositionError,
    compact_agent_catalog,
    compile_workflow_proposal,
)


# Keep each Runtime page comfortably below the SDK turn-size boundary. The complete
# compiler catalog remains server-side; smaller semantic pages trade a few bounded
# turns for reliable evaluation of Agents that appear near the end of the catalog.
ROLE_MATCHING_CATALOG_PAGE_CHARS = 12_000


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

    def preflight(
        self, paths: list[str], *, role_description: str | None = None
    ) -> dict[str, Any]:
        description = str(role_description or "").strip()
        if not paths and not description:
            raise RoleMatchingError(
                "At least one document path or role description is required.",
                code="role_matching_sources_required",
            )
        result = (
            preflight_scan(
                paths,
                max_files=self.settings.role_matching_max_files,
                max_file_bytes=self.settings.role_matching_max_file_bytes,
                max_total_bytes=self.settings.role_matching_max_total_bytes,
            )
            if paths
            else {
                "roots": [], "supported_file_count": 0, "total_bytes": 0,
                "issues": [], "blockers": [], "ready": True,
            }
        )
        result.update(
            {
                "source_mode": (
                    "combined" if paths and description else
                    "documents" if paths else "description"
                ),
                "description": {
                    "present": bool(description),
                    "characters": len(description),
                },
                "source_intake_complete": bool(paths or description) and bool(result["ready"]),
            }
        )
        return result

    async def create(
        self,
        *,
        paths: list[str],
        role_description: str | None = None,
        locale: str,
        consent: bool,
    ) -> dict[str, Any]:
        role_description = str(role_description or "").strip() or None
        if not paths and not role_description:
            raise RoleMatchingError(
                "At least one document path or role description is required.",
                code="role_matching_sources_required",
            )
        if role_description and len(role_description) > 12_000:
            raise RoleMatchingError(
                "Role description exceeds the 12000-character limit.",
                code="role_matching_description_limit",
            )
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
        description_documents: list[dict[str, Any]] = []
        if role_description:
            description_documents.append(
                create_user_description_document(
                    role_description,
                    cache_root=self.root / session_id / "documents",
                    turn=1,
                    locale=locale,
                )
            )
            self.store.save_role_matching_documents(
                session_id, description_documents
            )
        self.store.save_role_matching_turn(
            {
                "session_id": session_id,
                "turn": 1,
                "kind": "initial",
                "status": "queued",
                "rematch_mode": "full",
                "added_paths": paths,
                "decision": {
                    "added_description_document_ids": [
                        item["document_id"] for item in description_documents
                    ]
                },
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
        item = self.store.get_role_matching_revision(session_id, revision)
        item["catalog_current"] = item["catalog_digest"] == self._catalog()["digest"]
        return item

    async def feedback(
        self,
        session_id: str,
        *,
        base_revision: int,
        message: str,
        mode: str,
        added_paths: list[str],
        added_role_description: str | None = None,
        excluded_document_ids: list[str],
    ) -> dict[str, Any]:
        added_role_description = str(added_role_description or "").strip() or None
        if added_role_description and len(added_role_description) > 12_000:
            raise RoleMatchingError(
                "Role description exceeds the 12000-character limit.",
                code="role_matching_description_limit",
            )
        session = self.get(session_id)
        if int(session["current_revision"]) != base_revision:
            raise RoleMatchingError(
                "The role-matching revision changed before this feedback was submitted.",
                code="role_matching_revision_conflict",
            )
        # A failed analysis may be retried from the last immutable revision once
        # its worker has released the active job.  Treating every failed session
        # as active made recoverable Runtime failures (for example, an archived
        # Codex thread) permanently block the full-rematch action.
        if session["status"] not in {"completed", "waiting_input", "failed", "cancelled"}:
            raise RoleMatchingError("This session already has active work.", code="role_matching_job_active")
        active_job_id = str(session.get("active_job_id") or "")
        if active_job_id:
            try:
                active_job = self.store.get_execution_job(active_job_id)
            except KeyError:
                active_job = None
            if active_job and active_job.get("status") in {"queued", "running"}:
                raise RoleMatchingError(
                    "This session already has active work.", code="role_matching_job_active"
                )
            # Recover from terminal or missing scheduler jobs left by an older
            # process.  The immutable turn and revision remain available.
            self.store.update_role_matching_session(session_id, active_job_id=None)
            session["active_job_id"] = None
        turns = self.store.list_role_matching_turns(session_id)
        runtime_turns = sum(
            1 for item in turns
            if item.get("status") in {"planning", "running", "completed", "failed"}
        )
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
        turn = max((int(item["turn"]) for item in turns), default=0) + 1
        description_documents: list[dict[str, Any]] = []
        if added_role_description:
            description_documents.append(
                create_user_description_document(
                    added_role_description,
                    cache_root=self.root / session_id / "documents",
                    turn=turn,
                    locale=str(session["locale"]),
                )
            )
            existing_documents = self.store.list_role_matching_documents(session_id)
            self.store.save_role_matching_documents(
                session_id,
                [*existing_documents, *description_documents],
                excluded=set(requested_exclusions),
            )
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
                "decision": {
                    "added_description_document_ids": [
                        item["document_id"] for item in description_documents
                    ]
                },
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
                latest_turn = dict(turns[-1])
                if latest_turn.get("status") in {"queued", "planning", "running"}:
                    latest_turn.update({"status": "cancelled", "completed_at": utc_now()})
                    self.store.save_role_matching_turn(latest_turn)
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
            session_id, status="cancelled", phase="cancelled", active_job_id=None,
            completed_at=utc_now()
        )

    def create_workflow_draft(
        self, session_id: str, suggestion_id: str, *, revision: int, catalog_digest: str
    ) -> Any:
        item = self.store.get_role_matching_revision(session_id, revision)
        if (
            item["catalog_digest"] != catalog_digest
            or self._catalog()["digest"] != item["catalog_digest"]
        ):
            raise RoleMatchingError("The Agent catalog changed; rematch before creating a draft.", code="role_matching_catalog_changed")
        evaluation = item["result"].get("catalog_evaluation") or {}
        if not (
            evaluation.get("agent_catalog_complete")
            and evaluation.get("matching_complete")
        ):
            raise RoleMatchingError(
                "The Agent catalog was not evaluated completely; run a full rematch first.",
                code="role_matching_catalog_incomplete",
            )
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
        evaluation = result.get("catalog_evaluation") or {}
        lines.extend(
            [
                "## Agent目录覆盖",
                "",
                (
                    f"- 已检查 {evaluation.get('evaluated_agent_count', 0)} / "
                    f"{evaluation.get('total_agent_count', 0)} 个Agent；"
                    f"目录完整：{'是' if evaluation.get('agent_catalog_complete') else '否'}"
                ),
                "",
            ]
        )
        for title, key in [("岗位", "roles"), ("业务流程", "processes"), ("SAP日常操作", "operations"), ("Agent匹配", "agent_matches"), ("已排除候选", "rejected_candidates"), ("工作流建议", "workflow_suggestions"), ("Agent缺口", "agent_gaps")]:
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
                        source_name = str(ref.get("source_name") or ref.get("document_id") or "")
                        source_type = str(ref.get("source_type") or "document")
                        lines.append(
                            f"  - 来源：{source_name}（{source_type}）/ `{ref.get('chunk_id')}` / `{locator}`"
                        )
            lines.append("")
        return "\n".join(lines)

    def csv(self, session_id: str, revision: int, kind: str) -> str:
        item = self.revision(session_id, revision)
        values = item["result"].get(kind) or []
        keys = sorted({key for value in values if isinstance(value, dict) for key in value if not isinstance(value[key], (dict, list))})
        source_keys = ["source_types", "source_names"] if values else []
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=[*keys, *source_keys])
        writer.writeheader()
        for value in values:
            refs = value.get("evidence_refs") or []
            writer.writerow(
                {
                    **{key: value.get(key, "") for key in keys},
                    "source_types": ";".join(
                        sorted({str(ref.get("source_type") or "document") for ref in refs})
                    ),
                    "source_names": ";".join(
                        dict.fromkeys(str(ref.get("source_name") or "") for ref in refs)
                    ),
                }
            )
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
            stored_documents = self.store.list_role_matching_documents(session_id)
            description_documents = [
                item for item in stored_documents
                if _source_type(item) == "user_description"
            ]
            scanned = (
                scan_and_extract(
                    list(session["paths"]), cache_root=cache_root,
                    max_files=self.settings.role_matching_max_files,
                    max_file_bytes=self.settings.role_matching_max_file_bytes,
                    max_total_bytes=self.settings.role_matching_max_total_bytes,
                    reuse_by_hash=(
                        {item["sha256"]: item for item in stored_documents}
                        if mode == "incremental" else None
                    ),
                )
                if session["paths"] else empty_document_scan()
            )
            scanned["documents"] = [
                *scanned["documents"], *description_documents
            ]
            if self.get(session_id)["status"] == "cancelled":
                return
            excluded = set(turn.get("excluded_document_ids") or [])
            self.store.save_role_matching_documents(session_id, scanned["documents"], excluded=excluded)
            self._phase(
                session_id, "extracting", "extraction_completed",
                {"documents": len(scanned["documents"]), "issues": len(scanned["issues"])},
            )
            documents = [
                item for item in scanned["documents"]
                if item["status"] == "parsed" and item["document_id"] not in excluded
            ]
            runtime_documents: list[dict[str, Any]] = []
            total_chars = 0
            for document in documents:
                chunks = load_chunks(document)
                total_chars += sum(len(str(chunk.get("text") or "")) for chunk in chunks)
                runtime_documents.append(
                    {
                        "document_id": document["document_id"],
                        "name": document["name"],
                        "source_type": _source_type(document),
                        "chunks": chunks,
                    }
                )
            if total_chars > self.settings.role_matching_max_runtime_chars:
                raise RoleMatchingError(
                    "Extracted text exceeds the bounded Runtime context; narrow the selected paths.",
                    code="role_matching_runtime_context_limit",
                    detail={"characters": total_chars, "limit": self.settings.role_matching_max_runtime_chars},
                )
            if not runtime_documents:
                raise RoleMatchingError(
                    "No active document or user-description source is available.",
                    code="role_matching_no_active_sources",
                )
            catalog = self._catalog()
            previous = None
            runtime_previous = None
            reuse_business_understanding = False
            if session["current_revision"]:
                current_revision = self.store.get_role_matching_revision(
                    session_id, int(session["current_revision"])
                )
                previous = current_revision["result"]
                runtime_previous = previous
                current_document_ids = {item["document_id"] for item in documents}
                if mode == "full":
                    # "Rematch with the current complete catalog" must not make an unchanged
                    # source snapshot lose an already established business understanding. Use the
                    # latest non-empty understanding for the same immutable material set and only
                    # reevaluate the Agent catalog. If material changed, Runtime must understand it
                    # again before matching.
                    for candidate_revision in reversed(
                        self.store.list_role_matching_revisions(session_id)
                    ):
                        candidate_result = candidate_revision.get("result") or {}
                        if (
                            set(candidate_revision.get("document_ids") or [])
                            == current_document_ids
                            and candidate_result.get("operations")
                        ):
                            runtime_previous = candidate_result
                            reuse_business_understanding = True
                            break
            self._phase(session_id, "understanding", "understanding_started")
            if self.get(session_id)["status"] == "cancelled":
                return
            runtime_snapshot = session.get("runtime") or {}
            provider_id = str(runtime_snapshot.get("provider_id") or "codex")
            with self.runtime.pin(provider_id, runtime_snapshot.get("model")):
                method = self.runtime.review_role_matching_feedback if previous else self.runtime.analyze_role_matching
                raw = await method(
                    documents=runtime_documents,
                    agent_catalog=catalog,
                    previous_result=runtime_previous,
                    user_context=str(turn.get("message") or ""),
                    rematch_mode=mode,
                    reuse_business_understanding=reuse_business_understanding,
                    locale=session["locale"],
                    thread_id=session.get("thread_id"),
                )
            if self.get(session_id)["status"] == "cancelled":
                return
            self.store.update_role_matching_session(session_id, thread_id=raw.get("thread_id"))
            self._phase(session_id, "matching_agents", "matching_started")
            result = self._validate_analysis(
                raw.get("analysis") or {},
                catalog,
                {**scanned, "documents": documents},
            )
            self._phase(session_id, "compiling_workflows", "workflow_compilation_started")
            evaluation = result.get("catalog_evaluation") or {}
            catalog_complete = bool(evaluation.get("agent_catalog_complete"))
            matching_complete = bool(evaluation.get("matching_complete"))
            consolidation_complete = bool(evaluation.get("consolidation_complete"))
            if catalog_complete and matching_complete and consolidation_complete:
                validated_suggestions, workflow_issues = self._compile_suggestions(
                    session_id, result.get("workflow_suggestions") or [], catalog, session["locale"]
                )
            else:
                validated_suggestions = []
                workflow_issues = [{
                    "code": (
                        "role_matching_catalog_incomplete"
                        if not (catalog_complete and matching_complete)
                        else "role_matching_consolidation_incomplete"
                    )
                }]
                result["agent_gaps"] = []
            result["workflow_suggestions"] = validated_suggestions
            result["workflow_validation_issues"] = workflow_issues
            result["completeness"] = {
                "scan_complete": bool(scanned["scan_complete"]),
                "extraction_complete": bool(scanned["extraction_complete"]),
                "source_intake_complete": bool(scanned["scan_complete"])
                and bool(scanned["extraction_complete"])
                and all(item.get("status") == "parsed" for item in description_documents),
                "document_scan_status": (
                    "complete" if session["paths"] and scanned["scan_complete"] else
                    "incomplete" if session["paths"] else "not_requested"
                ),
                "document_source_count": sum(
                    1 for item in documents if _source_type(item) == "document"
                ),
                "description_source_count": sum(
                    1 for item in documents if _source_type(item) == "user_description"
                ),
                "description_source_complete": all(
                    item.get("status") == "parsed"
                    for item in description_documents
                    if item["document_id"] not in excluded
                ),
                "business_understanding_complete": True,
                "agent_catalog_complete": catalog_complete,
                "matching_complete": catalog_complete and matching_complete,
                "workflow_validation_complete": (
                    catalog_complete and matching_complete and consolidation_complete
                    and not workflow_issues
                ),
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
        capability_signals: dict[str, list[str]] = {}
        for agent in self.agents.list():
            if agent.get("kind") == "platform_assistant":
                continue
            validation = agent.get("validation") or {}
            execution = agent.get("execution") or {}
            agent_id = str(agent.get("slug") or "")
            capability_signals[agent_id] = _execution_capability_signals(execution)
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
        digest = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
        runtime_items = [
            {
                "agent_id": item["agent_id"],
                "version": item["version"],
                "module": item["module"],
                "title": item["title"],
                "summary": item["summary"],
                "tags": item["tags"],
                "sap_modules": item["sap_modules"],
                "input_ports": _compact_schema_ports(item["input_schema"]),
                "output_ports": _compact_schema_ports(item["output_schema"]),
                "executable": item["executable"],
                "validation_verdict": item["validation_verdict"],
                "capability_signals": capability_signals.get(str(item["agent_id"]), []),
            }
            for item in items
        ]
        runtime_pages = _paginate_runtime_catalog(
            runtime_items, digest=digest, max_chars=ROLE_MATCHING_CATALOG_PAGE_CHARS
        )
        return {
            "digest": digest,
            "items": items,
            "executable_catalog": executable,
            "capability_signals": capability_signals,
            "runtime_catalog": {
                "digest": digest,
                "total_agent_count": len(runtime_items),
                "page_count": len(runtime_pages),
                "pages": runtime_pages,
            },
        }

    def _validate_analysis(self, raw: dict[str, Any], catalog: dict[str, Any], scanned: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise RoleMatchingError("Runtime role analysis must be an object.", code="role_matching_runtime_output_invalid")
        result = deepcopy(raw)
        for key in ("roles", "processes", "operations", "agent_matches", "rejected_candidates", "workflow_suggestions", "agent_gaps", "document_issues"):
            if not isinstance(result.get(key), list):
                result[key] = []
        agents = {str(item["agent_id"]): item for item in catalog["items"]}
        valid_refs = {
            (document["document_id"], chunk["chunk_id"]): {
                "locator": deepcopy(chunk.get("locator") or {}),
                "source_type": _source_type(document),
                "source_name": str(document.get("name") or document["document_id"]),
            }
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
                source = valid_refs[pair]
                refs.append(
                    {
                        "document_id": pair[0],
                        "chunk_id": pair[1],
                        "locator": source["locator"],
                        "source_type": source["source_type"],
                        "source_name": source["source_name"],
                    }
                )
            return refs
        for collection in ("roles", "processes", "operations", "agent_matches", "rejected_candidates", "workflow_suggestions", "agent_gaps"):
            for item in result[collection]:
                if isinstance(item, dict):
                    item["evidence_refs"] = checked_refs(item.get("evidence_refs"))
                    if collection in {"roles", "processes", "operations", "agent_matches", "rejected_candidates", "workflow_suggestions", "agent_gaps"} and not item["evidence_refs"]:
                        raise RoleMatchingError(
                            "Every role-matching conclusion must cite document evidence.",
                            code="role_matching_evidence_required",
                            detail={"collection": collection},
                        )
        evaluation = _validate_catalog_evaluation(
            result.get("catalog_evaluation"), catalog,
            operation_count=len(result["operations"]),
        )
        result["catalog_evaluation"] = evaluation
        operations = {
            str(item.get("operation_id") or ""): item
            for item in result["operations"] if isinstance(item, dict)
        }
        verified_matches = []
        rejected_candidates = list(result.get("rejected_candidates") or [])
        for match in result["agent_matches"]:
            if not isinstance(match, dict) or str(match.get("agent_id") or "") not in agents:
                continue
            agent = agents[str(match["agent_id"])]
            match.update({"executable": agent["executable"], "validation_verdict": agent["validation_verdict"]})
            if match.get("coverage") not in {"full", "partial", "none"}:
                match["coverage"] = "partial"
            if match.get("confidence") not in {"high", "medium", "low"}:
                match["confidence"] = "low"
            required_signals = _operation_capability_signals(
                operations.get(str(match.get("operation_id") or ""), {})
            )
            declared_signals = set(
                catalog.get("capability_signals", {}).get(str(match["agent_id"]), [])
            )
            uncovered = [str(item) for item in match.get("uncovered_capabilities") or []]
            if (
                match.get("coverage") == "partial"
                and required_signals
                and required_signals.issubset(declared_signals)
                and uncovered
                and all(_gap_matches_signals(item, required_signals) for item in uncovered)
            ):
                match["coverage"] = "full"
                match["uncovered_capabilities"] = []
            if (
                match.get("coverage") == "full"
                and not (match.get("uncovered_capabilities") or [])
                and agent["executable"]
                and agent["validation_verdict"] == "PASS"
            ):
                # Full declared coverage by a verified executable Agent is a
                # high-confidence catalog match. Evidence provenance remains separate.
                match["confidence"] = "high"
            if match.get("coverage") == "none":
                rejected_candidates.append(match)
            else:
                verified_matches.append(match)
        verified_rejected = []
        for match in rejected_candidates:
            if not isinstance(match, dict) or str(match.get("agent_id") or "") not in agents:
                continue
            agent = agents[str(match["agent_id"])]
            if match.get("confidence") not in {"high", "medium", "low"}:
                match["confidence"] = "low"
            match.update(
                {
                    "coverage": "none",
                    "executable": agent["executable"],
                    "validation_verdict": agent["validation_verdict"],
                }
            )
            verified_rejected.append(match)
        coverage_rank = {"full": 0, "partial": 1}
        confidence_rank = {"high": 0, "medium": 1, "low": 2}
        result["agent_matches"] = sorted(
            _deduplicate_matches(verified_matches),
            key=lambda item: (
                coverage_rank.get(str(item.get("coverage")), 9),
                confidence_rank.get(str(item.get("confidence")), 9),
                str(item.get("operation_id") or ""),
                str(item.get("agent_id") or ""),
            ),
        )
        result["rejected_candidates"] = _deduplicate_matches(verified_rejected)
        if not (
            evaluation["agent_catalog_complete"] and evaluation["matching_complete"]
        ):
            result["agent_gaps"] = []
            result["workflow_suggestions"] = []
        else:
            fully_covered = {
                str(item.get("operation_id") or "")
                for item in result["agent_matches"]
                if item.get("coverage") == "full"
            }
            result["agent_gaps"] = [
                gap
                for gap in result["agent_gaps"]
                if not {
                    str(item) for item in gap.get("operation_ids") or []
                }.issubset(fully_covered)
            ]
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
        return {
            **{key: value for key, value in document.items() if key not in {"cache_path"}},
            "source_type": _source_type(document),
        }

    @staticmethod
    def _public_issue(issue: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in issue.items() if key != "path"}


def _digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _source_type(document: dict[str, Any]) -> str:
    return (
        "user_description"
        if str(document.get("extension") or "") == USER_DESCRIPTION_EXTENSION
        else "document"
    )


def _compact_schema_ports(schema: Any) -> list[dict[str, Any]]:
    if not isinstance(schema, dict):
        return []
    required = {str(item) for item in schema.get("required") or []}
    ports: list[dict[str, Any]] = []
    for name, raw in (schema.get("properties") or {}).items():
        if not isinstance(raw, dict):
            continue
        item: dict[str, Any] = {
            "name": str(name),
            "type": str(raw.get("type") or "object"),
            "required": str(name) in required,
        }
        for key in ("title", "description", "enum", "const", "minItems", "maxItems"):
            if key in raw:
                item[key] = deepcopy(raw[key])
        if isinstance(raw.get("items"), dict):
            nested = raw["items"]
            # Runtime matching needs the port kind and cardinality, not the
            # complete nested business object.  The authoritative full Schema
            # remains server-side for compiler and safety validation.
            item["item_type"] = str(nested.get("type") or "object")
        ports.append(item)
    branches = []
    for branch in schema.get("oneOf") or []:
        if isinstance(branch, dict):
            branches.append(
                {
                    "required": [str(item) for item in branch.get("required") or []],
                    "properties": sorted(str(item) for item in (branch.get("properties") or {})),
                }
            )
    if branches:
        ports.append({"name": "$oneOf", "type": "branch_contract", "branches": branches})
    return ports


def _execution_capability_signals(execution: Any) -> list[str]:
    if not isinstance(execution, dict):
        return []
    serialized = json.dumps(execution, ensure_ascii=False).lower()
    signals = []
    if any(
        token in serialized
        for token in (
            "actualgoodsmovementdate", "overallgoodsmovementstatus",
            "goodsmovementstatus",
        )
    ):
        signals.append("pgi_status")
    return signals


def _operation_capability_signals(operation: Any) -> set[str]:
    if not isinstance(operation, dict):
        return set()
    text = " ".join(
        [
            str(operation.get("name") or ""),
            str(operation.get("description") or ""),
            *[str(item) for item in operation.get("outputs") or []],
        ]
    ).lower()
    signals = set()
    if any(token in text for token in ("pgi", "goods issue", "goods movement", "发货过账", "出库过账")):
        signals.add("pgi_status")
    return signals


def _gap_matches_signals(value: str, signals: set[str]) -> bool:
    text = value.lower()
    if "pgi_status" in signals and any(
        token in text for token in ("pgi", "goods issue", "goods movement", "发货过账", "出库过账")
    ):
        return True
    return False


def _paginate_runtime_catalog(
    items: list[dict[str, Any]], *, digest: str, max_chars: int
) -> list[dict[str, Any]]:
    if max_chars < 1:
        raise RoleMatchingError("Agent catalog page limit must be positive.")
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for item in items:
        candidate = [*current, item]
        probe = {
            "catalog_digest": digest,
            "page_index": 9999,
            "page_count": 9999,
            "total_agent_count": len(items),
            "items": candidate,
        }
        encoded = json.dumps(probe, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) <= max_chars:
            current = candidate
            continue
        if not current:
            raise RoleMatchingError(
                f"Agent {item.get('agent_id')} cannot fit in a bounded Runtime catalog page.",
                code="role_matching_catalog_item_too_large",
            )
        groups.append(current)
        current = [item]
    if current:
        groups.append(current)
    page_count = len(groups)
    return [
        {
            "catalog_digest": digest,
            "page_index": index,
            "page_count": page_count,
            "total_agent_count": len(items),
            "items": group,
        }
        for index, group in enumerate(groups, start=1)
    ]


def _validate_catalog_evaluation(
    value: Any, catalog: dict[str, Any], *, operation_count: int
) -> dict[str, Any]:
    runtime_catalog = catalog.get("runtime_catalog") or {}
    expected_ids = {
        str(item.get("agent_id") or "")
        for item in catalog.get("items") or []
        if item.get("agent_id")
    }
    raw = value if isinstance(value, dict) else {}
    evaluated_ids = {
        str(item) for item in raw.get("evaluated_agent_ids") or [] if str(item) in expected_ids
    }
    failed_pages = sorted(
        {
            int(item)
            for item in raw.get("failed_pages") or []
            if isinstance(item, int) or (isinstance(item, str) and item.isdigit())
        }
    )
    digest_matches = str(raw.get("catalog_digest") or "") == str(catalog.get("digest") or "")
    page_count = int(runtime_catalog.get("page_count") or 0)
    reported_pages = int(raw.get("catalog_page_count") or 0)
    expected_pair_count = operation_count * len(expected_ids)
    evaluated_pair_count = int(raw.get("evaluated_pair_count") or 0)
    complete = bool(
        raw.get("agent_catalog_complete")
        and digest_matches
        and not failed_pages
        and reported_pages == page_count
        and evaluated_ids == expected_ids
        and evaluated_pair_count == expected_pair_count
    )
    matching_complete = bool(complete and raw.get("matching_complete"))
    return {
        "catalog_digest": str(catalog.get("digest") or ""),
        "total_agent_count": len(expected_ids),
        "evaluated_agent_count": len(evaluated_ids),
        "evaluated_pair_count": evaluated_pair_count,
        "expected_pair_count": expected_pair_count,
        "catalog_page_count": page_count,
        "agent_catalog_complete": complete,
        "matching_complete": matching_complete,
        "consolidation_complete": bool(
            matching_complete and raw.get("consolidation_complete")
        ),
        "failed_pages": failed_pages,
        "evaluated_agent_ids": sorted(evaluated_ids),
    }


def _deduplicate_matches(values: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("operation_id") or ""), str(item.get("agent_id") or ""))
        if not all(key) or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _change_summary(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    identifiers = {
        "roles": ("role_id", "name"), "processes": ("process_id", "name"),
        "operations": ("operation_id",), "agent_matches": ("operation_id", "agent_id"),
        "rejected_candidates": ("operation_id", "agent_id"),
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
