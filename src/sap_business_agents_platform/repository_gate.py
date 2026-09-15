from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import Condition, RLock, get_ident
from typing import Iterator


class RepositoryReadWriteGate:
    """Small re-entrant in-process gate for immutable package snapshots.

    Publication only needs an exclusive window while the prepared commit is
    fast-forwarded into ``main``.  Readers may otherwise continue normally and
    existing runs keep using the package snapshot already stored with the run.
    """

    def __init__(self) -> None:
        self._condition = Condition(RLock())
        self._readers: dict[int, int] = {}
        self._writer: int | None = None
        self._writer_depth = 0

    @contextmanager
    def read(self) -> Iterator[None]:
        owner = get_ident()
        with self._condition:
            while self._writer is not None and self._writer != owner:
                self._condition.wait()
            self._readers[owner] = self._readers.get(owner, 0) + 1
        try:
            yield
        finally:
            with self._condition:
                depth = self._readers.get(owner, 0) - 1
                if depth > 0:
                    self._readers[owner] = depth
                else:
                    self._readers.pop(owner, None)
                self._condition.notify_all()

    @contextmanager
    def write(self) -> Iterator[None]:
        owner = get_ident()
        with self._condition:
            if self._writer == owner:
                self._writer_depth += 1
            else:
                while self._writer is not None or any(
                    reader != owner and depth > 0
                    for reader, depth in self._readers.items()
                ):
                    self._condition.wait()
                self._writer = owner
                self._writer_depth = 1
        try:
            yield
        finally:
            with self._condition:
                self._writer_depth -= 1
                if self._writer_depth == 0:
                    self._writer = None
                    self._condition.notify_all()


_REGISTRY_LOCK = RLock()
_REGISTRY: dict[str, RepositoryReadWriteGate] = {}


def repository_gate(root: Path) -> RepositoryReadWriteGate:
    key = str(root.resolve()).casefold()
    with _REGISTRY_LOCK:
        return _REGISTRY.setdefault(key, RepositoryReadWriteGate())
