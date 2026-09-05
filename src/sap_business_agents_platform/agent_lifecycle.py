from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .config import Settings
from .database import RunStore
from .managed_rules import ManagedRuleError, validate_managed_rule
from .manifests import AgentRepository, ManifestError, is_agent_executable, validate_execution
from .models import RunStatus, TERMINAL_STATUSES, utc_now
from .acceptance import agent_execution_digest
from .workflows import WorkflowRepository, agent_digest


class AgentLifecycleError(RuntimeError):
    def __init__(self, message: str, *, code: str = "agent_management_failed", detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class AgentLifecycleService:
    """Author, validate and publish immutable deterministic Agent packages."""

    def __init__(
        self,
        settings: Settings,
        store: RunStore,
        agents: AgentRepository,
        workflows: WorkflowRepository,
        coordinator: Any,
        runtime: Any,
        legacy_factory: Any | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.agents = agents
        self.workflows = workflows
        self.coordinator = coordinator
        self.runtime = runtime
        self.legacy_factory = legacy_factory
        self.draft_root = (settings.draft_root / "agents").resolve()

    def catalog(self, state: str = "all") -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for agent in self.agents.list_all():
            if agent.get("kind") == "platform_assistant" or not isinstance(agent.get("execution"), dict):
                continue
            agent_id = str(agent.get("slug") or "")
            lifecycle = self.agents.lifecycle(agent_id)
            if state != "all" and lifecycle["state"] != state:
                continue
            dependencies = self._workflow_dependencies(agent_id)
            versions = self.versions(agent_id)
            items.append(
                {
                    "id": agent_id,
                    "module": agent.get("module"),
                    "title": agent.get("title"),
                    "summary": agent.get("summary"),
                    "version": agent.get("version"),
                    "digest": agent_digest(agent),
                    "validation": copy.deepcopy(agent.get("validation") or {}),
                    "lifecycle": lifecycle,
                    "version_count": len(versions),
                    "workflow_dependencies": dependencies,
                    "management": self._management_capabilities(agent_id, lifecycle, dependencies),
                }
            )
        return items

    def versions(self, agent_id: str) -> list[dict[str, Any]]:
        current = self.agents.get(agent_id)
        directory = self.agents._path(agent_id).parent
        result = [self._version_summary(current, directory, current=True)]
        for path in sorted((directory / "versions").glob("*/agent.json")):
            manifest = self._read_json(path)
            if str(manifest.get("version") or "") == str(current.get("version") or ""):
                continue
            result.append(self._version_summary(manifest, path.parent, current=False))
        return sorted(result, key=lambda item: _semver_key(item["version"]), reverse=True)

    def version(self, agent_id: str, version: str) -> dict[str, Any]:
        manifest = self.agents.get_version(agent_id, version)
        package = self.agents.package(agent_id, version)
        return {
            "manifest": manifest,
            "readme": self._read_optional(Path(package["directory"]) / "README.md"),
            "rules": package.get("rules_source"),
            "digest": agent_digest(manifest),
            "current": str(self.agents.get(agent_id).get("version")) == version,
        }

    async def create(self, payload: Any) -> dict[str, Any]:
        source = str(payload.source)
        package: dict[str, Any]
        source_version: str | None = None
        source_hash: str | None = None
        if source == "clone":
            source_agent = self.agents.get(str(payload.source_agent_id))
            self._assert_manageable(source_agent)
            package = self._capture_package(Path(self.agents.package(str(payload.source_agent_id))["directory"]))
            source_version = str(source_agent.get("version") or "")
            source_hash = agent_digest(source_agent)
            new_id = str(payload.agent_id or f"{payload.source_agent_id}-copy")
            package["manifest"]["slug"] = new_id
            package["manifest"]["version"] = "0.1.0"
            package["manifest"]["status"] = "Draft"
            package["manifest"]["validation"] = _not_tested_validation()
        elif source == "free_query":
            if self.legacy_factory is None:
                raise AgentLifecycleError("Agent Factory is unavailable.", code="agent_factory_unavailable")
            generated = await self.legacy_factory.create_from_run(str(payload.run_id))
            package = self._capture_package(Path(generated.path))
            if payload.agent_id:
                package["manifest"]["slug"] = str(payload.agent_id)
        else:
            package = self._blank_package(
                str(payload.agent_id or f"agent-{uuid.uuid4().hex[:8]}"),
                str(payload.module or "Common"),
                payload.title or {"zh": "新固定Agent", "en": "New Fixed Agent"},
            )
            if source == "workflow_gap":
                package["manifest"].setdefault("authoring", {})["workflowGap"] = {
                    "workflowDraftId": payload.workflow_draft_id,
                    "gapId": payload.gap_id,
                }
        self._assert_manageable(package["manifest"])
        draft_id = f"agent_draft_{uuid.uuid4().hex[:16]}"
        path = self._draft_path(draft_id)
        path.mkdir(parents=True, exist_ok=False)
        self._write_package(path, package)
        now = utc_now()
        draft = {
            "draft_id": draft_id,
            "agent_id": str(package["manifest"]["slug"]),
            "source_type": source,
            "status": "draft",
            "revision": 1,
            "path": str(path),
            "thread_id": None,
            "source_version": source_version,
            "source_hash": source_hash,
            "target_version": str(package["manifest"].get("version") or "0.1.0"),
            "risk_class": "behavior_change",
            "validation_run_id": None,
            "validation": {},
            "metadata": {
                "origin": {
                    "sourceAgentId": payload.source_agent_id,
                    "runId": payload.run_id,
                    "workflowDraftId": payload.workflow_draft_id,
                    "gapId": payload.gap_id,
                }
            },
            "created_at": now,
            "updated_at": now,
        }
        self.store.save_agent_authoring_draft(draft, package=package, diff=[])
        self.store.save_agent_conversation_turn(
            {
                "draft_id": draft_id,
                "turn": 1,
                "parent_turn": None,
                "kind": "initial",
                "status": "completed",
                "decision": {"source": source},
                "base_revision": None,
                "result_revision": 1,
                "diff": [],
                "created_at": now,
                "completed_at": now,
            }
        )
        return self.get_draft(draft_id)

    def create_version_draft(
        self,
        agent_id: str,
        *,
        bump: str,
        expected_version: str,
        expected_hash: str,
    ) -> dict[str, Any]:
        current = self._assert_expected(agent_id, expected_version, expected_hash)
        self._assert_manageable(current)
        self._assert_no_open_draft(agent_id)
        target = _bump_semver(expected_version, bump)
        if any(item["version"] == target for item in self.versions(agent_id)):
            raise AgentLifecycleError("Agent version already exists.", code="agent_version_exists")
        package = self._capture_package(Path(self.agents.package(agent_id)["directory"]))
        package["manifest"]["version"] = target
        draft_id = f"agent_draft_{uuid.uuid4().hex[:16]}"
        path = self._draft_path(draft_id)
        path.mkdir(parents=True, exist_ok=False)
        self._write_package(path, package)
        now = utc_now()
        draft = {
            "draft_id": draft_id,
            "agent_id": agent_id,
            "source_type": "clone",
            "status": "draft",
            "revision": 1,
            "path": str(path),
            "thread_id": None,
            "source_version": expected_version,
            "source_hash": expected_hash,
            "target_version": target,
            "risk_class": "metadata_only",
            "validation_run_id": None,
            "validation": {},
            "metadata": {"version_origin": {"bump": bump}},
            "created_at": now,
            "updated_at": now,
        }
        self.store.save_agent_authoring_draft(draft, package=package, diff=[])
        return self.get_draft(draft_id)

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        revision = self.store.get_agent_authoring_revision(draft_id, int(draft["revision"]))
        return {
            **draft,
            "package": revision["package"],
            "diff": revision["diff"],
            "revisions": self.store.list_agent_authoring_revisions(draft_id),
            "conversation": self.store.list_agent_conversation_turns(draft_id),
        }

    def list_drafts(self) -> list[dict[str, Any]]:
        return self.store.list_agent_authoring_drafts()

    def update(self, draft_id: str, payload: Any) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        self._assert_editable(draft)
        if int(payload.expected_revision) != int(draft["revision"]):
            raise AgentLifecycleError("Agent draft revision changed.", code="agent_draft_conflict")
        previous = self.store.get_agent_authoring_revision(draft_id, int(draft["revision"]))["package"]
        package = copy.deepcopy(previous)
        if payload.manifest is not None:
            package["manifest"] = copy.deepcopy(payload.manifest)
        if payload.readme is not None:
            package["readme"] = str(payload.readme)
        if payload.rules is not None:
            package["rules"] = str(payload.rules) or None
        if str(package["manifest"].get("slug") or "") != draft["agent_id"]:
            raise AgentLifecycleError("Agent ID cannot change inside a version draft.", code="agent_id_immutable")
        self._assert_manageable(package["manifest"])
        new_revision = int(draft["revision"]) + 1
        diff = _package_diff(previous, package)
        risk = self._risk_class(draft, package)
        package["manifest"]["version"] = draft.get("target_version") or package["manifest"].get("version")
        self._write_package(Path(draft["path"]), package)
        draft.update(
            status="draft",
            revision=new_revision,
            risk_class=risk,
            validation_run_id=None,
            validation={},
            updated_at=utc_now(),
        )
        self.store.save_agent_authoring_draft(draft, package=package, diff=diff)
        self.store.save_agent_conversation_turn(
            {
                "draft_id": draft_id,
                "turn": len(self.store.list_agent_conversation_turns(draft_id)) + 1,
                "parent_turn": None,
                "kind": "manual_edit",
                "status": "completed",
                "decision": {"risk_class": risk},
                "base_revision": new_revision - 1,
                "result_revision": new_revision,
                "diff": diff,
                "completed_at": utc_now(),
            }
        )
        return self.get_draft(draft_id)

    async def feedback(self, draft_id: str, payload: Any) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        self._assert_editable(draft)
        if int(payload.base_revision) != int(draft["revision"]):
            raise AgentLifecycleError("Agent draft revision changed.", code="agent_draft_conflict")
        supports = getattr(self.runtime, "supports", None)
        if callable(supports) and not supports("review_agent_feedback"):
            raise AgentLifecycleError(
                "The selected Agent Runtime does not support Agent revision conversations.",
                code="runtime_agent_feedback_unavailable",
            )
        current = self.store.get_agent_authoring_revision(draft_id, int(draft["revision"]))["package"]
        try:
            decision = await self.runtime.review_agent_feedback(
                feedback=str(payload.feedback),
                locale=str(payload.locale),
                package=current,
                thread_id=draft.get("thread_id"),
            )
        except Exception as exc:
            raise AgentLifecycleError(
                "Agent Runtime could not produce a safe Agent revision.",
                code="runtime_agent_feedback_failed",
                detail={"message": str(exc)},
            ) from exc
        package = decision.get("package")
        if not isinstance(package, dict) or not isinstance(package.get("manifest"), dict):
            raise AgentLifecycleError("Runtime returned an invalid Agent package.", code="runtime_agent_feedback_invalid")
        update = type("Update", (), {
            "expected_revision": draft["revision"],
            "manifest": package["manifest"],
            "readme": package.get("readme", current.get("readme")),
            "rules": package.get("rules", current.get("rules")),
        })()
        result = self.update(draft_id, update)
        refreshed = self.store.get_agent_authoring_draft(draft_id)
        refreshed["thread_id"] = decision.get("thread_id") or draft.get("thread_id")
        refreshed["metadata"] = {
            **(refreshed.get("metadata") or {}),
            "last_runtime_decision": {
                "summary": decision.get("summary"),
                "required_changes": decision.get("required_changes") or [],
            },
        }
        self.store.save_agent_authoring_draft(refreshed)
        return self.get_draft(draft_id)

    def undo(self, draft_id: str, *, expected_revision: int, target_revision: int) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        if int(draft["revision"]) != expected_revision:
            raise AgentLifecycleError("Agent draft revision changed.", code="agent_draft_conflict")
        package = self.store.get_agent_authoring_revision(draft_id, target_revision)["package"]
        update = type("Update", (), {
            "expected_revision": expected_revision,
            "manifest": package["manifest"],
            "readme": package.get("readme"),
            "rules": package.get("rules"),
        })()
        result = self.update(draft_id, update)
        turns = self.store.list_agent_conversation_turns(draft_id)
        self.store.save_agent_conversation_turn(
            {
                "draft_id": draft_id,
                "turn": len(turns) + 1,
                "kind": "undo",
                "status": "completed",
                "decision": {"target_revision": target_revision},
                "base_revision": expected_revision,
                "result_revision": result["revision"],
                "diff": result["diff"],
                "completed_at": utc_now(),
            }
        )
        return self.get_draft(draft_id)

    def validate(self, draft_id: str) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        revision = self.store.get_agent_authoring_revision(draft_id, int(draft["revision"]))
        package = revision["package"]
        manifest = package["manifest"]
        checks: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        try:
            self._assert_manageable(manifest)
            validate_execution(manifest, f"agent-draft:{draft_id}")
            checks.append({"code": "agent_schema_valid", "status": "pass"})
        except (ManifestError, AgentLifecycleError) as exc:
            errors.append({"code": "agent_schema_invalid", "message": str(exc)})
        rules_source = package.get("rules")
        if rules_source:
            try:
                validate_managed_rule(
                    str(rules_source),
                    expected_digest=(manifest.get("managedRule") or {}).get("sha256"),
                )
                checks.append({"code": "managed_rule_safe", "status": "pass"})
            except ManagedRuleError as exc:
                errors.append({"code": exc.code, "message": str(exc)})
        risk = self._risk_class(draft, package)
        report: dict[str, Any] = {
            "revision": draft["revision"],
            "risk_class": risk,
            "checks": checks,
            "errors": errors,
            "sap_get_count": 0,
            "validated_at": utc_now(),
        }
        if not errors and risk == "metadata_only":
            source = self.agents.get(str(draft["agent_id"]))
            if not is_agent_executable(source):
                errors.append({"code": "source_acceptance_unavailable", "message": "The source version has no reusable PASS acceptance."})
            else:
                report.update(
                    verdict="PASS",
                    reused_validation=True,
                    source_validation=copy.deepcopy(source.get("validation") or {}),
                    execution_digest=_execution_digest(manifest, package.get("rules")),
                )
        if errors:
            report["verdict"] = "FAIL"
            status = "invalid"
        elif risk == "behavior_change":
            report["verdict"] = "NOT_TESTED"
            report["requires_live_validation"] = True
            status = "validated"
        else:
            status = "validated"
        draft.update(status=status, risk_class=risk, validation=report, updated_at=utc_now())
        self.store.save_agent_authoring_draft(draft)
        return self.get_draft(draft_id)

    async def live_validate(self, draft_id: str, *, input_value: dict[str, Any], auto_discover: bool) -> dict[str, Any]:
        validated = self.validate(draft_id)
        if validated["status"] == "invalid":
            raise AgentLifecycleError("Static Agent validation failed.", code="agent_static_validation_failed")
        draft = self.store.get_agent_authoring_draft(draft_id)
        package = self.store.get_agent_authoring_revision(draft_id, int(draft["revision"]))["package"]
        effective_input = copy.deepcopy(input_value)
        if auto_discover and not effective_input:
            effective_input = copy.deepcopy(
                ((package["manifest"].get("execution") or {}).get("acceptance") or {}).get("inputDefaults")
                or {}
            )
        run_id = await self.coordinator.submit_agent_snapshot(
            package["manifest"],
            effective_input,
            rules_source=package.get("rules"),
            draft_id=draft_id,
            revision=int(draft["revision"]),
        )
        report = {
            "run_id": run_id,
            "revision": draft["revision"],
            "status": "running",
            "verdict": "pending",
            "automatic_checks": (draft.get("validation") or {}).get("checks") or [],
            "sample_source": "auto_discovered" if auto_discover else "user",
            "normalized_input": effective_input,
            "started_at": utc_now(),
        }
        self.store.save_agent_validation_attempt(
            draft_id=draft_id,
            run_id=run_id,
            revision=int(draft["revision"]),
            report=report,
            report_digest=None,
        )
        draft.update(status="validating", validation_run_id=run_id, validation=report, updated_at=utc_now())
        self.store.save_agent_authoring_draft(draft)
        return report

    def validation_report(self, draft_id: str) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        run_id = draft.get("validation_run_id")
        if not run_id:
            return copy.deepcopy(draft.get("validation") or {})
        attempt = self.store.get_agent_validation_attempt(draft_id, run_id)
        run = self.store.get_run(run_id)
        if run.status not in TERMINAL_STATUSES:
            return {**attempt["report"], "status": "running", "progress": run.progress.model_dump(mode="json")}
        if attempt["completed_at"]:
            return attempt["report"]
        result = run.result
        tool_read_only = bool(result) and all(
            str(call.get("http_method") or "GET").upper() == "GET"
            for call in (result.tool_calls or [])
        )
        schema_complete = bool(result and result.workflow_output is not None)
        complete = bool(
            result
            and result.completeness.source_complete
            and result.completeness.business_complete
        )
        verdict = "PASS" if run.status == RunStatus.completed and tool_read_only and schema_complete and complete else "INCONCLUSIVE" if run.status == RunStatus.inconclusive else "FAIL"
        report = {
            **attempt["report"],
            "status": "completed",
            "verdict": verdict,
            "completed_at": run.completed_at,
            "fixedAgentComparison": "MATCH" if verdict == "PASS" else "BLOCKED",
            "read_only_audit": tool_read_only,
            "output_schema_valid": schema_complete,
            "source_complete": bool(result and result.completeness.source_complete),
            "evidence_complete": bool(result and result.completeness.business_complete),
            "errors": copy.deepcopy(run.error or (result.errors if result else [])),
        }
        digest = _json_digest(report)
        self.store.save_agent_validation_attempt(
            draft_id=draft_id,
            run_id=run_id,
            revision=int(draft["revision"]),
            report=report,
            report_digest=digest,
            completed_at=run.completed_at or utc_now(),
        )
        draft.update(status="validated" if verdict == "PASS" else "needs_review", validation=report, updated_at=utc_now())
        self.store.save_agent_authoring_draft(draft)
        return {**report, "report_digest": digest}

    def publish(self, draft_id: str, payload: Any) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        package = self.store.get_agent_authoring_revision(draft_id, int(draft["revision"]))["package"]
        manifest = package["manifest"]
        report = self.validation_report(draft_id)
        if report.get("verdict") != "PASS":
            raise AgentLifecycleError("Only a PASS Agent version can be published.", code="agent_validation_pass_required")
        if payload.validation_report_digest and report.get("report_digest") != payload.validation_report_digest:
            raise AgentLifecycleError("Agent validation report changed.", code="agent_validation_report_conflict")
        minimum = self._minimum_bump(draft, package)
        target_version = str(payload.target_version or draft.get("target_version") or manifest.get("version") or "0.1.0")
        base_version = draft.get("source_version")
        if base_version and _bump_rank(_bump_kind(base_version, target_version)) < _bump_rank(minimum):
            raise AgentLifecycleError(
                f"This change requires at least a {minimum} version bump.",
                code="agent_version_bump_too_low",
                detail={"minimum": minimum, "target_version": target_version},
            )
        manifest["version"] = target_version
        manifest["validation"] = {
            "verdict": "PASS",
            "executable": True,
            "acceptanceMode": "three_stage" if draft["risk_class"] == "behavior_change" else "deterministic_runtime",
            "fixedAgentComparison": "MATCH",
            "freeQueryComparison": (report.get("source_validation") or {}).get("freeQueryComparison", "MATCH"),
            "validated_at": report.get("completed_at") or report.get("validated_at") or utc_now(),
        }
        branch = self._prepare_branch(draft["agent_id"], "publish", target_version)
        agent_dir = self.settings.repository_root / "agents" / str(manifest.get("module") or "Common") / draft["agent_id"]
        existing_dir = self._existing_directory(draft["agent_id"])
        if existing_dir is not None:
            agent_dir = existing_dir
        agent_dir.mkdir(parents=True, exist_ok=True)
        version_dir = agent_dir / "versions" / target_version
        if version_dir.exists():
            raise AgentLifecycleError("Agent version already exists.", code="agent_version_exists")
        self._write_package(version_dir, package, manifest_override=manifest)
        self._write_json(version_dir / "validation.json", report)
        lifecycle = self._current_lifecycle_or_default(draft["agent_id"], manifest, agent_dir)
        lifecycle.update(
            schemaVersion=1,
            agent_id=draft["agent_id"],
            latest_version=target_version,
            published_at=utc_now(),
            git_branch=branch,
            git_commit="recorded_in_agent_management_events",
        )
        if bool(payload.activate):
            self._activate_package(agent_dir, version_dir, lifecycle)
            lifecycle.update(
                lifecycle_state="active",
                state="active",
                active_version=target_version,
                active_digest=agent_digest(manifest),
                activated_at=utc_now(),
                deactivated_at=None,
            )
        else:
            lifecycle.setdefault("lifecycle_state", "inactive" if existing_dir is None else lifecycle.get("state", "active"))
            lifecycle["state"] = lifecycle["lifecycle_state"]
            if existing_dir is None:
                # Keep the package discoverable by the management catalog but not runnable.
                self._activate_package(agent_dir, version_dir, lifecycle, archive_current=False)
                lifecycle.update(active_version=None, active_digest=None, lifecycle_state="inactive", state="inactive")
        self._write_json(agent_dir / "publication.json", lifecycle)
        commit_sha = self._commit_agent_change(agent_dir, f"Publish {draft['agent_id']} v{target_version}")
        self._audit(draft["agent_id"], "published", draft.get("source_version"), target_version, agent_digest(manifest), branch, commit_sha, {"activated": bool(payload.activate)})
        draft.update(status="published", target_version=target_version, validation={**report, "branch": branch, "commit_sha": commit_sha}, updated_at=utc_now())
        self.store.save_agent_authoring_draft(draft)
        reload_scheduled = self._schedule_service_refresh() if payload.activate else False
        return {"agent_id": draft["agent_id"], "version": target_version, "active": bool(payload.activate), "branch": branch, "commit_sha": commit_sha, "pushed": False, "reload_scheduled": reload_scheduled}

    def deactivate(self, agent_id: str, payload: Any) -> dict[str, Any]:
        manifest = self._assert_expected(agent_id, payload.expected_version, payload.expected_agent_hash)
        lifecycle = self.agents.lifecycle(agent_id)
        if lifecycle["state"] != "active":
            raise AgentLifecycleError("Agent is already inactive.", code="agent_already_inactive")
        branch = self._prepare_branch(agent_id, "deactivate", payload.expected_version)
        lifecycle.update(lifecycle_state="inactive", state="inactive", deactivated_at=utc_now(), deactivation_reason=payload.reason, git_branch=branch)
        directory = self.agents._path(agent_id).parent
        self._write_json(directory / "publication.json", lifecycle)
        commit = self._commit_agent_change(directory, f"Deactivate {agent_id}")
        self._audit(agent_id, "deactivated", payload.expected_version, payload.expected_version, agent_digest(manifest), branch, commit, {"reason": payload.reason})
        return {"agent_id": agent_id, "state": "inactive", "branch": branch, "commit_sha": commit, "pushed": False}

    def activate(self, agent_id: str, payload: Any) -> dict[str, Any]:
        current = self._assert_expected(agent_id, payload.expected_version, payload.expected_agent_hash)
        lifecycle = self.agents.lifecycle(agent_id)
        version = str(payload.version or lifecycle.get("latest_version") or payload.expected_version)
        candidate = self.agents.get_version(agent_id, version)
        if not is_agent_executable(candidate):
            raise AgentLifecycleError("Only a PASS Agent version can be activated.", code="agent_validation_pass_required")
        branch = self._prepare_branch(agent_id, "activate", version)
        directory = self.agents._path(agent_id).parent
        version_dir = directory / "versions" / version
        if version != str(current.get("version")):
            self._activate_package(directory, version_dir, lifecycle)
        lifecycle.update(lifecycle_state="active", state="active", active_version=version, active_digest=agent_digest(candidate), activated_at=utc_now(), deactivated_at=None, git_branch=branch)
        self._write_json(directory / "publication.json", lifecycle)
        commit = self._commit_agent_change(directory, f"Activate {agent_id} v{version}")
        self._audit(agent_id, "activated", str(current.get("version")), version, agent_digest(candidate), branch, commit, {"reason": payload.reason})
        reload_scheduled = self._schedule_service_refresh()
        return {"agent_id": agent_id, "state": "active", "version": version, "branch": branch, "commit_sha": commit, "pushed": False, "reload_required": not reload_scheduled, "reload_scheduled": reload_scheduled}

    def rollback(self, agent_id: str, payload: Any) -> dict[str, Any]:
        if not payload.version:
            raise AgentLifecycleError("Rollback requires a target version.", code="agent_rollback_version_required")
        return self.activate(agent_id, payload)

    def delete(self, agent_id: str, payload: Any) -> dict[str, Any]:
        self._assert_expected(agent_id, payload.expected_version, payload.expected_agent_hash)
        if payload.confirm_agent_id != agent_id:
            raise AgentLifecycleError("Agent ID confirmation does not match.", code="agent_delete_confirmation_mismatch")
        blockers = self._delete_blockers(agent_id)
        if blockers:
            raise AgentLifecycleError("Agent does not meet permanent deletion requirements.", code="agent_delete_blocked", detail={"blockers": blockers})
        directory = self.agents._path(agent_id).parent.resolve()
        agents_root = (self.settings.repository_root / "agents").resolve()
        if agents_root not in directory.parents:
            raise AgentLifecycleError("Agent deletion target escaped the Agent root.", code="agent_delete_path_invalid")
        branch = self._prepare_branch(agent_id, "delete", payload.expected_version)
        shutil.rmtree(directory)
        commit = self._commit_agent_change(directory, f"Delete {agent_id}", deleted=True)
        self._audit(agent_id, "deleted", payload.expected_version, None, payload.expected_agent_hash, branch, commit, {"git_history_recoverable": True})
        return {"agent_id": agent_id, "deleted": True, "branch": branch, "commit_sha": commit, "recoverable_from_git": True, "pushed": False}

    def _management_capabilities(self, agent_id: str, lifecycle: dict[str, Any], dependencies: list[dict[str, Any]]) -> dict[str, Any]:
        blockers = self._delete_blockers(agent_id, dependencies=dependencies)
        return {
            "can_create_version": not self._open_drafts(agent_id),
            "can_deactivate": lifecycle["state"] == "active",
            "can_activate": lifecycle["state"] == "inactive",
            "can_rollback": len(self.versions(agent_id)) > 1,
            "can_delete": not blockers,
            "delete_blockers": blockers,
        }

    def _delete_blockers(self, agent_id: str, *, dependencies: list[dict[str, Any]] | None = None) -> list[str]:
        blockers: list[str] = []
        try:
            if self.agents.lifecycle(agent_id)["state"] != "inactive":
                blockers.append("agent_must_be_inactive")
        except KeyError:
            blockers.append("agent_not_found")
        if self.store.count_open_agent_runs(agent_id):
            blockers.append("agent_has_active_runs")
        if self._open_drafts(agent_id):
            blockers.append("agent_has_active_drafts")
        if dependencies if dependencies is not None else self._workflow_dependencies(agent_id):
            blockers.append("agent_is_referenced_by_workflow")
        return blockers

    def _workflow_dependencies(self, agent_id: str) -> list[dict[str, Any]]:
        dependencies: list[dict[str, Any]] = []
        root = self.settings.repository_root / "workflows"
        for path in root.glob("*/*/workflow.json"):
            try:
                workflow = self._read_json(path)
            except AgentLifecycleError:
                continue
            for node in workflow.get("nodes") or []:
                if isinstance(node, dict) and node.get("agentId") == agent_id:
                    dependencies.append({"workflow_id": workflow.get("id"), "version": workflow.get("version"), "agent_version": node.get("agentVersion")})
        return dependencies

    def _risk_class(self, draft: dict[str, Any], package: dict[str, Any]) -> str:
        if not draft.get("source_version"):
            return "behavior_change"
        try:
            source = self.agents.package(draft["agent_id"], draft["source_version"], draft.get("source_hash"))
        except (KeyError, ManifestError):
            return "behavior_change"
        return "metadata_only" if _execution_digest(source["manifest"], source.get("rules_source")) == _execution_digest(package["manifest"], package.get("rules")) else "behavior_change"

    def _minimum_bump(self, draft: dict[str, Any], package: dict[str, Any]) -> str:
        if not draft.get("source_version"):
            return "minor"
        try:
            source = self.agents.get_version(draft["agent_id"], draft["source_version"], draft.get("source_hash"))
        except (KeyError, ManifestError):
            return "major"
        old_execution = source.get("execution") or {}
        new_execution = package["manifest"].get("execution") or {}
        if _breaking_schema_change(old_execution.get("inputSchema") or {}, new_execution.get("inputSchema") or {}) or _breaking_schema_change(old_execution.get("outputSchema") or {}, new_execution.get("outputSchema") or {}):
            return "major"
        return "patch" if self._risk_class(draft, package) == "metadata_only" else "minor"

    def _assert_manageable(self, manifest: dict[str, Any]) -> None:
        if manifest.get("kind") == "platform_assistant" or not isinstance(manifest.get("execution"), dict):
            raise AgentLifecycleError("Platform assistants are maintained by platform code and cannot be managed here.", code="platform_assistant_not_manageable")

    def _assert_expected(self, agent_id: str, version: str, digest: str) -> dict[str, Any]:
        manifest = self.agents.get(agent_id)
        actual_version = str(manifest.get("version") or "")
        actual_digest = agent_digest(manifest)
        if version != actual_version or digest != actual_digest:
            raise AgentLifecycleError("The Agent changed; reload before continuing.", code="agent_management_conflict", detail={"actual_version": actual_version, "actual_digest": actual_digest})
        return manifest

    def _assert_no_open_draft(self, agent_id: str) -> None:
        drafts = self._open_drafts(agent_id)
        if drafts:
            raise AgentLifecycleError("An unfinished Agent draft already exists.", code="agent_draft_exists", detail={"draft_ids": drafts})

    def _open_drafts(self, agent_id: str) -> list[str]:
        return [item["draft_id"] for item in self.store.list_agent_authoring_drafts() if item["agent_id"] == agent_id and item["status"] not in {"published", "cancelled"}]

    @staticmethod
    def _assert_editable(draft: dict[str, Any]) -> None:
        if draft["status"] == "published":
            raise AgentLifecycleError("A published Agent draft is immutable.", code="agent_draft_published")

    def _draft_path(self, draft_id: str) -> Path:
        path = (self.draft_root / draft_id).resolve()
        if self.draft_root not in path.parents:
            raise AgentLifecycleError("Agent draft path escaped the draft root.", code="agent_draft_path_invalid")
        return path

    def _existing_directory(self, agent_id: str) -> Path | None:
        try:
            return self.agents._path(agent_id).parent
        except KeyError:
            return None

    def _current_lifecycle_or_default(self, agent_id: str, manifest: dict[str, Any], directory: Path) -> dict[str, Any]:
        try:
            return copy.deepcopy(self.agents.lifecycle(agent_id))
        except KeyError:
            return {"schemaVersion": 1, "agent_id": agent_id, "lifecycle_state": "inactive", "state": "inactive", "active_version": None, "latest_version": str(manifest.get("version") or ""), "active_digest": None}

    def _activate_package(self, directory: Path, version_dir: Path, lifecycle: dict[str, Any], *, archive_current: bool = True) -> None:
        current = directory / "agent.json"
        if archive_current and current.is_file():
            manifest = self._read_json(current)
            version = str(manifest.get("version") or "")
            archive = directory / "versions" / version
            if not (archive / "agent.json").is_file():
                self._copy_current_package(directory, archive)
        for path in list(directory.iterdir()) if directory.exists() else []:
            if path.name in {"versions", "publication.json"}:
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        for source in version_dir.rglob("*"):
            if source.is_dir():
                continue
            target = directory / source.relative_to(version_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    @staticmethod
    def _copy_current_package(directory: Path, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        for source in directory.rglob("*"):
            if source.is_dir() or "versions" in source.relative_to(directory).parts or source.name == "publication.json":
                continue
            destination = target / source.relative_to(directory)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    def _prepare_branch(self, agent_id: str, action: str, version: str) -> str:
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.settings.repository_root, check=True, capture_output=True, text=True)
        if status.stdout.strip():
            raise AgentLifecycleError("Agent management requires a clean Git worktree.", code="git_worktree_dirty")
        safe = re.sub(r"[^0-9A-Za-z._-]", "-", version)
        branch = f"codex/agent-{agent_id}-{action}-v{safe}-{uuid.uuid4().hex[:8]}"
        subprocess.run(["git", "switch", "-c", branch], cwd=self.settings.repository_root, check=True, capture_output=True, text=True)
        return branch

    def _commit_agent_change(self, directory: Path, message: str, *, deleted: bool = False) -> str:
        relative = directory.resolve().relative_to(self.settings.repository_root.resolve()) if not deleted else directory.relative_to(self.settings.repository_root)
        subprocess.run(["git", "add", "-A", "--", str(relative)], cwd=self.settings.repository_root, check=True, capture_output=True, text=True)
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=self.settings.repository_root)
        if staged.returncode == 0:
            raise AgentLifecycleError("Agent management produced no repository change.", code="agent_management_no_change")
        subprocess.run(["git", "commit", "-m", message], cwd=self.settings.repository_root, check=True, capture_output=True, text=True)
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.settings.repository_root, check=True, capture_output=True, text=True).stdout.strip()

    def _schedule_service_refresh(self) -> bool:
        """Refresh the cached site and API after the response leaves the current process."""

        launcher = self.settings.repository_root / "scripts" / "Start-SAPBusinessAgents.ps1"
        if not launcher.is_file():
            return False
        log_root = self.settings.data_root / "agent-management-reload"
        log_root.mkdir(parents=True, exist_ok=True)
        log_path = log_root / f"reload-{uuid.uuid4().hex[:12]}.log"
        escaped = str(launcher).replace("'", "''")
        command = (
            "Start-Sleep -Seconds 2; "
            f"& '{escaped}' -Restart -RebuildSite -NoBrowser *>> '{str(log_path).replace("'", "''")}'"
        )
        creation_flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", command],
                cwd=self.settings.repository_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
                close_fds=True,
            )
        except OSError:
            return False
        return True

    def _audit(self, agent_id: str, action: str, from_version: str | None, to_version: str | None, digest: str | None, branch: str | None, commit: str | None, detail: dict[str, Any]) -> None:
        self.store.append_agent_management_event(event_id=f"agent_event_{uuid.uuid4().hex[:16]}", agent_id=agent_id, action=action, from_version=from_version, to_version=to_version, agent_hash=digest, branch=branch, commit_sha=commit, detail=detail)

    def _capture_package(self, directory: Path) -> dict[str, Any]:
        manifest = self._read_json(directory / "agent.json")
        files: dict[str, str] = {}
        for path in directory.rglob("*"):
            if path.is_dir() or "versions" in path.relative_to(directory).parts or path.name in {"agent.json", "README.md", "rules.py", "publication.json", "validation.json"}:
                continue
            try:
                files[path.relative_to(directory).as_posix()] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
        return {"manifest": manifest, "readme": self._read_optional(directory / "README.md"), "rules": self._read_optional(directory / "rules.py") or None, "files": files}

    def _write_package(self, directory: Path, package: dict[str, Any], *, manifest_override: dict[str, Any] | None = None) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._write_json(directory / "agent.json", manifest_override or package["manifest"])
        (directory / "README.md").write_text(str(package.get("readme") or "# Agent\n"), encoding="utf-8")
        rules_source = package.get("rules")
        if rules_source:
            (directory / "rules.py").write_text(str(rules_source), encoding="utf-8")
        elif (directory / "rules.py").exists():
            (directory / "rules.py").unlink()
        for name, content in (package.get("files") or {}).items():
            target = (directory / name).resolve()
            if directory.resolve() not in target.parents:
                raise AgentLifecycleError("Agent package file escaped its directory.", code="agent_package_path_invalid")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")

    @staticmethod
    def _blank_package(agent_id: str, module: str, title: dict[str, str]) -> dict[str, Any]:
        manifest = {
            "schemaVersion": 2,
            "slug": agent_id,
            "module": module,
            "title": title,
            "summary": {"zh": "待定义的严格只读固定Agent。", "en": "A GET-only deterministic Agent awaiting definition."},
            "status": "Draft",
            "version": "0.1.0",
            "owner": "Unassigned",
            "tags": ["Draft", "Read-only"],
            "sapModules": [module],
            "transactions": [],
            "tables": [],
            "systems": ["SAP S/4HANA"],
            "inputs": {"zh": [], "en": []},
            "outputs": {
                "zh": ["业务状态", "查询源完整性", "业务证据完整性"],
                "en": ["Business status", "Query-source completeness", "Business-evidence completeness"],
            },
            "guardrails": {"zh": ["严格只读；发布前必须完成真机验收。"], "en": ["GET-only; live acceptance is required before activation."]},
            "workflow": [{"id": "evaluate", "title": {"zh": "确定性判断", "en": "Deterministic evaluation"}, "description": {"zh": "根据结构化输入形成初始结果。", "en": "Produces an initial result from structured input."}, "tools": [{"name": "evidence_summary", "kind": "Local deterministic rule", "purpose": {"zh": "草稿规则入口", "en": "Draft rule entrypoint"}}], "executionStepIds": ["evaluate"]}],
            "execution": {
                "mode": "deterministic",
                "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
                "outputSchema": {"type": "object", "additionalProperties": False, "required": ["business_status", "source_complete", "evidence_complete"], "properties": {"business_status": {"type": "string", "const": "inconclusive", "title": {"zh": "业务状态", "en": "Business status"}}, "source_complete": {"type": "boolean", "title": {"zh": "查询源完整性", "en": "Query-source completeness"}}, "evidence_complete": {"type": "boolean", "title": {"zh": "业务证据完整性", "en": "Business-evidence completeness"}}}},
                "steps": [{"id": "evaluate", "executor": "rule", "operation": "evidence_summary", "inputMapping": {}}],
                "outputMapping": {"business_status": "inconclusive", "source_complete": False, "evidence_complete": False},
                "acceptance": {
                    "schemaVersion": "1.0",
                    "comparisonMode": "business_semantic",
                    "businessKeys": ["business_status"],
                    "facts": ["business_status", "source_complete", "evidence_complete"],
                    "metrics": [],
                    "requiredLimitations": ["Agent business logic must be defined before live acceptance."],
                },
            },
            "validation": _not_tested_validation(),
        }
        return {"manifest": manifest, "readme": f"# {title.get('zh') or agent_id}\n\n严格只读固定Agent草稿。\n", "rules": None, "files": {}}

    def _version_summary(self, manifest: dict[str, Any], directory: Path, *, current: bool) -> dict[str, Any]:
        validation_path = directory / "validation.json"
        validation = self._read_json(validation_path) if validation_path.is_file() else copy.deepcopy(manifest.get("validation") or {})
        return {"version": str(manifest.get("version") or ""), "current": current, "digest": agent_digest(manifest), "validation": validation}

    @staticmethod
    def _read_optional(path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AgentLifecycleError(f"Cannot load {path}: {exc}", code="agent_package_invalid") from exc
        if not isinstance(value, dict):
            raise AgentLifecycleError(f"{path} must contain a JSON object.", code="agent_package_invalid")
        return value

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)


