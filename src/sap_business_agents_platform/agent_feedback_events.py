"""Resumable event projection for draft conversations, not a general chat service."""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator


async def stream_feedback_events(request: Any, store: Any, draft_id: str, *, after: int = 0,
                                 poll_interval: float = 1) -> AsyncIterator[str]:
    try:
        resumed = max(0, int(request.headers.get("last-event-id") or "0"))
    except ValueError:
        resumed = 0
    sequence = max(0, after, resumed)
    while not await request.is_disconnected():
        events = store.list_agent_conversation_events(draft_id, sequence)
        for event in events:
            sequence = event["sequence"]
            yield f"id: {sequence}\nevent: {event['kind']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        if not events:
            yield ": heartbeat\n\n"
        await asyncio.sleep(poll_interval)
