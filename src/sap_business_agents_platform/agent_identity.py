"""Draft identity rules. Runtime-authored content cannot claim or rename identities."""
from __future__ import annotations

import copy
import re
from typing import Any

from .models import utc_now

_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", "clock$", "conin$", "conout$"} | {
    f"{prefix}{index}" for prefix in ("com", "lpt") for index in range(1, 10)
}


def validate_agent_id(value: str) -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if not 3 <= len(normalized) <= 80 or not _ID.fullmatch(normalized) or normalized in _WINDOWS_RESERVED:
        raise ValueError("agent_technical_id_invalid")
    return normalized


def draft_identity_kind(draft: dict[str, Any]) -> str:
    metadata = draft.get("metadata") or {}
    declared = (metadata.get("identity") or {}).get("kind")
    if declared in {"new_agent", "version_upgrade"}:
        return declared
    if metadata.get("version_origin") and draft.get("source_version"):
        return "version_upgrade"
    if draft.get("source_type") in {"blank", "free_query", "workflow_gap"}:
        return "new_agent"
    if draft.get("source_type") == "clone" and (metadata.get("origin") or {}).get("sourceAgentId"):
        return "new_agent"
    return "unknown"


class AgentIdentityMixin:
    def _save_initial_identity(self, draft: dict[str, Any], package: dict[str, Any]) -> None:
        try:
            self.store.save_agent_authoring_draft(draft, package=package, diff=[])
        except Exception as exc:
            # Only this newly-created draft projection is eligible for rollback.
            path = self._draft_path(draft["draft_id"])
            try:
                self.store.get_agent_authoring_draft(draft["draft_id"])
            except KeyError:
                if path.resolve().parent == self.draft_root and path.exists():
                    import shutil
                    shutil.rmtree(path)
            if isinstance(exc, ValueError):
                raise self._identity_error(str(exc)) from exc
            raise

    def _identity_error(self, code: str) -> Exception:
        return self._authoring_error("Agent technical identity could not be confirmed; reload or choose another ID.", code)

    def _registered_agent_ids(self) -> set[str]:
        # Include residual directories and inactive/version-only packages, not just runnable catalog entries.
        root = self.settings.repository_root / "agents"
        return {path.name for path in root.glob("*/*") if path.is_dir()}

    def technical_identity(self, draft: dict[str, Any]) -> dict[str, Any]:
        kind = draft_identity_kind(draft)
        identity = (draft.get("metadata") or {}).get("identity") or {}
        locked = draft.get("status") == "published" or kind == "version_upgrade"
        confirmed = locked or identity.get("confirmed_agent_id") == draft["agent_id"]
        blockers = []
        if kind == "unknown":
            blockers.append("agent_identity_unknown")
        if draft.get("status") in {"published", "cancelled"}:
            blockers.append("agent_draft_published" if draft["status"] == "published" else "agent_draft_cancelled")
        return {"kind": kind, "agent_id": draft["agent_id"], "confirmed": confirmed,
                "locked": locked, "can_rename": kind == "new_agent" and not locked and not blockers,
                "blockers": blockers}

    def require_technical_identity(self, draft: dict[str, Any] | str) -> None:
        if isinstance(draft, str):
            draft = self.store.get_agent_authoring_draft(draft)
        identity = self.technical_identity(draft)
        if identity["kind"] == "unknown":
            raise self._identity_error("agent_identity_unknown")
        if not identity["confirmed"]:
            raise self._identity_error("agent_technical_id_confirmation_required")

    def _assert_identity_available(self, agent_id: str, *, draft: dict[str, Any] | None = None) -> str:
        try:
            normalized = validate_agent_id(agent_id)
        except ValueError as exc:
            raise self._identity_error(str(exc)) from exc
        upgrade = draft is not None and draft_identity_kind(draft) == "version_upgrade"
        if not upgrade and normalized in self._registered_agent_ids():
            raise self._identity_error("agent_technical_id_taken")
        if self.store.agent_identity_taken(normalized, draft_id=(draft or {}).get("draft_id"), allow_published=upgrade):
            raise self._identity_error("agent_technical_id_taken")
        return normalized

    def check_technical_id(self, draft_id: str, agent_id: str) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        identity = self.technical_identity(draft)
        if not identity["can_rename"]:
            raise self._identity_error(identity["blockers"][0] if identity["blockers"] else "agent_id_immutable")
        normalized = self._assert_identity_available(agent_id, draft=draft)
        return {"agent_id": normalized, "available": True, "reserved": False,
                "revision": draft["revision"]}

    def set_technical_id(self, draft_id: str, payload: Any) -> dict[str, Any]:
        operation = self._reserve_operation(draft_id, int(payload.expected_revision), "technical_id")
        try:
            draft = self.store.get_agent_authoring_draft(draft_id)
            self._assert_editable(draft)
            checked = self.check_technical_id(draft_id, payload.agent_id)
            normalized = checked["agent_id"]
            if normalized != draft["agent_id"]:
                package = copy.deepcopy(self.store.get_agent_authoring_revision(draft_id, int(draft["revision"]))["package"])
                package["manifest"]["slug"] = normalized
                return self._apply_package(draft_id, int(draft["revision"]), package, kind="technical_id",
                                           operation_id=operation["operation_id"])
            # Confirmation is metadata-only and must not create a fake content revision.
            metadata = copy.deepcopy(draft.get("metadata") or {})
            metadata["identity"] = {**metadata.get("identity", {}), "kind": "new_agent",
                                    "confirmed_agent_id": normalized, "confirmed_at": utc_now()}
            draft.update(metadata=metadata, updated_at=utc_now())
            self.store.save_agent_authoring_draft(draft, expected_revision=int(payload.expected_revision),
                                                  operation_id=operation["operation_id"])
            return self.get_draft(draft_id)
        finally:
            self._finish_operation(draft_id, operation["operation_id"])

    def _prepare_identity_package(self, draft: dict[str, Any], package: dict[str, Any], *, kind: str) -> bool:
        manifest = package.get("manifest")
        if not isinstance(manifest, dict) or manifest.get("slug") == draft["agent_id"]:
            return False
        if kind not in {"technical_id", "undo"}:
            raise self._identity_error("agent_id_immutable")
        identity = self.technical_identity(draft)
        if not identity["can_rename"]:
            raise self._identity_error("agent_identity_unknown" if identity["kind"] == "unknown" else "agent_id_immutable")
        normalized = self._assert_identity_available(manifest.get("slug"), draft=draft)
        manifest["slug"] = normalized
        metadata = copy.deepcopy(draft.get("metadata") or {})
        if draft.get("validation") or metadata.get("static_checks") or metadata.get("trial"):
            history = list(metadata.get("identity_invalidated_validations") or [])
            history.append({"agent_id": draft["agent_id"], "revision": draft["revision"],
                            "validation": copy.deepcopy(draft.get("validation") or {}),
                            "static_checks": copy.deepcopy(metadata.get("static_checks")),
                            "trial": copy.deepcopy(metadata.get("trial")),
                            "applicable_to_current_revision": False, "invalidated_at": utc_now()})
            metadata["identity_invalidated_validations"] = history
        metadata["identity"] = {**metadata.get("identity", {}), "kind": "new_agent",
                                "confirmed_agent_id": normalized, "confirmed_at": utc_now(),
                                "validation_invalidated_revision": int(draft["revision"]) + 1,
                                "previous_agent_id": draft["agent_id"]}
        # Keep the old evidence in immutable revisions/attempts; never carry current eligibility forward.
        metadata.update(static_checks=None, trial=None, sample_discovery=None)
        draft.update(agent_id=normalized, metadata=metadata, thread_id=None)
        return True

    @staticmethod
    def _not_tested_identity_validation() -> dict[str, Any]:
        from .agent_lifecycle import _not_tested_validation
        return _not_tested_validation()
