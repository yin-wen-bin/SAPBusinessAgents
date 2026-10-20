"""Small local atomic journal and scoped source archive, independent of SDKs."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from .runtime_validation import ValidationError, digest
from .runtime_validation_inventory import sha


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


_threads: dict[str, threading.RLock] = {}
_threads_lock = threading.Lock()


@contextmanager
def locked(path):
    path = Path(path).resolve()
    with _threads_lock:
        local = _threads.setdefault(str(path), threading.RLock())
    with local:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as stream:
            # Windows permits locking beyond EOF. Never read/write the lock
            # byte before ownership: another process may already hold it.
            stream.seek(0)
            until = time.monotonic() + 2
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() >= until:
                        raise ValidationError("validation_store_busy") from exc
                    time.sleep(.01)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)


def relative_file(root, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
        raise ValidationError("validation_artifact_path_invalid")
    root = Path(root).resolve()
    path = root.joinpath(*relative.parts)
    if not path.resolve().is_relative_to(root):
        raise ValidationError("validation_artifact_path_invalid")
    if any(part.startswith(".env") or part in {".git", ".local-data", "__pycache__", "node_modules", ".venv"} for part in relative.parts):
        raise ValidationError("validation_source_not_allowed")
    return path


def capture(root, names, destination):
    """Explicit reviewed file list only. Never scans credentials or environments."""
    root, destination = Path(root).resolve(), Path(destination).resolve()
    values, content = {}, {}
    for name in sorted(set(names)):
        path = relative_file(root, name)
        if path.suffix not in {".py", ".json", ".md", ".toml", ".yaml", ".yml", ".txt"}:
            raise ValidationError("validation_source_not_allowed")
        content[name] = path.read_bytes()
        if path.suffix == '.json':
            def sensitive(value):
                if isinstance(value, dict):
                    return any((str(k).lower().replace('-', '_') in {'password', 'api_key', 'access_token', 'refresh_token', 'client_secret', 'authorization'}
                        and v not in (None, '', False)) or sensitive(v) for k, v in value.items())
                return isinstance(value, list) and any(sensitive(v) for v in value)
            if sensitive(json.loads(content[name])):
                raise ValidationError('validation_sensitive_source_rejected')
        import hashlib
        values[name] = hashlib.sha256(content[name]).hexdigest()
    # A concurrent edit cannot silently combine old and new bytes.
    if any(sha(relative_file(root, name)) != value for name, value in values.items()):
        raise ValidationError("validation_source_changed")
    body = {"version": "sapba.validation.source/1", "files": values}
    identifier = digest(body)
    target = destination / identifier
    with locked(destination / "archive.lock"):
        if (target / "manifest.json").exists():
            verify_archive(target, identifier)
        else:
            for name, value in content.items():
                path = target / "source" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value)
            atomic(target / "manifest.json", {**body, "digest": identifier})
    return {"digest": identifier, "directory": str(target)}


def verify_archive(path, expected):
    path = Path(path)
    manifest = read(path / "manifest.json")
    if manifest.get("digest") != expected or digest({k: v for k, v in manifest.items() if k != "digest"}) != expected:
        raise ValidationError("validation_source_manifest_changed")
    actual = {p.relative_to(path / 'source').as_posix() for p in (path / 'source').rglob('*') if p.is_file()}
    if actual != set(manifest['files']):
        raise ValidationError('validation_source_inventory_changed')
    if any(sha(relative_file(path / "source", name)) != value for name, value in manifest["files"].items()):
        raise ValidationError("validation_source_changed")
    return manifest


def process_binding(archive):
    manifest = verify_archive(archive["directory"], archive["digest"])
    source = (Path(archive["directory"]) / "source").resolve()
    if not Path(__file__).resolve().is_relative_to(source):
        raise ValidationError("validation_process_not_frozen")
    # Reject a package preloaded from the working checkout/site-packages.
    for name, module in tuple(sys.modules.items()):
        if (name.startswith("sap_business_agents_platform") or name.startswith('scripts.')) and getattr(module, "__file__", None):
            path = Path(module.__file__).resolve()
            if not path.is_relative_to(source) or path.relative_to(source).as_posix() not in manifest["files"]:
                raise ValidationError("validation_loaded_code_mismatch")
    return {"pid": os.getpid(), "interpreter": sys.executable, "source_digest": archive["digest"],
        "started_marker": _STARTED}


_STARTED = uuid.uuid4().hex


class BatchStore:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()

    def claim(self, case, binding, grant, target, parameters):
        identity = {"case_id": case.id, "case_digest": case.digest, "mode": case.mode,
            "binding_digest": digest(binding), "batch": self.directory.name}
        key = digest(identity)
        fingerprint = digest({"identity": identity, "authorization": asdict(grant), "target": target, "parameters": parameters})
        path = self.directory / "attempts" / (key + ".json")
        with locked(self.directory / "dispatch.lock"):
            if path.exists():
                value = read(path)
                if value["fingerprint"] != fingerprint:
                    raise ValidationError("validation_request_conflict")
                return False, value
            value = {**identity, "attempt_id": key, "job_id": "validation_" + uuid.uuid4().hex,
                "fingerprint": fingerprint, "status": "claimed", "created_at": time.time()}
            atomic(path, value)
            return True, value

    def finish(self, claim, result):
        path = self.directory / "attempts" / (claim["attempt_id"] + ".json")
        with locked(self.directory / "dispatch.lock"):
            value = read(path)
            if value["job_id"] != claim["job_id"] or value["fingerprint"] != claim["fingerprint"]:
                raise ValidationError("validation_result_binding_mismatch")
            if "result" in value:
                if value["result"] != result:
                    raise ValidationError("validation_late_result")
                return
            if value["status"] not in {"claimed", "running"}:
                raise ValidationError("validation_late_result")
            atomic(path, {**value, "status": result["status"], "result": result})

    @staticmethod
    def existing_result(record):
        # Missing completion requires owned-job reconciliation, never redispatch.
        return record.get("result", {"case_id": record["case_id"], "job_id": record["job_id"], "status": record["status"]})

    def cancel(self, claim):
        self.finish(claim, {"case_id": claim["case_id"], "job_id": claim["job_id"],
            "status": "cleanup_pending", "failure_code": "validation_cancelled"})

    def reconcile(self, claim, evidence):
        """Append a resolution; a missing process/report alone proves nothing."""
        path = self.directory / "attempts" / (claim["attempt_id"] + ".json")
        if evidence.get("job_id") != claim["job_id"] or evidence.get("cleanup_complete") is not True or not evidence.get("artifact"):
            raise ValidationError("validation_cleanup_unproven")
        verify_artifacts(self.directory, [evidence["artifact"]])
        proof = read(self.directory / evidence["artifact"]["path"])
        if proof.get("job_id") != claim["job_id"] or proof.get("cleanup_complete") is not True or not proof.get("owned_process_start_id"):
            raise ValidationError("validation_cleanup_unproven")
        with locked(self.directory / "dispatch.lock"):
            value = read(path)
            if value.get("status") not in {"claimed", "running", "cleanup_pending"}:
                raise ValidationError("validation_reconciliation_not_applicable")
            resolution = {"status": "interrupted", "job_id": claim["job_id"], "case_id": claim["case_id"], "evidence": evidence}
            atomic(path, {**value, "status": "interrupted", "resolution": resolution,
                "result": {"case_id": claim["case_id"], "job_id": claim["job_id"], "status": "interrupted"}})


def artifact(root, path):
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root):
        raise ValidationError("validation_artifact_path_invalid")
    return {"path": path.relative_to(root).as_posix(), "sha256": sha(path)}


def verify_artifacts(root, values):
    if not values:
        raise ValidationError("validation_artifacts_missing")
    root = Path(root).resolve()
    for value in values:
        # Evidence may live in a protected batch, but never outside its bound root.
        path = root / value["path"]
        if not path.resolve().is_relative_to(root) or sha(path) != value["sha256"]:
            raise ValidationError("validation_artifact_changed")
