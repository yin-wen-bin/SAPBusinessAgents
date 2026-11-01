from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


class TestAgentError(ValueError):
    __test__ = False

    def __init__(self, code: str, **detail):
        super().__init__(code)
        self.code, self.detail = code, detail


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def safe_path(root: Path, name: str, verified_parents: set | None = None) -> Path:
    parts = PurePosixPath(name).parts
    if not parts or "\\" in name or PurePosixPath(name).is_absolute() or any(
        p in {".", ".."} or re.search(r'[<>:"|?*\x00-\x1f]', p) or p.rstrip(" .") != p
        or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p)
        for p in name.split("/")
    ):
        raise TestAgentError("path_invalid")
    root = root.resolve()
    target = root.joinpath(*parts)
    for parent in [target, *target.parents]:
        if parent == root:
            break
        if verified_parents is not None and parent in verified_parents:
            break
        try:
            information = parent.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(information.st_mode) or getattr(information, "st_file_attributes", 0) & 0x400:
            raise TestAgentError("path_link_rejected")
        if stat.S_ISDIR(information.st_mode) and verified_parents is not None:
            verified_parents.add(parent)
    if not target.resolve().is_relative_to(root):
        raise TestAgentError("path_outside_root")
    if target.is_file() and target.stat().st_nlink > 1:
        raise TestAgentError("path_link_rejected")
    return target


def atomic_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".test-agent-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    atomic_bytes(Path(path), (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Store:
    """SQLite transactions serialize state changes across CLI and MCP processes."""

    def __init__(self, repository: Path, data_root: Path | None = None):
        self.repository = Path(repository).resolve()
        self.root = (data_root or self.repository / ".local-data" / "auto-test").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "state.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                    at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
            """)

    def connect(self):
        connection = sqlite3.connect(self.database, timeout=30, factory=ClosingConnection)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, kind, key, db=None):
        if db is None:
            with self.connect() as connection:
                return self.get(kind, key, connection)
        row = db.execute("SELECT payload FROM documents WHERE kind=? AND id=?", (kind, key)).fetchone()
        if row is None:
            raise TestAgentError("record_not_found", kind=kind, id=key)
        return json.loads(row[0])

    def put(self, kind, key, value, db):
        db.execute("INSERT INTO documents VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload",
                   (kind, key, json.dumps(value, ensure_ascii=False)))

    def event(self, task, kind, payload, db):
        db.execute("INSERT INTO events(task_id,at,kind,payload) VALUES (?,?,?,?)",
                   (task, now(), kind, json.dumps(payload, ensure_ascii=False)))

    def events(self, task, after=0):
        with self.connect() as db:
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in db.execute(
                "SELECT * FROM events WHERE task_id=? AND sequence>? ORDER BY sequence", (task, after))]

    def task_dir(self, task):
        return safe_path(self.root, "runs/" + task)