def _execution_digest(manifest: dict[str, Any], rules_source: str | None) -> str:
    return agent_execution_digest(manifest, rules_source)


def _json_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _not_tested_validation() -> dict[str, Any]:
    return {"verdict": "NOT_TESTED", "executable": False, "acceptanceMode": "three_stage", "fixedAgentComparison": "NOT_TESTED", "freeQueryComparison": "NOT_TESTED"}


def _package_diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    _diff_value(before.get("manifest"), after.get("manifest"), "/manifest", changes)
    for name in ("readme", "rules", "files"):
        if before.get(name) != after.get(name):
            changes.append({"path": f"/{name}", "change": "modified", "before_digest": _json_digest(before.get(name)), "after_digest": _json_digest(after.get(name))})
    return changes


def _diff_value(before: Any, after: Any, path: str, changes: list[dict[str, Any]]) -> None:
    if type(before) is not type(after):
        changes.append({"path": path, "change": "type_changed", "before": before, "after": after})
    elif isinstance(before, dict):
        for key in sorted(set(before) | set(after)):
            child = f"{path}/{key}"
            if key not in before:
                changes.append({"path": child, "change": "added", "after": after[key]})
            elif key not in after:
                changes.append({"path": child, "change": "removed", "before": before[key]})
            else:
                _diff_value(before[key], after[key], child, changes)
    elif before != after:
        changes.append({"path": path, "change": "modified", "before": before, "after": after})


