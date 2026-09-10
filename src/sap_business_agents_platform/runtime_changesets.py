"""Controller-owned pending code proposals; no model-accessible apply operation.

The independent integration/health verifier must certify a proposal before the
application service can apply it. An SDK test claim never supplies that evidence.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .authoring_harness import content_digest
from .authoring_workspace import _allowed_source, _PROTECTED, safe_relative
from .runtime_execution import RuntimeExecutionError

_KEY = re.compile(r"(?:sk-[A-Za-z0-9_-]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")


class RuntimeChangeSets:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._lock = threading.RLock()

    def _path(self, change_set_id: str) -> Path:
        if not re.fullmatch(r"rcs_[0-9a-f]{32}", change_set_id):
            raise RuntimeExecutionError("runtime_changeset_not_found")
        return self.root / (change_set_id + ".json")

    def create(self, workspace: Any, *, source_id: str, candidate_digest: str = "") -> dict[str, Any] | None:
        changes = workspace.platform_changes()
        if not changes:
            return None
        for change in changes:
            name = change["path"]
            safe_relative(name)
            if not _allowed_source(name) or Path(name).name in _PROTECTED:
                raise RuntimeExecutionError("runtime_changeset_protected_path")
            if any(_KEY.search(str(change.get(key) or "")) for key in ("before", "after")):
                raise RuntimeExecutionError("runtime_changeset_sensitive_content")
        record = {
            "change_set_id": "rcs_" + uuid.uuid4().hex, "revision": 1,
            "source_id": source_id, "base_commit": workspace.base_commit,
            "base_digest": content_digest(workspace.base_files),
            "candidate_digest": candidate_digest, "changes": changes,
            "status": "awaiting_verification", "can_apply": False,
            "blockers": ["runtime_changeset_integration_verification_required"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        record["digest"] = content_digest(record)
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(record["change_set_id"])
            temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
            try:
                with temporary.open("x", encoding="utf-8") as stream:
                    json.dump(record, stream, ensure_ascii=False, allow_nan=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return record

    def get(self, change_set_id: str) -> dict[str, Any]:
        with self._lock:
            try:
                result = json.loads(self._path(change_set_id).read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise RuntimeExecutionError("runtime_changeset_not_found") from None
            expected = result.pop("digest", None)
            if not expected or content_digest(result) != expected:
                raise RuntimeExecutionError("runtime_changeset_digest_mismatch")
            return {**result, "digest": expected}

    def require_applicable(self, change_set_id: str, *, revision: int, digest: str) -> dict[str, Any]:
        record = self.get(change_set_id)
        if record["revision"] != revision or record["digest"] != digest:
            raise RuntimeExecutionError("runtime_changeset_conflict")
        # This repository currently has no independent platform integration and
        # supervised-restart certification. Fail closed, never trust self-reported
        # tests or make a pretend successful apply response.
        raise RuntimeExecutionError("runtime_changeset_integration_verification_required")
