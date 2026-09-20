from __future__ import annotations

import base64
import asyncio
import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from threading import RLock
from typing import Any

from .config import Settings
from .database import RunStore
from .managed_rules import ManagedRuleError, validate_managed_rule
from .manifests import (
    ALLOWED_CATALOG_MODULES,
    AgentRepository,
    ManifestError,
    is_agent_executable,
    validate_execution,
)
from .models import RunStatus, TERMINAL_STATUSES, utc_now
from .acceptance import agent_execution_digest
from .agent_authoring import AgentAuthoringMixin, package_changes
from .agent_identity import AgentIdentityMixin, draft_identity_kind
from .agent_presentation import inspect_agent_presentation, presentation_ready
from .agent_publication import (
    AgentPublicationWorkspace,
    PublicationInfrastructureError,
    deployment_file_lock,
)
from .factory import infer_catalog_module
from .plugins import PluginError
from .relationships import apply_advisory_relationship_policy
from .skills import SkillError, validate_agent_skill_dependencies
from .site_release import SiteReleaseError, SiteReleaseManager
from .workflows import WorkflowRepository, WorkflowError, agent_digest, validate_value


class AgentLifecycleError(RuntimeError):
    def __init__(self, message: str, *, code: str = "agent_management_failed", detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class AgentLifecycleService(AgentIdentityMixin, AgentAuthoringMixin):
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
        skills: Any | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.agents = agents
        self.workflows = workflows
        self.coordinator = coordinator
        self.runtime = runtime
        self.legacy_factory = legacy_factory
        self.skills = skills
        self.draft_root = (settings.draft_root / "agents").resolve()
        self._import_lock = RLock()
        self.store.register_published_agent_identities(self._registered_agent_ids())
        self._feedback_tasks: dict[str, asyncio.Task[Any]] = {}
        self._publication_tasks: dict[str, asyncio.Task[Any]] = {}
        self.feedback_timeout_seconds = settings.agent_feedback_budget_seconds
        self._publication_workspace = AgentPublicationWorkspace(
            settings.repository_root, settings.data_root
        )
        self._site_release = SiteReleaseManager(
            settings.repository_root, settings.data_root
        )

    def _require_skill_dependencies(self, manifest: dict[str, Any]) -> list[str]:
        if self.skills is None:
            return []
        try:
            return validate_agent_skill_dependencies(manifest, self.skills)
        except (SkillError, PluginError) as exc:
            raise AgentLifecycleError(
                str(exc),
                code=str(getattr(exc, "code", "agent_skill_dependency_unavailable")),
                detail=getattr(exc, "detail", None),
            ) from exc

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
            repository_module = self.agents.repository_module(agent_id)
            catalog_module = self.agents.catalog_module(agent_id)
            catalog_revision = int(lifecycle.get("catalog_revision") or 1)
            items.append(
                {
                    "id": agent_id,
                    "module": catalog_module,
                    "catalog_module": catalog_module,
                    "catalog_revision": catalog_revision,
                    "repository_module": repository_module,
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

    def set_catalog_module(self, agent_id: str, payload: Any) -> dict[str, Any]:
        """Reclassify a published Agent without changing its immutable package."""

        with self._import_lock:
            current = self._assert_expected(
                agent_id, payload.expected_version, payload.expected_agent_hash
            )
            lifecycle = copy.deepcopy(self.agents.lifecycle(agent_id))
            current_revision = int(lifecycle.get("catalog_revision") or 1)
            if int(payload.expected_catalog_revision) != current_revision:
                raise AgentLifecycleError(
                    "The Agent catalog classification changed; reload before continuing.",
                    code="agent_catalog_module_conflict",
                    detail={"actual_catalog_revision": current_revision},
                )
            return self._set_catalog_module_owned(
                agent_id,
                str(payload.module),
                current=current,
                lifecycle=lifecycle,
            )

    def _set_catalog_module_owned(
        self,
        agent_id: str,
        module: str,
        *,
        current: dict[str, Any] | None = None,
        lifecycle: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if module not in ALLOWED_CATALOG_MODULES:
            raise AgentLifecycleError(
                "The requested Agent catalog module is invalid.",
                code="agent_catalog_module_invalid",
            )
        current = current or self.agents.get(agent_id)
        lifecycle = copy.deepcopy(lifecycle or self.agents.lifecycle(agent_id))
        previous_module = str(
            lifecycle.get("catalog_module") or current.get("module") or "Common"
        )
        current_revision = int(lifecycle.get("catalog_revision") or 1)
        if previous_module == module:
            return {
                "agent_id": agent_id,
                "catalog_module": module,
                "catalog_revision": current_revision,
                "repository_module": self.agents.repository_module(agent_id),
                "changed": False,
                "pushed": False,
                "reload_required": False,
                "reload_scheduled": False,
            }

        package = self.agents.package(agent_id)
        execution_digest = _execution_digest(current, package.get("rules_source"))
        rules_digest = (
            "sha256:" + hashlib.sha256(package["rules_source"].encode("utf-8")).hexdigest()
            if package.get("rules_source") is not None
            else None
        )
        acceptance_digest = _json_digest(
            ((current.get("execution") or {}).get("acceptance") or {})
        )
        agent_hash = agent_digest(current)
        directory = self.agents._path(agent_id).parent
        branch = self._prepare_branch(
            agent_id, "catalog-module", str(current.get("version") or "0.0.0")
        )
        publication_path = directory / "publication.json"
        publication_before = publication_path.read_bytes() if publication_path.is_file() else None
        documentation_before = self._catalog_documentation_snapshot()
        lifecycle.update(
            catalog_module=module,
            catalog_revision=current_revision + 1,
            catalog_updated_at=utc_now(),
        )
        try:
            self._write_json(publication_path, lifecycle)
            reloaded = self.agents.get(agent_id)
            reloaded_package = self.agents.package(agent_id)
            reloaded_rules = reloaded_package.get("rules_source")
            reloaded_rules_digest = (
                "sha256:" + hashlib.sha256(reloaded_rules.encode("utf-8")).hexdigest()
                if reloaded_rules is not None
                else None
            )
            if (
                agent_digest(reloaded) != agent_hash
                or _execution_digest(reloaded, reloaded_rules) != execution_digest
                or reloaded_rules_digest != rules_digest
                or _json_digest(((reloaded.get("execution") or {}).get("acceptance") or {}))
                != acceptance_digest
            ):
                raise AgentLifecycleError(
                    "Catalog reclassification changed the immutable Agent package.",
                    code="agent_catalog_module_execution_changed",
                )
            documentation_paths = self._refresh_catalog_documentation()
            commit = self._commit_agent_change(
                directory,
                f"Reclassify {agent_id} as {module}",
                additional_paths=documentation_paths,
            )
        except Exception as exc:
            if publication_before is None:
                publication_path.unlink(missing_ok=True)
            else:
                publication_path.write_bytes(publication_before)
            for path, content in documentation_before.items():
                path.write_bytes(content)
            rollback_paths = [publication_path, *documentation_before]
            subprocess.run(
                [
                    "git",
                    "add",
                    "-A",
                    "--",
                    *(str(path.relative_to(self.settings.repository_root)) for path in rollback_paths),
                ],
                cwd=self.settings.repository_root,
                check=False,
                capture_output=True,
                text=True,
            )
            if isinstance(exc, AgentLifecycleError):
                raise
            raise AgentLifecycleError(
                "The Agent catalog module could not be updated; repository files were restored.",
                code="agent_catalog_module_update_failed",
            ) from exc
        detail = {
            "from_module": previous_module,
            "to_module": module,
            "catalog_revision": current_revision + 1,
            "repository_module": self.agents.repository_module(agent_id),
            "execution_digest": execution_digest,
            "rules_digest": rules_digest,
            "acceptance_contract_digest": acceptance_digest,
            "sap_get_count": 0,
            "runtime_call_count": 0,
        }
        self._audit(
            agent_id,
            "catalog_module_changed",
            str(current.get("version") or ""),
            str(current.get("version") or ""),
            agent_hash,
            branch,
            commit,
            detail,
        )
        reload_scheduled = self._schedule_service_refresh()
        return {
            "agent_id": agent_id,
            "catalog_module": module,
            "catalog_revision": current_revision + 1,
            "repository_module": detail["repository_module"],
            "changed": True,
            "branch": branch,
            "commit_sha": commit,
            "pushed": False,
            "reload_required": not reload_scheduled,
            "reload_scheduled": reload_scheduled,
            "acceptance_reused": True,
            "digests": {
                "execution": execution_digest,
                "rules": rules_digest,
                "acceptance_contract": acceptance_digest,
            },
        }

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
            package["manifest"]["module"] = str(
                payload.module or self.agents.catalog_module(str(payload.source_agent_id))
            )
        elif source == "free_query":
            if self.legacy_factory is None:
                raise AgentLifecycleError("Agent Factory is unavailable.", code="agent_factory_unavailable")
            generated = await self.legacy_factory.create_from_run(
                str(payload.run_id), module=payload.module
            )
            if not payload.agent_id:
                imported = self.import_source_draft(generated.draft_id)
                return self.get_draft(imported["managed_draft_id"])
            package = self._capture_package(Path(generated.path))
            if payload.agent_id:
                package["manifest"]["slug"] = str(payload.agent_id)
            if payload.module:
                package["manifest"]["module"] = str(payload.module)
        else:
            requested_module = payload.module
            if source == "workflow_gap" and not requested_module:
                requested_module = infer_catalog_module(
                    self.store.get_run(str(payload.run_id)).plan
                )
            package = self._blank_package(
                str(payload.agent_id or f"agent-{uuid.uuid4().hex[:8]}"),
                str(requested_module or "Common"),
                payload.title or {"zh": "新固定Agent", "en": "New Fixed Agent"},
            )
            if source == "workflow_gap":
                package["manifest"].setdefault("authoring", {})["workflowGap"] = {
                    "workflowDraftId": payload.workflow_draft_id,
                    "gapId": payload.gap_id,
                }
        self._assert_manageable(package["manifest"])
        apply_advisory_relationship_policy(package["manifest"])
        package["manifest"]["slug"] = self._assert_identity_available(package["manifest"]["slug"])
        acceptance = package["manifest"]["execution"].setdefault("acceptance", {})
        acceptance["contractVersion"] = "1.0"
        acceptance.setdefault("requiredAssessments", [])
        draft_id = f"agent_draft_{uuid.uuid4().hex[:16]}"
        path = self._draft_path(draft_id)
        path.mkdir(parents=True, exist_ok=False)
        self._write_package(path, package)
        now = utc_now()
        runtime_snapshot = self._new_runtime_snapshot()
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
                "identity": {"kind": "new_agent"},
                "origin": {
                    "sourceAgentId": payload.source_agent_id,
                    "runId": payload.run_id,
                    "workflowDraftId": payload.workflow_draft_id,
                    "gapId": payload.gap_id,
                },
                "runtime_snapshot": runtime_snapshot,
                "catalog_module": str(package["manifest"].get("module") or "Common"),
                "catalog_revision": 1,
            },
            "created_at": now,
            "updated_at": now,
        }
        self._save_initial_identity(draft, package)
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

    def import_source_draft(self, source_id: str) -> dict[str, Any]:
        """Import an existing isolated package; never regenerate or run SAP."""
        with self._import_lock:
            imported = self.store.get_draft_import(source_id)
            if imported:
                try:
                    self.store.get_agent_authoring_draft(imported["managed_draft_id"])
                except KeyError as exc:
                    raise AgentLifecycleError("The imported management draft was deleted.", code="managed_draft_deleted", detail={"managed_draft_id": imported["managed_draft_id"]}) from exc
                return {"draft_id": source_id, "managed_draft_id": imported["managed_draft_id"], "management_import_status": "complete"}
            source = self.store.get_draft(source_id)
            if source.status == "applied":
                raise AgentLifecycleError("An applied source draft cannot be imported.", code="source_draft_applied")
            root = self.settings.draft_root.resolve()
            path = Path(source.path).resolve()
            if not re.fullmatch(r"draft_[a-zA-Z0-9_-]+", source_id) or path != root / source_id or root not in path.parents:
                raise AgentLifecycleError("Source draft escaped its registered directory.", code="agent_draft_path_invalid")
            package = self._capture_package(path)
            self._assert_manageable(package["manifest"])
            source_hash = _json_digest(package)
            managed_id = "agent_draft_" + hashlib.sha256(source_id.encode()).hexdigest()[:24]
            # Deterministic identity also recovers a process interruption between the two saves.
            try:
                previous = self.store.get_agent_authoring_draft(managed_id)
            except KeyError:
                previous = None
            if previous:
                if previous.get("metadata", {}).get("source_draft_id") != source_id:
                    raise AgentLifecycleError("Draft import identity conflict.", code="agent_draft_conflict")
                source_hash = previous["metadata"]["source_package_hash"]
            else:
                package["manifest"]["slug"] = self._assert_identity_available(package["manifest"]["slug"])
                package["manifest"]["validation"] = _not_tested_validation()
                apply_advisory_relationship_policy(package["manifest"])
                acceptance = package["manifest"]["execution"].setdefault("acceptance", {})
                acceptance["contractVersion"] = "1.0"
                acceptance.setdefault("requiredAssessments", [])
                try:
                    source_run = self.store.get_run(source.run_id)
                    source_result = source_run.result
                except KeyError:
                    source_run = None
                    source_result = None
                now = utc_now()
                target = self._draft_path(managed_id)
                self._write_package(target, package)
                draft = {
                    "draft_id": managed_id, "agent_id": package["manifest"]["slug"],
                    "source_type": "free_query", "status": "needs_review", "revision": 1,
                    "path": str(target), "thread_id": None, "source_version": None, "source_hash": None,
                    "target_version": package["manifest"].get("version", "0.1.0"),
                    "risk_class": "behavior_change", "validation_run_id": None,
                    "validation": _not_tested_validation(),
                    "metadata": {"identity": {"kind": "new_agent"}, "source_draft_id": source_id, "source_run_id": source.run_id,
                                 "source_package_hash": source_hash, "source_validation": source.validation,
                                 "catalog_module": str(package["manifest"].get("module") or "Common"),
                                 "catalog_revision": 1,
                                 "source_result_available": source_result is not None,
                                 "source_summary": copy.deepcopy(source_result.summary) if source_result else None,
                                 "runtime_snapshot": (
                                     source_run.runtime.model_dump(mode="json")
                                     if source_run and source_run.runtime
                                     else self._new_runtime_snapshot()
                                 ),
                                 "source_evidence_refs": sorted({
                                     str(item["evidence_ref"]) for item in source_result.evidence
                                     if isinstance(item, dict) and item.get("evidence_ref")
                                 }) if source_result else [],
                                 "private_source_files": sorted(
                                     (set(package.get("files", {})) - _GENERATED_PUBLIC_FILES)
                                     | set(package.get("binary_files", {}))
                                 ),
                                 "origin": copy.deepcopy(source.origin)},
                    "created_at": now, "updated_at": now,
                }
                self._save_initial_identity(draft, package)
            if not self.store.list_agent_conversation_turns(managed_id):
                self.store.save_agent_conversation_turn({
                    "draft_id": managed_id, "turn": 1, "parent_turn": None, "kind": "initial",
                    "status": "completed", "decision": {"source": "free_query", "source_draft_id": source_id},
                    "base_revision": None, "result_revision": 1, "diff": [], "completed_at": utc_now(),
                })
            self.store.save_draft_import(source_id, managed_id, source_hash)
            self._audit(package["manifest"]["slug"], "import_source_draft", None,
                        package["manifest"].get("version"), source_hash, None, None,
                        {"source_draft_id": source_id, "managed_draft_id": managed_id})
            return {"draft_id": source_id, "managed_draft_id": managed_id, "management_import_status": "complete"}

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
        apply_advisory_relationship_policy(package["manifest"])
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
            "metadata": {
                "identity": {"kind": "version_upgrade", "confirmed_agent_id": agent_id, "confirmed_at": now},
                "version_origin": {"bump": bump},
                "runtime_snapshot": self._new_runtime_snapshot(),
                "catalog_module": self.agents.catalog_module(agent_id),
                "catalog_revision": int(self.agents.lifecycle(agent_id).get("catalog_revision") or 1),
            },
            "created_at": now,
            "updated_at": now,
        }
        self._save_initial_identity(draft, package)
        return self.get_draft(draft_id)

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        revision = self.store.get_agent_authoring_revision(draft_id, int(draft["revision"]))
        assessment = self._validation_summary(draft, revision["package"])
        catalog_module, catalog_revision, repository_module = self._draft_catalog_metadata(
            draft, revision["package"]
        )
        sample_operation = self.store.latest_agent_operation(draft_id, "sample_discovery")
        sample = ({**(sample_operation.get("detail") or {}), "run_id": sample_operation["operation_id"], "status": sample_operation["status"], "revision": sample_operation["revision"]} if sample_operation and int(sample_operation["revision"]) == int(draft["revision"]) else None)
        if sample and sample["status"] in {"queued", "running", "cancelling"} and sample.get("phase") != "cleaning_up":
            sample.update(self.store.get_harness_state(sample["run_id"]).get("sample_execution", {}))
        publication_candidates = [
            item
            for item in (
                self.store.latest_agent_operation(draft_id, "publish"),
                self.store.latest_agent_operation(draft_id, "site_refresh"),
            )
            if item is not None
        ]
        publication_operation = (
            self._public_publication_operation(
                max(publication_candidates, key=lambda item: str(item.get("created_at") or ""))
            )
            if publication_candidates
            else None
        )
        return {
            **draft,
            "technical_identity": self.technical_identity(draft),
            "catalog_module": catalog_module,
            "catalog_revision": catalog_revision,
            "repository_module": repository_module,
            "has_publishable_changes": self._has_publishable_changes(
                draft, revision["package"]
            ),
            "package": revision["package"],
            "presentation": inspect_agent_presentation(revision["package"]["manifest"]),
            "diff": revision["diff"],
            "revisions": self.store.list_agent_authoring_revisions(draft_id),
            "conversation": [self._public_feedback_turn(turn) for turn in self.store.list_agent_conversation_turns(draft_id)],
            "active_operation": self.store.get_agent_operation(draft_id),
            "publication_operation": publication_operation,
            "sample_discovery": sample,
            **assessment,
        }

    def list_drafts(self, state: str = "all") -> list[dict[str, Any]]:
        if state not in {"all", "unpublished"}:
            raise AgentLifecycleError(
                "Unknown Agent draft state filter.", code="agent_draft_state_invalid"
            )
        items: list[dict[str, Any]] = []
        for draft in self.store.list_agent_authoring_drafts():
            sync_error = None
            if draft.get("status") == "validating":
                try:
                    if not draft.get("validation_run_id"):
                        raise KeyError("Missing validation run")
                    self.validation_report(draft["draft_id"])
                    draft = self.store.get_agent_authoring_draft(draft["draft_id"])
                except (KeyError, AgentLifecycleError):
                    sync_error = "validation_state_unavailable"
            if state == "unpublished" and draft["status"] in {"published", "cancelled"}:
                continue
            try:
                package = self.store.get_agent_authoring_revision(
                    draft["draft_id"], int(draft["revision"])
                )["package"]
            except KeyError:
                package = {}
            manifest = package.get("manifest") if isinstance(package, dict) else {}
            catalog_module, catalog_revision, repository_module = self._draft_catalog_metadata(
                draft, package
            )
            blockers = self._draft_delete_blockers(draft)
            items.append(
                {
                    **draft,
                    "sync_error": sync_error,
                    "technical_identity": self.technical_identity(draft),
                    "title": copy.deepcopy((manifest or {}).get("title") or {}),
                    "module": catalog_module,
                    "catalog_module": catalog_module,
                    "catalog_revision": catalog_revision,
                    "repository_module": repository_module,
                    "management": {
                        "can_delete": not blockers,
                        "delete_blockers": blockers,
                    },
                }
            )
        return items

    def set_draft_catalog_module(self, draft_id: str, payload: Any) -> dict[str, Any]:
        operation = self._reserve_operation(
            draft_id, int(payload.expected_revision), "catalog_module"
        )
        try:
            draft = self.store.get_agent_authoring_draft(draft_id)
            self._assert_editable(draft)
            if draft_identity_kind(draft) == "version_upgrade":
                with self._import_lock:
                    current = self._assert_expected(
                        draft["agent_id"], draft["source_version"], draft["source_hash"]
                    )
                    lifecycle = copy.deepcopy(self.agents.lifecycle(draft["agent_id"]))
                    current_catalog_revision = int(lifecycle.get("catalog_revision") or 1)
                    expected_catalog_revision = getattr(
                        payload, "expected_catalog_revision", None
                    )
                    if (
                        expected_catalog_revision is not None
                        and int(expected_catalog_revision) != current_catalog_revision
                    ):
                        raise AgentLifecycleError(
                            "The Agent catalog classification changed; reload before continuing.",
                            code="agent_catalog_module_conflict",
                            detail={"actual_catalog_revision": current_catalog_revision},
                        )
                    result = self._set_catalog_module_owned(
                        draft["agent_id"],
                        str(payload.module),
                        current=current,
                        lifecycle=lifecycle,
                    )
                return {**self.get_draft(draft_id), "catalog_module_update": result}

            package = self.store.get_agent_authoring_revision(
                draft_id, int(payload.expected_revision)
            )["package"]
            previous_digests = {
                "execution": _execution_digest(package["manifest"], package.get("rules")),
                "rules": _json_digest(package.get("rules")),
                "acceptance": _json_digest(
                    ((package["manifest"].get("execution") or {}).get("acceptance") or {})
                ),
            }
            updated = copy.deepcopy(package)
            updated["manifest"]["module"] = str(payload.module)
            updated_digests = {
                "execution": _execution_digest(updated["manifest"], updated.get("rules")),
                "rules": _json_digest(updated.get("rules")),
                "acceptance": _json_digest(
                    ((updated["manifest"].get("execution") or {}).get("acceptance") or {})
                ),
            }
            if updated_digests != previous_digests:
                raise AgentLifecycleError(
                    "Catalog reclassification changed Agent execution.",
                    code="agent_catalog_module_execution_changed",
                )
            return self._apply_package(
                draft_id,
                int(payload.expected_revision),
                updated,
                kind="catalog_module",
                operation_id=operation["operation_id"],
                record_turn=False,
                allow_catalog_module_change=True,
                preserve_validation=True,
            )
        finally:
            self._finish_operation(draft_id, operation["operation_id"])

    def delete_draft(self, draft_id: str, payload: Any) -> dict[str, Any]:
        operation = self._reserve_operation(draft_id, int(payload.expected_revision), "delete")
        try:
            return self._delete_draft_owned(draft_id, payload, operation_id=operation["operation_id"])
        finally:
            self._finish_operation(draft_id, operation["operation_id"])

    def _delete_draft_owned(self, draft_id: str, payload: Any, *, operation_id: str) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        if int(payload.expected_revision) != int(draft["revision"]):
            raise AgentLifecycleError(
                "Agent draft revision changed.", code="agent_draft_conflict"
            )
        if str(payload.confirm_agent_id) != str(draft["agent_id"]):
            raise AgentLifecycleError(
                "Agent ID confirmation does not match.",
                code="agent_draft_delete_confirmation_mismatch",
            )
        if draft["status"] == "published":
            raise AgentLifecycleError(
                "A published Agent draft is immutable.", code="agent_draft_published"
            )
        blockers = self._draft_delete_blockers(draft)
        if blockers:
            raise AgentLifecycleError(
                "The Agent draft cannot be deleted while validation is active.",
                code="agent_draft_validation_active",
                detail={"blockers": blockers},
            )

        path = Path(str(draft["path"])).resolve()
        expected = self._draft_path(draft_id)
        if path != expected or self.draft_root not in path.parents:
            raise AgentLifecycleError(
                "Agent draft deletion target escaped the draft root.",
                code="agent_draft_path_invalid",
            )
        trash_root = (self.settings.draft_root / "management-trash").resolve()
        trash_root.mkdir(parents=True, exist_ok=True)
        trash = (trash_root / f"{draft_id}-{uuid.uuid4().hex[:12]}").resolve()
        if trash_root not in trash.parents:
            raise AgentLifecycleError(
                "Agent draft trash target escaped its root.",
                code="agent_draft_path_invalid",
            )
        moved = False
        package_snapshot: dict[str, bytes] = {}
        if path.exists():
            package_snapshot = self._snapshot_draft_package(path)
            shutil.move(str(path), str(trash))
            moved = True
            try:
                shutil.rmtree(trash)
            except Exception:
                self._restore_draft_package(path, package_snapshot)
                raise
        audit_event_id = f"agent_event_{uuid.uuid4().hex[:16]}"
        try:
            retained_run_ids = self.store.delete_agent_authoring_draft(
                draft_id,
                operation_id=operation_id,
                expected_revision=int(payload.expected_revision),
                audit_event_id=audit_event_id,
                agent_id=str(draft["agent_id"]),
                detail={
                    "draft_id": draft_id,
                    "revision": int(draft["revision"]),
                    "source_type": draft["source_type"],
                    "target_version": draft.get("target_version"),
                },
            )
        except ValueError as exc:
            if moved:
                self._restore_draft_package(path, package_snapshot)
            raise AgentLifecycleError(
                "Agent draft revision changed.", code="agent_draft_conflict"
            ) from exc
        except Exception:
            if moved:
                self._restore_draft_package(path, package_snapshot)
            raise
        return {
            "draft_id": draft_id,
            "agent_id": draft["agent_id"],
            "deleted": True,
            "retained_validation_run_ids": retained_run_ids,
            "audit_event_id": audit_event_id,
        }

    def update(self, draft_id: str, payload: Any) -> dict[str, Any]:
        operation = self._reserve_operation(draft_id, int(payload.expected_revision), "edit")
        try:
            package = copy.deepcopy(self.store.get_agent_authoring_revision(draft_id, int(payload.expected_revision))["package"])
            if payload.manifest is not None:
                package["manifest"] = copy.deepcopy(payload.manifest)
            if payload.readme is not None:
                package["readme"] = str(payload.readme)
            supplied = getattr(payload, "model_fields_set", set())
            if payload.rules is not None or "rules" in supplied:
                package["rules"] = str(payload.rules) if payload.rules else None
            return self._apply_package(draft_id, int(payload.expected_revision), package, kind="manual_edit", operation_id=operation["operation_id"])
        finally:
            self._finish_operation(draft_id, operation["operation_id"])

    async def feedback(self, draft_id: str, payload: Any) -> dict[str, Any]:
        return await self.submit_feedback(draft_id, payload)

    def _new_runtime_snapshot(self) -> dict[str, Any] | None:
        snapshot = getattr(self.runtime, "snapshot", None)
        if not callable(snapshot):
            return None
        try:
            value = snapshot()
        except Exception:
            return None
        return copy.deepcopy(value) if isinstance(value, dict) else None

    def undo(self, draft_id: str, *, expected_revision: int, target_revision: int) -> dict[str, Any]:
        operation = self._reserve_operation(draft_id, expected_revision, "undo")
        try:
            package = self.store.get_agent_authoring_revision(draft_id, target_revision)["package"]
            return self._apply_package(draft_id, expected_revision, package, kind="undo", operation_id=operation["operation_id"], decision={"target_revision": target_revision})
        finally:
            self._finish_operation(draft_id, operation["operation_id"])

    def validate(self, draft_id: str, *, operation_id: str | None = None, expected_revision: int | None = None) -> dict[str, Any]:
        own_operation = None
        if operation_id is None:
            current = self.store.get_agent_authoring_draft(draft_id)
            own_operation = self._reserve_operation(draft_id, int(expected_revision if expected_revision is not None else current["revision"]), "static_validation")
            operation_id = own_operation["operation_id"]
        try:
            return self._validate_owned(draft_id, operation_id)
        finally:
            if own_operation:
                self._finish_operation(draft_id, operation_id)

    def _validate_owned(self, draft_id: str, operation_id: str) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        self._assert_editable(draft)
        revision = self.store.get_agent_authoring_revision(draft_id, int(draft["revision"]))
        package = revision["package"]
        manifest = package["manifest"]
        checks: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        try:
            self._assert_manageable(manifest)
            validate_execution(manifest, f"agent-draft:{draft_id}")
            checks.append({"code": "agent_schema_valid", "status": "pass"})
            from .authoring_checks import (
                candidate_business_output_issues,
                candidate_input_issues,
            )
            input_issues = candidate_input_issues(manifest)
            errors.extend(input_issues)
            checks.append({"code": "agent_input_contract", "status": "fail" if input_issues else "pass"})
            business_output_issues = candidate_business_output_issues(manifest)
            errors.extend(business_output_issues)
            checks.append(
                {
                    "code": "agent_business_output_contract",
                    "status": "fail" if business_output_issues else "pass",
                }
            )
            self._require_skill_dependencies(manifest)
            checks.append({"code": "agent_skill_dependencies_valid", "status": "pass"})
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
        presentation = inspect_agent_presentation(manifest)
        report["presentation_contract"] = presentation
        report["acceptance_readiness"] = self._acceptance_readiness(draft, package)
        checks.append({
            "code": "agent_presentation_contract",
            "status": "pass" if presentation.get("status") == "ready" else "needs_input",
        })
        if not errors and presentation.get("status") == "ready" and risk == "metadata_only":
            source = self.agents.get(str(draft["agent_id"]))
            if not is_agent_executable(source):
                errors.append({"code": "source_acceptance_unavailable", "message": "The source version has no reusable PASS acceptance."})
            else:
                report.update(
                    verdict="PASS",
                    reused_validation=True,
                    source_validation=copy.deepcopy(source.get("validation") or {}),
                    source_version=source["version"],
                    source_validation_digest=_json_digest(source.get("validation") or {}),
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
        draft["metadata"] = {**(draft.get("metadata") or {}), "static_checks": copy.deepcopy(report)}
        existing = self._formal_acceptance(draft, package)
        if existing and not report.get("reused_validation") and not errors:
            draft.update(status=status, risk_class=risk, updated_at=utc_now())
        else:
            draft.update(status=status, risk_class=risk, validation=report, updated_at=utc_now())
        self.store.save_agent_authoring_draft(draft, expected_revision=int(draft["revision"]), operation_id=operation_id)
        return self.get_draft(draft_id)

    async def live_validate(self, draft_id: str, *, input_value: dict[str, Any], auto_discover: bool = False, expected_revision: int | None = None, sensitive_inputs: dict[str, Any] | None = None, request_id: str | None = None) -> dict[str, Any]:
        if auto_discover:
            raise AgentLifecycleError("Find and confirm a real sample before trial execution.", code="agent_sample_confirmation_required")
        draft = self.store.get_agent_authoring_draft(draft_id)
        revision = int(expected_revision if expected_revision is not None else draft["revision"])
        self.require_technical_identity(draft)
        secret_fingerprints: dict[str, Any] = {}
        if sensitive_inputs:
            descriptor = getattr(getattr(self.coordinator, "secret_protector", None), "hmac_descriptor", None)
            if not callable(descriptor):
                raise AgentLifecycleError("Secure input protection is unavailable.", code="agent_secure_input_unsupported")
            try:
                secret_fingerprints = {str(field): descriptor(str(value).strip(), domain=f"agent-trial:{draft_id}:{field}") for field, value in sensitive_inputs.items()}
            except Exception as exc:
                raise AgentLifecycleError("Secure input protection failed.", code="agent_secure_input_unsupported") from exc
        fingerprint = _json_digest({"revision": revision, "input": input_value, "sensitive_inputs": secret_fingerprints})
        operation = self._reserve_operation(draft_id, revision, "trial", request_id, fingerprint)
        if operation.get("reused"):
            return copy.deepcopy((operation.get("detail") or {}).get("trial") or {"operation_id": operation["operation_id"], "status": operation["status"]})
        operation_id = operation["operation_id"]
        try:
            validated = self.validate(draft_id, operation_id=operation_id)
            if validated["status"] == "invalid":
                static_errors = (
                    (validated.get("metadata") or {})
                    .get("static_checks", {})
                    .get("errors", [])
                )
                business_issue = next(
                    (
                        item
                        for item in static_errors
                        if isinstance(item, dict)
                        and item.get("code")
                        == "agent_business_output_contract_missing"
                    ),
                    None,
                )
                if business_issue:
                    raise AgentLifecycleError(
                        str(business_issue.get("message") or "The Agent business-output contract is missing."),
                        code="agent_business_output_contract_missing",
                    )
                raise AgentLifecycleError("Static Agent validation failed.", code="agent_static_validation_failed")
            draft = self.store.get_agent_authoring_draft(draft_id)
            package = self.store.get_agent_authoring_revision(draft_id, revision)["package"]
            run_id = await self.coordinator.submit_agent_snapshot(package["manifest"], copy.deepcopy(input_value), rules_source=package.get("rules"), draft_id=draft_id, revision=revision, sensitive_inputs=sensitive_inputs or {})
            self._assert_operation(draft_id, operation_id, revision)
            report = {
                "type": "trial",
                "run_id": run_id,
                "agent_id": draft["agent_id"],
                "revision": revision,
                "operation_id": operation_id,
                "execution_digest": _execution_digest(package["manifest"], package.get("rules")),
                "acceptance_contract_digest": _json_digest(
                    (package["manifest"].get("execution") or {}).get("acceptance") or {}
                ),
                "status": "running",
                "verdict": "pending",
                "automatic_checks": (draft.get("metadata", {}).get("static_checks") or {}).get("checks") or [],
                "sample_source": "user_confirmed",
                "timeout_seconds": self.settings.deterministic_run_seconds,
                "business_output_available": None,
                "business_record_count": None,
                "business_output_issues": [],
                "started_at": utc_now(),
            }
            self.store.save_agent_validation_attempt(draft_id=draft_id, run_id=run_id, revision=revision, report=report, report_digest=None)
            draft["metadata"] = {**(draft.get("metadata") or {}), "trial": report}
            draft.update(status="validating", validation_run_id=run_id, updated_at=utc_now())
            self.store.save_agent_authoring_draft(draft, expected_revision=revision, operation_id=operation_id)
            self.store.update_agent_operation(draft_id, operation_id, detail={"trial": report})
            return report
        except BaseException:
            self._finish_operation(draft_id, operation_id, "failed")
            raise

    def validation_report(self, draft_id: str) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        run_id = draft.get("validation_run_id")
        if not run_id:
            return self._validation_response(draft)
        attempt = self.store.get_agent_validation_attempt(draft_id, run_id)
        if int(attempt["revision"]) != int(draft["revision"]):
            raise AgentLifecycleError("Validation belongs to an older draft revision.", code="agent_validation_revision_conflict")
        run = self.store.get_run(run_id)
        if run.status not in TERMINAL_STATUSES:
            response = self._validation_response(draft)
            response["trial"] = {**attempt["report"], "type": "trial", "status": str(run.status.value if hasattr(run.status, "value") else run.status), "progress": run.progress.model_dump(mode="json")}
            return response
        if attempt["completed_at"]:
            response = self._validation_response(draft)
            # Legacy attempts remain immutable and explicitly non-certifying.
            response["trial"] = {key: value for key, value in attempt["report"].items() if key not in {"fixedAgentComparison", "freeQueryComparison", "acceptanceMode"}}
            response["trial"]["type"] = "trial"
            return response
        result = run.result
        run_status = str(run.status.value if hasattr(run.status, "value") else run.status)
        run_errors = copy.deepcopy(run.error or (result.errors if result else []))
        incomplete_attempt = result is None or run.status not in {RunStatus.completed, RunStatus.inconclusive}
        if incomplete_attempt:
            # Cancellation, interruption and timeouts are operational attempt
            # outcomes.  They must not manufacture business-schema failures or
            # replace a previously applicable completed trial.
            report = {
                **attempt["report"],
                "type": "trial",
                "status": run_status,
                "verdict": "FAIL",
                "completed_at": run.completed_at or utc_now(),
                "read_only_audit": None,
                "output_schema_valid": None,
                "timeout_seconds": attempt["report"].get(
                    "timeout_seconds", self.settings.deterministic_run_seconds
                ),
                "business_output_available": None,
                "business_record_count": None,
                "business_output_issues": [],
                "source_complete": None,
                "evidence_complete": None,
                "business_complete": None,
                "errors": run_errors,
                "acceptance_readiness": {
                    "status": "not_completed",
                    "revision": int(draft["revision"]),
                    "issues": [],
                },
            }
            digest = _json_digest(report)
            report["report_digest"] = digest
            if not self.store.finish_agent_validation(draft_id, run_id, int(draft["revision"]), report, digest):
                raise AgentLifecycleError("The draft changed while validation was synchronizing.", code="agent_validation_revision_conflict")
            if report.get("operation_id"):
                self._finish_operation(draft_id, report["operation_id"])
            refreshed = self.store.get_agent_authoring_draft(draft_id)
            return self._validation_response(refreshed)
        from .workflow_factory import _read_only_audit

        tool_read_only = bool(result) and _read_only_audit({"readOnly": True}, result.tool_calls or [])[0]
        # The platform audit admits only Broker-owned approved Skill transport; an
        # arbitrary POST with no approved capability must not be accepted as ADT.
        for call in (result.tool_calls if result else []) or []:
            method = str(call.get("http_method") or call.get("httpMethod") or "GET").upper()
            if method != "GET" and not (str(call.get("capability") or "") == "skill_execute.v1" and method == "POST" and call.get("read_only") is not False):
                tool_read_only = False
        package = self.store.get_agent_authoring_revision(draft_id, int(draft["revision"]))["package"]
        schema_complete = False
        if result and result.workflow_output is not None:
            try:
                validate_value(result.workflow_output, package["manifest"]["execution"]["outputSchema"], label="Candidate trial output")
                schema_complete = True
            except WorkflowError:
                schema_complete = False
        complete = bool(
            result
            and result.completeness.source_complete
            and result.completeness.business_complete
        )
        from .authoring_checks import trial_business_output_summary

        business_output = trial_business_output_summary(result) if result else {
            "business_output_available": False,
            "business_record_count": 0,
            "business_output_issues": [{
                "code": "agent_trial_business_output_missing",
                "message": "The trial did not produce a public business result.",
            }],
        }
        # A completed execution with incomplete SAP evidence is an
        # inconclusive trial, not a failed Agent implementation.  Keep safety
        # or output-contract violations as FAIL so they cannot be mistaken for
        # a merely data-bounded result.
        if run.status == RunStatus.completed:
            verdict = (
                "FAIL"
                if not tool_read_only
                or not schema_complete
                or not business_output["business_output_available"]
                else "PASS"
                if complete
                else "INCONCLUSIVE"
            )
        elif run.status == RunStatus.inconclusive:
            verdict = (
                "FAIL"
                if not tool_read_only
                or not schema_complete
                or not business_output["business_output_available"]
                else "INCONCLUSIVE"
            )
        else:
            verdict = "FAIL"
        report = {
            **attempt["report"],
            "type": "trial",
            "status": run_status,
            "verdict": verdict,
            "completed_at": run.completed_at or utc_now(),
            "read_only_audit": tool_read_only,
            "output_schema_valid": schema_complete,
            "timeout_seconds": attempt["report"].get(
                "timeout_seconds", self.settings.deterministic_run_seconds
            ),
            **business_output,
            "source_complete": bool(result and result.completeness.source_complete),
            "evidence_complete": bool(result and isinstance(result.workflow_output, dict) and result.workflow_output.get("evidence_complete", result.completeness.business_complete)),
            "business_complete": bool(result and result.completeness.business_complete),
            "errors": run_errors,
        }
        from .acceptance_contract import readiness, result_payload
        report["acceptance_readiness"] = readiness(
            package["manifest"], int(draft["revision"]),
            result=result_payload(result),
        )
        digest = _json_digest(report)
        report["report_digest"] = digest
        if not self.store.finish_agent_validation(draft_id, run_id, int(draft["revision"]), report, digest):
            raise AgentLifecycleError("The draft changed while validation was synchronizing.", code="agent_validation_revision_conflict")
        if report.get("operation_id"):
            self._finish_operation(draft_id, report["operation_id"])
        refreshed = self.store.get_agent_authoring_draft(draft_id)
        return self._validation_response(refreshed)

    def _formal_acceptance(self, draft: dict[str, Any], package: dict[str, Any]) -> dict[str, Any] | None:
        if not presentation_ready(package["manifest"]):
            return None
        report = draft.get("validation") or {}
        identity = (draft.get("metadata") or {}).get("identity") or {}
        if identity.get("validation_invalidated_revision") and (
            report.get("agent_id") != draft["agent_id"] or report.get("revision") != draft["revision"]
        ):
            return None
        digest = _execution_digest(package["manifest"], package.get("rules"))
        if report.get("verdict") != "PASS" or report.get("type") == "trial" or report.get("execution_digest", report.get("agent_execution_digest")) != digest:
            return None
        if report.get("reused_validation"):
            if self._risk_class(draft, package) != "metadata_only":
                return None
            return copy.deepcopy(report)
        from .acceptance_contract import contract_issues
        if contract_issues(package["manifest"]):
            return None
        # A locally executed run is not an independent comparison certificate.
        if report.get("run_id") == draft.get("validation_run_id") and report.get("run_id"):
            return None
        validation = report.get("agent_validation") or report
        if validation.get("executable") is not True or validation.get("fixedAgentComparison") != "MATCH" or not validation.get("acceptanceMode") or validation.get("blockingLimitations"):
            return None
        if validation.get("acceptanceMode") == "three_stage" and validation.get("freeQueryComparison") != "MATCH":
            return None
        return copy.deepcopy(report)

    def _acceptance_readiness(self, draft: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
        from .acceptance_contract import readiness, result_payload
        if self._risk_class(draft, package) == "metadata_only":
            return {"status": "reused", "revision": draft["revision"], "issues": []}
        trial = (draft.get("metadata") or {}).get("effective_trial") or {}
        result = None
        expected_execution = _execution_digest(package["manifest"], package.get("rules"))
        expected_contract = _json_digest(
            (package["manifest"].get("execution") or {}).get("acceptance") or {}
        )
        if (
            trial.get("catalog_metadata_revision", trial.get("revision", trial.get("draft_revision"))) == draft["revision"]
            and trial.get("run_id")
            and trial.get("agent_id") == draft.get("agent_id")
            and trial.get("execution_digest") == expected_execution
            and trial.get("acceptance_contract_digest") == expected_contract
        ):
            try:
                run = self.store.get_run(trial["run_id"])
                if run.result:
                    result = result_payload(run.result)
            except KeyError:
                pass
        return readiness(package["manifest"], int(draft["revision"]), result=result)

    def _validation_summary(self, draft: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
        certificate = self._formal_acceptance(draft, package)
        recorded = copy.deepcopy(draft.get("validation") or {})
        current_digest = (
            _execution_digest(package["manifest"], package.get("rules"))
            if isinstance(package.get("manifest"), dict)
            else None
        )
        acceptance = certificate
        if (
            acceptance is None
            and current_digest is not None
            and recorded.get("type") == "formal_acceptance"
            and recorded.get("revision") == draft.get("revision")
            and recorded.get("execution_digest", recorded.get("agent_execution_digest")) == current_digest
        ):
            # FAIL and BLOCKED are useful formal results, but are never
            # publication certificates.  Keep them visible to the user while
            # the publishability gate continues to require _formal_acceptance.
            acceptance = recorded
        trial = copy.deepcopy((draft.get("metadata") or {}).get("trial"))
        effective_trial = copy.deepcopy((draft.get("metadata") or {}).get("effective_trial"))
        static = copy.deepcopy((draft.get("metadata") or {}).get("static_checks") or {})
        manifest = package.get("manifest")
        presentation = (
            inspect_agent_presentation(manifest)
            if isinstance(manifest, dict)
            else {
                "status": "invalid",
                "issues": [{
                    "code": "presentation_manifest_missing",
                    "path": "manifest",
                    "severity": "error",
                    "blocking": True,
                    "message": {
                        "zh": "Agent包缺少Manifest，无法生成展示资料。",
                        "en": "The Agent package has no Manifest, so presentation metadata cannot be derived.",
                    },
                }],
            }
        )
        static["presentation_contract"] = presentation
        acceptance_readiness = self._acceptance_readiness(draft, package)
        static["acceptance_readiness"] = acceptance_readiness
        blockers = [] if certificate else ["agent_formal_acceptance_required"]
        if acceptance_readiness["status"] not in {"ready", "reused"}:
            blockers.append("agent_acceptance_contract_not_ready")
        if presentation.get("status") != "ready":
            blockers.append("agent_presentation_contract_invalid")
        if self._platform_changes_pending(draft["draft_id"]):
            blockers.append("runtime_changeset_integration_verification_required")
        identity = self.technical_identity(draft)
        if identity["kind"] == "unknown":
            blockers.append("agent_identity_unknown")
        elif not identity["confirmed"]:
            blockers.append("agent_technical_id_confirmation_required")
        if static.get("errors"):
            blockers.append("agent_static_validation_failed")
        if draft.get("status") == "published":
            blockers.append("agent_draft_published")
        active = self.store.get_agent_operation(draft["draft_id"])
        if active and active.get("kind") not in {"publish", "static_validation"}:
            blockers.append("agent_draft_operation_active")
        return {
            "static_checks": static,
            "acceptance_readiness": acceptance_readiness,
            "trial": trial,
            "effective_trial": effective_trial,
            "acceptance": acceptance or {"verdict": "NOT_TESTED", "requires_formal_acceptance": True},
            "publishability": {"can_publish": not blockers, "blockers": blockers},
        }

    def _platform_changes_pending(self, draft_id: str) -> bool:
        return any((turn.get("decision") or {}).get("harness", {}).get("platform_dependency_status") == "awaiting_verification"
                   for turn in self.store.list_agent_conversation_turns(draft_id))

    def _validation_response(self, draft: dict[str, Any]) -> dict[str, Any]:
        package = self.store.get_agent_authoring_revision(draft["draft_id"], int(draft["revision"]))["package"]
        summary = self._validation_summary(draft, package)
        return {**copy.deepcopy(draft.get("validation") or {}), **summary, "verdict": summary["acceptance"]["verdict"], "revision": draft["revision"]}

    def publish(self, draft_id: str, payload: Any, *, schedule_refresh: bool = True) -> dict[str, Any]:
        """Synchronous entry point retained for lifecycle scripts and unit tests."""

        operation = self._reserve_publication(draft_id, payload)
        if operation.get("reused"):
            detail = operation.get("detail") or {}
            return copy.deepcopy(detail.get("result") or self._public_publication_operation(operation))
        return self._execute_publication(
            draft_id, payload, operation["operation_id"], schedule_refresh=schedule_refresh
        )

    def start_publish(self, draft_id: str, payload: Any) -> dict[str, Any]:
        operation = self._reserve_publication(draft_id, payload)
        public = self._public_publication_operation(operation)
        if operation.get("reused"):
            return public
        task = asyncio.create_task(
            asyncio.to_thread(
                self._execute_publication,
                draft_id,
                payload,
                operation["operation_id"],
                True,
            )
        )
        self._publication_tasks[operation["operation_id"]] = task
        task.add_done_callback(
            lambda completed: self._publication_task_done(operation["operation_id"], completed)
        )
        return public

    def start_site_refresh(self, draft_id: str, payload: Any) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        latest_publish = self.store.latest_agent_operation(draft_id, "publish")
        publish_detail = (latest_publish or {}).get("detail") or {}
        publication = publish_detail.get("result") or {}
        if draft.get("status") != "published" or publication.get("publication_status") != "published":
            raise AgentLifecycleError(
                "Only a completed Agent publication can retry the Web UI refresh.",
                code="agent_publication_required",
            )
        fingerprint = _json_digest(
            {
                "revision": int(draft["revision"]),
                "agent_id": draft["agent_id"],
                "kind": "site_refresh",
            }
        )
        operation = self._reserve_operation(
            draft_id,
            int(draft["revision"]),
            "site_refresh",
            str(payload.request_id),
            fingerprint,
            allow_published=True,
        )
        if operation.get("reused"):
            return self._public_publication_operation(operation)
        detail = {
            "phase": "queued",
            "publication_status": "published",
            "site_refresh_status": "queued",
            "commit_sha": publication.get("commit_sha"),
            "started_at": None,
            "completed_at": None,
            "failure_code": None,
            "agent_id": draft["agent_id"],
            "version": publication.get("version") or draft.get("target_version"),
        }
        self.store.update_agent_operation(
            draft_id, operation["operation_id"], status="queued", detail=detail
        )
        operation = self.store.get_agent_operation_by_id(draft_id, operation["operation_id"])
        task = asyncio.create_task(
            asyncio.to_thread(self._execute_site_refresh, draft_id, operation["operation_id"])
        )
        self._publication_tasks[operation["operation_id"]] = task
        task.add_done_callback(
            lambda completed: self._publication_task_done(operation["operation_id"], completed)
        )
        return self._public_publication_operation(operation)

    def _reserve_publication(self, draft_id: str, payload: Any) -> dict[str, Any]:
        input_hash = _json_digest(
            {
                "expected_revision": int(payload.expected_revision),
                "target_version": str(getattr(payload, "target_version", "") or ""),
                "activate": bool(getattr(payload, "activate", False)),
                "validation_report_digest": getattr(payload, "validation_report_digest", None),
            }
        )
        operation = self._reserve_operation(
            draft_id,
            int(payload.expected_revision),
            "publish",
            str(getattr(payload, "request_id", "") or uuid.uuid4()),
            input_hash,
        )
        if not operation.get("reused"):
            detail = {
                "phase": "queued",
                "publication_status": "pending",
                "site_refresh_status": "pending" if bool(getattr(payload, "activate", False)) else "not_required",
                "commit_sha": None,
                "build_fingerprint": None,
                "started_at": None,
                "completed_at": None,
                "failure_code": None,
                "agent_id": self.store.get_agent_authoring_draft(draft_id)["agent_id"],
                "version": str(getattr(payload, "target_version", "") or ""),
                "activate": bool(getattr(payload, "activate", False)),
            }
            self.store.update_agent_operation(
                draft_id, operation["operation_id"], status="queued", detail=detail
            )
            operation = self.store.get_agent_operation_by_id(draft_id, operation["operation_id"])
        return operation

    def _publication_task_done(self, operation_id: str, task: asyncio.Task[Any]) -> None:
        self._publication_tasks.pop(operation_id, None)
        if not task.cancelled():
            task.exception()

    def _execute_publication(
        self,
        draft_id: str,
        payload: Any,
        operation_id: str,
        schedule_refresh: bool = True,
    ) -> dict[str, Any]:
        self._update_publication_operation(
            draft_id, operation_id, status="running", phase="preparing", started_at=utc_now()
        )
        try:
            with self._import_lock:
                result = self._publish_owned(
                    draft_id,
                    payload,
                    schedule_refresh=schedule_refresh,
                    operation_id=operation_id,
                )
        except Exception as exc:
            code = str(
                getattr(
                    exc,
                    "code",
                    "agent_publication_failed",
                )
            )
            try:
                interrupted_detail = self.store.get_agent_operation_by_id(
                    draft_id, operation_id
                ).get("detail") or {}
                candidate = str(interrupted_detail.get("candidate_commit_sha") or "")
                merged_but_unrecorded = bool(
                    candidate and self._publication_workspace.is_merged(candidate)
                )
            except (KeyError, PublicationInfrastructureError):
                merged_but_unrecorded = False
            if merged_but_unrecorded:
                code = "agent_publication_state_unconfirmed"
            self._update_publication_operation(
                draft_id,
                operation_id,
                status="failed",
                publication_status="unknown" if merged_but_unrecorded else "failed",
                site_refresh_status="not_started",
                failure_code=code,
                completed_at=utc_now(),
            )
            if isinstance(exc, AgentLifecycleError):
                raise
            if isinstance(exc, (PublicationInfrastructureError, SiteReleaseError)):
                raise AgentLifecycleError(str(exc), code=code) from exc
            raise
        terminal_status = "completed"
        self._update_publication_operation(
            draft_id,
            operation_id,
            status=terminal_status,
            phase="completed",
            completed_at=utc_now(),
            result=result,
            publication_status=result["publication_status"],
            site_refresh_status=result["site_refresh_status"],
            commit_sha=result.get("commit_sha"),
            build_fingerprint=result.get("build_fingerprint"),
            failure_code=result.get("failure_code"),
        )
        return result

    def _publish_owned(self, draft_id: str, payload: Any, *, schedule_refresh: bool, operation_id: str) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        if self._platform_changes_pending(draft_id):
            raise AgentLifecycleError("The platform changeset requires independent verification and application.",
                                      code="runtime_changeset_integration_verification_required")
        if int(payload.expected_revision) != int(draft["revision"]):
            raise AgentLifecycleError("Agent draft revision changed.", code="agent_draft_conflict")
        self.require_technical_identity(draft)
        self._assert_identity_available(draft["agent_id"], draft=draft)
        package = self.store.get_agent_authoring_revision(draft_id, int(draft["revision"]))["package"]
        manifest = package["manifest"]
        summary = self._validation_summary(draft, package)
        report = self._formal_acceptance(draft, package)
        if not report or not summary["publishability"]["can_publish"]:
            raise AgentLifecycleError("Only a PASS Agent version can be published.", code="agent_validation_pass_required")
        if payload.validation_report_digest and report.get("report_digest") != payload.validation_report_digest:
            raise AgentLifecycleError("Agent validation report changed.", code="agent_validation_report_conflict")
        minimum = self._minimum_bump(draft, package)
        target_version = str(payload.target_version or draft.get("target_version") or manifest.get("version") or "0.1.0")
        base_version = draft.get("source_version") if draft_identity_kind(draft) == "version_upgrade" else None
        if base_version and _bump_rank(_bump_kind(base_version, target_version)) < _bump_rank(minimum):
            raise AgentLifecycleError(
                f"This change requires at least a {minimum} version bump.",
                code="agent_version_bump_too_low",
                detail={"minimum": minimum, "target_version": target_version},
            )
        manifest["version"] = target_version
        if draft["risk_class"] == "metadata_only":
            source = self._assert_expected(draft["agent_id"], draft["source_version"], draft["source_hash"])
            if (
                self._risk_class(draft, package) != "metadata_only"
                or not report.get("reused_validation")
                or not is_agent_executable(source)
                or report.get("source_validation_digest") != _json_digest(source.get("validation") or {})
                or report.get("execution_digest") != _execution_digest(manifest, package.get("rules"))
            ):
                raise AgentLifecycleError("The reusable acceptance changed; validate again.", code="agent_validation_report_conflict")
            # Preserve the original SAP date, acceptance mode and comparisons verbatim.
            # A documentation review is not another live SAP acceptance.
            manifest["validation"] = copy.deepcopy(report["source_validation"])
            manifest["validation"]["documentationReuse"] = {
                "sourceVersion": report["source_version"],
                "executionDigest": report["execution_digest"],
                "sourceValidationDigest": report["source_validation_digest"],
                "reviewedAt": report["validated_at"],
            }
        else:
            manifest["validation"] = _catalog_validation_from_formal_report(report)
            manifest["status"] = (
                "Three-stage live acceptance passed"
                if manifest["validation"]["acceptanceMode"] == "three_stage"
                else "Live acceptance passed"
            )
            package.setdefault("files", {})[
                manifest["validation"]["reportPath"]
            ] = _formal_acceptance_markdown(report, manifest)
        if bool(payload.activate):
            self._require_skill_dependencies(manifest)
        package = self._publication_package(draft, package)
        package = copy.deepcopy(package)
        package["readme"] = self._publication_readme_with_facts_block(
            package.get("readme")
        )
        # Every published package must satisfy the same catalog contract as the
        # site build.  This also verifies that the projected validation summary
        # points to a bundled Markdown acceptance report before Git is touched.
        self._validate_documentation_package(package)
        self._update_publication_operation(draft_id, operation_id, phase="preparing_worktree")
        try:
            base_sha = self._publication_workspace.assert_clean_main()
        except PublicationInfrastructureError as exc:
            raise AgentLifecycleError(str(exc), code=exc.code) from exc
        branch = self._publication_workspace.branch_name(
            draft["agent_id"], target_version, operation_id
        )
        self._update_publication_operation(
            draft_id,
            operation_id,
            branch=branch,
            base_sha=base_sha,
            validation_report_digest=report.get("report_digest"),
        )
        worktree: Path | None = None
        build: dict[str, Any] | None = None
        existing_dir = self._existing_directory(draft["agent_id"])
        agent_relative = (
            existing_dir.relative_to(self.settings.repository_root)
            if existing_dir is not None
            else Path("agents") / str(manifest.get("module") or "Common") / draft["agent_id"]
        )
        try:
            worktree = self._publication_workspace.create(operation_id, branch, base_sha)
            agent_dir = worktree / agent_relative
            agent_dir.mkdir(parents=True, exist_ok=True)
            version_dir = agent_dir / "versions" / target_version
            if version_dir.exists():
                raise AgentLifecycleError("Agent version already exists.", code="agent_version_exists")
            self._write_package(version_dir, package, manifest_override=manifest)
            self._write_json(version_dir / "validation.json", report)
            lifecycle = self._current_lifecycle_or_default(draft["agent_id"], manifest, agent_dir)
            catalog_module = str(
                lifecycle.get("catalog_module")
                or (draft.get("metadata") or {}).get("catalog_module")
                or manifest.get("module")
                or "Common"
            )
            lifecycle.update(
                schemaVersion=1,
                agent_id=draft["agent_id"],
                catalog_module=catalog_module,
                catalog_revision=int(lifecycle.get("catalog_revision") or 1),
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
                lifecycle.setdefault(
                    "lifecycle_state",
                    "inactive" if existing_dir is None else lifecycle.get("state", "active"),
                )
                lifecycle["state"] = lifecycle["lifecycle_state"]
                if existing_dir is None:
                    self._activate_package(agent_dir, version_dir, lifecycle, archive_current=False)
                    lifecycle.update(
                        active_version=None,
                        active_digest=None,
                        lifecycle_state="inactive",
                        state="inactive",
                    )
            self._write_json(agent_dir / "publication.json", lifecycle)
            self._refresh_catalog_documentation_at(worktree)
            if bool(payload.activate) and schedule_refresh:
                self._update_publication_operation(
                    draft_id, operation_id, phase="building_site", site_refresh_status="building"
                )
                build = self._site_release.prepare(
                    worktree,
                    operation_id=operation_id,
                    agent_id=draft["agent_id"],
                    version=target_version,
                    catalog_module=catalog_module,
                )
                self._update_publication_operation(
                    draft_id,
                    operation_id,
                    phase="checking_site",
                    build_fingerprint=build["fingerprint"],
                    site_refresh_status="prepared",
                )
            self._update_publication_operation(draft_id, operation_id, phase="saving_publication")
            try:
                commit_sha = self._publication_workspace.commit(
                    worktree,
                    f"Publish {draft['agent_id']} v{target_version}",
                    agent_relative,
                )
            except PublicationInfrastructureError as exc:
                raise AgentLifecycleError(str(exc), code=exc.code) from exc
            self._update_publication_operation(
                draft_id, operation_id, commit_sha=commit_sha, candidate_commit_sha=commit_sha
            )
            with deployment_file_lock(self.settings.data_root), self.agents.write_transaction():
                latest = self.store.get_agent_authoring_draft(draft_id)
                if int(latest["revision"]) != int(payload.expected_revision) or latest["status"] == "published":
                    raise AgentLifecycleError(
                        "Agent draft revision changed.", code="agent_draft_conflict"
                    )
                latest_report = self._formal_acceptance(latest, package)
                if not latest_report or latest_report.get("report_digest") != report.get("report_digest"):
                    raise AgentLifecycleError(
                        "Agent validation report changed.", code="agent_validation_report_conflict"
                    )
                try:
                    self._publication_workspace.fast_forward(commit_sha, base_sha)
                except PublicationInfrastructureError as exc:
                    raise AgentLifecycleError(str(exc), code=exc.code) from exc
            self._audit(
                draft["agent_id"],
                "published",
                base_version,
                target_version,
                agent_digest(manifest),
                branch,
                commit_sha,
                {"activated": bool(payload.activate), "base_sha": base_sha},
            )
            draft.update(
                status="published",
                target_version=target_version,
                validation={**report, "branch": branch, "commit_sha": commit_sha},
                updated_at=utc_now(),
            )
            self.store.save_agent_authoring_draft(
                draft,
                expected_revision=int(payload.expected_revision),
                operation_id=operation_id,
            )
            result = {
                "agent_id": draft["agent_id"],
                "version": target_version,
                "active": bool(payload.activate),
                "branch": branch,
                "commit_sha": commit_sha,
                "pushed": False,
                "publication_status": "published",
                "site_refresh_status": "not_required",
                "build_fingerprint": build.get("fingerprint") if build else None,
                "failure_code": None,
            }
            if build is not None:
                self._update_publication_operation(
                    draft_id, operation_id, phase="refreshing_site", publication_status="published",
                    site_refresh_status="switching",
                )
                try:
                    switched = self._site_release.switch(
                        build,
                        operation_id=operation_id,
                        agent_id=draft["agent_id"],
                        version=target_version,
                        catalog_module=catalog_module,
                    )
                except Exception as exc:
                    # The Git commit and draft state are authoritative now.
                    # A Web UI failure must never be reported as a failed or
                    # ambiguous Agent publication.
                    switched = {
                        "status": "failed",
                        "failure_code": str(getattr(exc, "code", "site_refresh_failed")),
                        "rolled_back": False,
                    }
                result["site_refresh_status"] = (
                    "completed" if switched.get("status") == "completed" else "failed"
                )
                result["failure_code"] = switched.get("failure_code")
                result["site_rolled_back"] = bool(switched.get("rolled_back"))
            return result
        except SiteReleaseError as exc:
            raise AgentLifecycleError(str(exc), code=exc.code) from exc
        except PublicationInfrastructureError as exc:
            raise AgentLifecycleError(str(exc), code=exc.code) from exc
        finally:
            self._publication_workspace.cleanup(worktree)

    def _execute_site_refresh(self, draft_id: str, operation_id: str) -> None:
        self._update_publication_operation(
            draft_id,
            operation_id,
            status="running",
            phase="building_site",
            started_at=utc_now(),
            site_refresh_status="building",
        )
        try:
            draft = self.store.get_agent_authoring_draft(draft_id)
            latest_publish = self.store.latest_agent_operation(draft_id, "publish") or {}
            published = ((latest_publish.get("detail") or {}).get("result") or {})
            agent_id = str(draft["agent_id"])
            manifest = self.agents.get(agent_id)
            version = str(published.get("version") or manifest.get("version") or "")
            catalog_module = self.agents.catalog_module(agent_id)
            self._publication_workspace.assert_clean_main()
            build = self._site_release.prepare(
                self.settings.repository_root,
                operation_id=operation_id,
                agent_id=agent_id,
                version=version,
                catalog_module=catalog_module,
            )
            self._update_publication_operation(
                draft_id,
                operation_id,
                phase="refreshing_site",
                site_refresh_status="switching",
                build_fingerprint=build["fingerprint"],
            )
            switched = self._site_release.switch(
                build,
                operation_id=operation_id,
                agent_id=agent_id,
                version=version,
                catalog_module=catalog_module,
            )
            success = switched.get("status") == "completed"
            result = {
                **published,
                "agent_id": agent_id,
                "version": version,
                "publication_status": "published",
                "site_refresh_status": "completed" if success else "failed",
                "build_fingerprint": build["fingerprint"],
                "failure_code": switched.get("failure_code"),
                "site_rolled_back": bool(switched.get("rolled_back")),
            }
            self._update_publication_operation(
                draft_id,
                operation_id,
                status="completed" if success else "failed",
                phase="completed",
                completed_at=utc_now(),
                result=result,
                publication_status="published",
                site_refresh_status=result["site_refresh_status"],
                build_fingerprint=build["fingerprint"],
                failure_code=result["failure_code"],
            )
        except Exception as exc:
            code = str(getattr(exc, "code", "site_refresh_failed"))
            self._update_publication_operation(
                draft_id,
                operation_id,
                status="failed",
                phase="completed",
                completed_at=utc_now(),
                publication_status="published",
                site_refresh_status="failed",
                failure_code=code,
            )

    def _update_publication_operation(
        self,
        draft_id: str,
        operation_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        **changes: Any,
    ) -> None:
        try:
            current = self.store.get_agent_operation_by_id(draft_id, operation_id)
        except KeyError:
            return
        detail = copy.deepcopy(current.get("detail") or {})
        if phase is not None:
            detail["phase"] = phase
        detail.update(changes)
        self.store.update_agent_operation(
            draft_id, operation_id, status=status, detail=detail
        )

    @staticmethod
    def _public_publication_operation(operation: dict[str, Any]) -> dict[str, Any]:
        detail = copy.deepcopy(operation.get("detail") or {})
        return {
            **detail,
            "operation_id": operation.get("operation_id"),
            "kind": operation.get("kind"),
            "status": operation.get("status"),
            "request_id": operation.get("request_id"),
            "revision": operation.get("revision"),
            "created_at": operation.get("created_at"),
            "updated_at": operation.get("updated_at"),
        }

    def reconcile_publications(self) -> None:
        """Repair the DB side of a publication whose prepared commit reached main."""

        for draft in self.store.list_agent_authoring_drafts():
            operation = self.store.latest_agent_operation(draft["draft_id"], "publish")
            if not operation or operation.get("status") != "interrupted":
                continue
            detail = copy.deepcopy(operation.get("detail") or {})
            candidate = str(detail.get("candidate_commit_sha") or detail.get("commit_sha") or "")
            version = str(detail.get("version") or draft.get("target_version") or "")
            if not candidate or not version or not self._publication_workspace.is_merged(candidate):
                self._publication_workspace.cleanup(
                    self._publication_workspace.worktree_root / operation["operation_id"]
                )
                continue
            try:
                manifest = self.agents.get_version(draft["agent_id"], version)
            except (KeyError, ManifestError):
                continue
            if draft.get("status") != "published":
                report = copy.deepcopy(draft.get("validation") or {})
                report.update(branch=detail.get("branch"), commit_sha=candidate)
                draft.update(
                    status="published",
                    target_version=version,
                    validation=report,
                    updated_at=utc_now(),
                )
                try:
                    self.store.save_agent_authoring_draft(
                        draft, expected_revision=int(draft["revision"])
                    )
                except ValueError:
                    self._publication_workspace.cleanup(
                        self._publication_workspace.worktree_root / operation["operation_id"]
                    )
                    continue
            activated = bool(detail.get("activate"))
            refreshed = bool(
                activated
                and detail.get("build_fingerprint")
                and self._site_release.current_fingerprint() == detail.get("build_fingerprint")
            )
            result = {
                "agent_id": draft["agent_id"],
                "version": version,
                "active": activated,
                "branch": detail.get("branch"),
                "commit_sha": candidate,
                "pushed": False,
                "publication_status": "published",
                "site_refresh_status": (
                    "completed" if refreshed else "failed" if activated else "not_required"
                ),
                "build_fingerprint": detail.get("build_fingerprint"),
                "failure_code": "site_refresh_required" if activated and not refreshed else None,
            }
            detail.update(
                phase="completed",
                completed_at=utc_now(),
                publication_status="published",
                site_refresh_status=result["site_refresh_status"],
                failure_code=result["failure_code"],
                result=result,
            )
            self.store.reconcile_interrupted_agent_operation(
                draft["draft_id"], operation["operation_id"], status="completed", detail=detail
            )
            self.store.register_published_agent_identities({draft["agent_id"]})
            self._publication_workspace.cleanup(
                self._publication_workspace.worktree_root / operation["operation_id"]
            )

    def _refresh_catalog_documentation_at(self, repository_root: Path) -> None:
        script = repository_root / "scripts" / "documentation.py"
        if not script.is_file():
            return
        result = subprocess.run(
            [sys.executable, str(script), "--write"],
            cwd=repository_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        if result.returncode != 0:
            raise AgentLifecycleError(
                "Agent documentation indexes could not be refreshed.",
                code="agent_documentation_refresh_failed",
            )

    @staticmethod
    def _validate_documentation_package(package: dict[str, Any]) -> None:
        """Use the same complete manifest contract as the site, before any Git writes."""
        validator = Path(__file__).resolve().parents[2] / "site" / "scripts" / "validate-agent-package.mjs"
        try:
            result = subprocess.run(
                ["node", str(validator)], input=json.dumps(package, ensure_ascii=False),
                encoding="utf-8", capture_output=True, timeout=20, check=False,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AgentLifecycleError("Package validation is unavailable.", code="agent_package_validation_unavailable") from exc
        if result.returncode:
            # Do not expose package bodies through command or validation errors.
            raise AgentLifecycleError("The complete Agent package failed catalog validation.", code="agent_package_invalid")

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
        self._require_skill_dependencies(candidate)
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
        activation_blockers: list[str] = []
        if lifecycle["state"] != "inactive":
            activation_blockers.append("agent_must_be_inactive")
        else:
            target_version = str(lifecycle.get("latest_version") or "")
            try:
                candidate = self.agents.get_version(agent_id, target_version)
            except KeyError:
                activation_blockers.append("agent_version_not_found")
            else:
                if not is_agent_executable(candidate):
                    activation_blockers.append("agent_validation_pass_required")
                else:
                    try:
                        self._require_skill_dependencies(candidate)
                    except AgentLifecycleError as exc:
                        activation_blockers.append(exc.code)
        return {
            "can_create_version": not self._open_drafts(agent_id),
            "can_deactivate": lifecycle["state"] == "active",
            "can_activate": lifecycle["state"] == "inactive" and not activation_blockers,
            "activate_blockers": activation_blockers,
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
        if draft_identity_kind(draft) != "version_upgrade" or not draft.get("source_version"):
            return "behavior_change"
        try:
            source = self.agents.package(draft["agent_id"], draft["source_version"], draft.get("source_hash"))
        except (KeyError, ManifestError):
            return "behavior_change"
        return "metadata_only" if _execution_digest(source["manifest"], source.get("rules_source")) == _execution_digest(package["manifest"], package.get("rules")) else "behavior_change"

    def _minimum_bump(self, draft: dict[str, Any], package: dict[str, Any]) -> str:
        if draft_identity_kind(draft) != "version_upgrade" or not draft.get("source_version"):
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

    def _draft_delete_blockers(self, draft: dict[str, Any]) -> list[str]:
        if draft["status"] in {"published", "cancelled"}:
            return [f"agent_draft_{draft['status']}"]
        operation = self.store.get_agent_operation(draft["draft_id"])
        if operation and operation["kind"] != "delete":
            return ["agent_draft_operation_active"]
        run_id = draft.get("validation_run_id")
        if not run_id:
            return []
        try:
            run = self.store.get_run(str(run_id))
        except KeyError:
            return ["agent_draft_validation_state_unknown"] if draft["status"] == "validating" else []
        return [] if run.status in TERMINAL_STATUSES else ["agent_draft_validation_active"]

    @staticmethod
    def _snapshot_draft_package(path: Path) -> dict[str, bytes]:
        snapshot: dict[str, bytes] = {}
        for item in path.rglob("*"):
            if item.is_symlink():
                raise AgentLifecycleError(
                    "Agent draft package contains a symbolic link.",
                    code="agent_draft_path_invalid",
                )
            if item.is_file():
                snapshot[item.relative_to(path).as_posix()] = item.read_bytes()
        return snapshot

    @staticmethod
    def _restore_draft_package(path: Path, snapshot: dict[str, bytes]) -> None:
        path.mkdir(parents=True, exist_ok=True)
        for relative, content in snapshot.items():
            target = path.joinpath(*relative.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

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

    def _draft_catalog_metadata(
        self, draft: dict[str, Any], package: dict[str, Any]
    ) -> tuple[str, int, str | None]:
        manifest = package.get("manifest") if isinstance(package, dict) else {}
        if draft_identity_kind(draft) == "version_upgrade":
            try:
                lifecycle = self.agents.lifecycle(draft["agent_id"])
                return (
                    self.agents.catalog_module(draft["agent_id"]),
                    int(lifecycle.get("catalog_revision") or 1),
                    self.agents.repository_module(draft["agent_id"]),
                )
            except (KeyError, ManifestError):
                pass
        metadata = draft.get("metadata") or {}
        module = str(
            metadata.get("catalog_module") or (manifest or {}).get("module") or "Common"
        )
        return module, int(metadata.get("catalog_revision") or 1), None

    def _has_publishable_changes(
        self, draft: dict[str, Any], package: dict[str, Any]
    ) -> bool:
        if draft_identity_kind(draft) != "version_upgrade":
            return True
        try:
            source_directory = Path(
                self.agents.package(draft["agent_id"], draft["source_version"])["directory"]
            )
            source = self._capture_package(source_directory)
        except (KeyError, AgentLifecycleError, ManifestError):
            return True

        def normalized(value: dict[str, Any]) -> dict[str, Any]:
            result = copy.deepcopy(value)
            manifest = result.get("manifest") or {}
            for key in ("module", "status", "validation", "version"):
                manifest.pop(key, None)
            return result

        return normalized(source) != normalized(package)

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
            if source.is_dir() or _transient_package_file(source.relative_to(directory)) or "versions" in source.relative_to(directory).parts or source.name == "publication.json":
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

    def _catalog_documentation_snapshot(self) -> dict[Path, bytes]:
        root = self.settings.repository_root
        paths = [root / "README.md"]
        paths.extend((root / "agents").rglob("README.md"))
        paths.extend((root / "workflows").rglob("README.md"))
        return {path: path.read_bytes() for path in paths if path.is_file()}

    def _refresh_catalog_documentation(self) -> list[Path]:
        script = self.settings.repository_root / "scripts" / "documentation.py"
        if not script.is_file():
            return []
        subprocess.run(
            [sys.executable, str(script), "--write"],
            cwd=self.settings.repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
        changed = subprocess.run(
            ["git", "diff", "--name-only", "--", "README.md", "agents", "workflows"],
            cwd=self.settings.repository_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        return [
            self.settings.repository_root / path
            for path in changed
            if path.replace("\\", "/").endswith("README.md")
        ]

    def _commit_agent_change(
        self,
        directory: Path,
        message: str,
        *,
        deleted: bool = False,
        additional_paths: list[Path] | None = None,
    ) -> str:
        relative = directory.resolve().relative_to(self.settings.repository_root.resolve()) if not deleted else directory.relative_to(self.settings.repository_root)
        relatives = [relative]
        for path in additional_paths or []:
            resolved = path.resolve()
            try:
                candidate = resolved.relative_to(self.settings.repository_root.resolve())
            except ValueError as exc:
                raise AgentLifecycleError(
                    "Agent management attempted to stage a file outside the repository.",
                    code="agent_management_path_invalid",
                ) from exc
            if candidate not in relatives:
                relatives.append(candidate)
        subprocess.run(["git", "add", "-A", "--", *(str(path) for path in relatives)], cwd=self.settings.repository_root, check=True, capture_output=True, text=True)
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
        escaped_log_path = str(log_path).replace("'", "''")
        command = (
            "Start-Sleep -Seconds 2; "
            f"& '{escaped}' -Restart -RebuildSite -NoBrowser *>> '{escaped_log_path}'"
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

    @staticmethod
    def _publication_package(draft: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
        if not draft.get("metadata", {}).get("source_draft_id"):
            return package
        # Keep source samples and attachments in the ignored management package.
        # Only the generated reviewable code/docs accompany a published Agent.
        public = copy.deepcopy(package)
        public["files"] = {name: value for name, value in package.get("files", {}).items() if name in _GENERATED_PUBLIC_FILES}
        public.pop("binary_files", None)
        return public

    @staticmethod
    def _publication_readme_with_facts_block(readme: Any) -> str:
        """Ensure documentation.py can refresh a newly published Agent README."""

        start = "<!-- generated:facts:start -->"
        end = "<!-- generated:facts:end -->"
        text = str(readme or "# Agent\n")
        start_count = text.count(start)
        end_count = text.count(end)
        if start_count == 1 and end_count == 1 and text.index(start) < text.index(end):
            return text
        if start_count or end_count:
            raise AgentLifecycleError(
                "The Agent README has an incomplete or duplicate generated facts block.",
                code="agent_documentation_block_invalid",
            )
        if not text.endswith("\n"):
            text += "\n"
        if not text.endswith("\n\n"):
            text += "\n"
        return f"{text}{start}\n{end}\n"

    def _capture_package(self, directory: Path) -> dict[str, Any]:
        excluded = {"versions", "__pycache__", ".pytest_cache", ".git", ".venv", "node_modules"}
        paths = [path for path in directory.rglob("*") if not excluded.intersection(path.relative_to(directory).parts)]
        # Check every entry before reading even the manifest or README.
        for path in paths:
            if path.is_symlink() or directory.resolve() not in path.resolve().parents:
                raise AgentLifecycleError("Agent package contains an external file link.", code="agent_draft_path_invalid")
        manifest = self._read_json(directory / "agent.json")
        files: dict[str, str] = {}
        binary_files: dict[str, str] = {}
        for path in paths:
            if path.is_dir() or _transient_package_file(path.relative_to(directory)) or "versions" in path.relative_to(directory).parts or path.relative_to(directory).as_posix() in {"agent.json", "README.md", "rules.py", "publication.json", "validation.json"}:
                continue
            try:
                files[path.relative_to(directory).as_posix()] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                binary_files[path.relative_to(directory).as_posix()] = base64.b64encode(path.read_bytes()).decode("ascii")
        package = {"manifest": manifest, "readme": self._read_optional(directory / "README.md"), "rules": self._read_optional(directory / "rules.py") or None, "files": files}
        if binary_files:
            package["binary_files"] = binary_files
        return package

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
        for name, content in (package.get("binary_files") or {}).items():
            target = (directory / name).resolve()
            if directory.resolve() not in target.parents:
                raise AgentLifecycleError("Agent attachment escaped its directory.", code="agent_package_path_invalid")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(content, validate=True))

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
                "relationshipPolicy": "advisory",
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


def _transient_package_file(path: Path) -> bool:
    """Runtime caches and local environments are not immutable release artifacts."""
    return (
        bool(set(path.parts) & {"__pycache__", ".pytest_cache", ".local", ".local-data", ".venv", "node_modules"})
        or path.suffix in {".pyc", ".pyo"}
        or (path.name.startswith(".env") and path.name != ".env.example")
    )


_GENERATED_PUBLIC_FILES = {
    "content.zh.md", "content.en.md", "src/rules.py", "src/rule-review-notes.json",
    "tests/test_manifest_contract.py", "docs/data-contract.json",
    "docs/formal-acceptance.md",
}


def _json_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _not_tested_validation() -> dict[str, Any]:
    return {"verdict": "NOT_TESTED", "executable": False, "acceptanceMode": "three_stage", "fixedAgentComparison": "NOT_TESTED", "freeQueryComparison": "NOT_TESTED"}


def _catalog_validation_from_formal_report(report: dict[str, Any]) -> dict[str, Any]:
    """Project a detailed formal Campaign into the public Agent manifest contract."""

    source = report.get("agent_validation")
    if isinstance(source, dict) and all(
        source.get(field) is not None
        for field in ("testedAt", "evidenceScope", "providers", "summary", "reportPath")
    ):
        return copy.deepcopy(source)

    mode = str(report.get("acceptanceMode") or "three_stage")
    free_comparison = str(report.get("freeQueryComparison") or "NOT_TESTED")
    if mode == "deterministic_runtime" and free_comparison == "NOT_APPLICABLE":
        free_comparison = "NOT_TESTED"

    normalized_results = [
        ((case.get("baseline") or {}).get("normalized_result") or {})
        for case in report.get("cases") or []
        if isinstance(case, dict)
    ]
    complete = bool(normalized_results) and all(
        result.get("source_complete") is True
        and result.get("evidence_complete") is True
        and result.get("business_complete") is True
        for result in normalized_results
    )
    evidence_scope = "complete" if complete else "bounded"

    providers = ["codex-sdk-direct-sap", "embedded-sap-odata"]
    if mode == "three_stage":
        providers.insert(1, "free-query")

    verdict = str(report.get("verdict") or "NOT_TESTED")
    if verdict == "PASS":
        summary = {
            "zh": (
                "独立SAP基线、自由查询和固定Agent的业务语义一致。"
                if mode == "three_stage"
                else "独立SAP基线与固定Agent的业务语义一致。"
            ),
            "en": (
                "The independent SAP baseline, free query, and fixed Agent are semantically consistent."
                if mode == "three_stage"
                else "The independent SAP baseline and fixed Agent are semantically consistent."
            ),
        }
    else:
        summary = {
            "zh": "正式验收未通过；请查看验收报告中的差异或阻断原因。",
            "en": "Formal acceptance did not pass; review the report for differences or blockers.",
        }

    return {
        "verdict": verdict,
        "testedAt": str(
            report.get("tested_at")
            or report.get("completed_at")
            or report.get("testedAt")
            or utc_now()
        ),
        "evidenceScope": evidence_scope,
        "providers": providers,
        "summary": summary,
        "reportPath": "docs/formal-acceptance.md",
        "executable": report.get("executable") is True,
        "acceptanceMode": mode,
        "freeQueryComparison": free_comparison,
        "fixedAgentComparison": str(
            report.get("fixedAgentComparison") or "NOT_TESTED"
        ),
        "blockingLimitations": copy.deepcopy(
            report.get("blockingLimitations") or []
        ),
        "baselineRuntime": "codex_sdk_direct_sap",
        "usedSapBusinessAgentsForBaseline": False,
        "campaignId": report.get("campaign_id"),
        "reportDigest": report.get("report_digest"),
    }


def _formal_acceptance_markdown(
    report: dict[str, Any], manifest: dict[str, Any]
) -> str:
    """Create a public, non-sensitive summary while validation.json retains detail."""

    title = manifest.get("title") or {}
    lines = [
        "# 正式验收 / Formal acceptance",
        "",
        f"- Agent: `{manifest.get('slug') or report.get('agent_id') or ''}`",
        f"- 名称 / Name: {title.get('zh') or ''} / {title.get('en') or ''}",
        f"- 判定 / Verdict: `{report.get('verdict') or 'NOT_TESTED'}`",
        f"- 模式 / Mode: `{report.get('acceptanceMode') or 'three_stage'}`",
        f"- 验收时间 / Tested at: `{report.get('tested_at') or report.get('completed_at') or ''}`",
        f"- Campaign: `{report.get('campaign_id') or ''}`",
        f"- 只读审计 / Read-only audit: `{'PASS' if report.get('read_only_audit') is True else 'NOT_TESTED'}`",
        "",
        "## 案例 / Cases",
        "",
        "| Case | Verdict | Baseline | Free query | Fixed Agent | Source anchors |",
        "|---|---|---|---|---|---|",
    ]
    for case in report.get("cases") or []:
        if not isinstance(case, dict):
            continue
        baseline = case.get("baseline") or {}
        free_query = case.get("free_query") or {}
        fixed_agent = case.get("fixed_agent") or {}
        anchors = case.get("source_anchors") or {}
        lines.append(
            "| {case_id} | {verdict} | {baseline} | {free} | {fixed} | {anchors} |".format(
                case_id=case.get("case_id") or "",
                verdict=case.get("verdict") or "NOT_TESTED",
                baseline="PASS" if baseline.get("normalized_result") else "NOT_TESTED",
                free=(free_query.get("comparison") or {}).get("verdict") or "NOT_APPLICABLE",
                fixed=(fixed_agent.get("comparison") or {}).get("verdict") or "NOT_TESTED",
                anchors=anchors.get("verdict") or "NOT_TESTED",
            )
        )
    lines.extend(
        [
            "",
            "完整的不可变验收记录保存在同版本目录的 `validation.json`。",
            "The complete immutable acceptance record is stored in `validation.json` in this version directory.",
            "",
        ]
    )
    return "\n".join(lines)


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