def _breaking_schema_change(before: dict[str, Any], after: dict[str, Any]) -> bool:
    before_props = set((before.get("properties") or {}).keys())
    after_props = set((after.get("properties") or {}).keys())
    if not before_props.issubset(after_props):
        return True
    if set(before.get("required") or []) - set(after.get("required") or []):
        return False
    if set(after.get("required") or []) - set(before.get("required") or []):
        return True
    return any((before.get("properties") or {}).get(key) != (after.get("properties") or {}).get(key) for key in before_props)


def _semver_key(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value)
    return tuple(int(item) for item in match.groups()) if match else (0, 0, 0)


def _bump_semver(value: str, bump: str) -> str:
    major, minor, patch = _semver_key(value)
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise AgentLifecycleError("Invalid semantic version bump.", code="agent_version_bump_invalid")


def _bump_kind(before: str, after: str) -> str:
    old = _semver_key(before)
    new = _semver_key(after)
    if new[0] > old[0]:
        return "major"
    if new[0] == old[0] and new[1] > old[1]:
        return "minor"
    if new[0:2] == old[0:2] and new[2] > old[2]:
        return "patch"
    raise AgentLifecycleError("Target version must be newer than the source version.", code="agent_version_invalid")


def _bump_rank(value: str) -> int:
    return {"patch": 1, "minor": 2, "major": 3}.get(value, 0)
