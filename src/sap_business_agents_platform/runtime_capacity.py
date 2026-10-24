"""Owned query capacity with writer-preferring exclusive business access."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager


class RuntimeCapacity:
    def __init__(self, limit: int = 10):
        self.limit = max(1, int(limit))
        self.condition = asyncio.Condition()
        self.readers = 0
        self.writer = False
        self.waiting_writers = 0

    @asynccontextmanager
    async def acquire(self, *, shared: bool):
        async with self.condition:
            if shared:
                await self.condition.wait_for(lambda: not self.writer and not self.waiting_writers and self.readers < self.limit)
                self.readers += 1
            else:
                self.waiting_writers += 1
                try:
                    await self.condition.wait_for(lambda: not self.writer and not self.readers)
                    self.writer = True
                finally:
                    self.waiting_writers -= 1
                    self.condition.notify_all()
        try:
            yield
        finally:
            async with self.condition:
                if shared:
                    self.readers -= 1
                else:
                    self.writer = False
                self.condition.notify_all()
