"""Provider-neutral, safe projections of draft assistant conversations.

Original conversation rows remain untouched. The projection is not SAP evidence
and a clarification answer never grants additional tool permissions.
"""
from __future__ import annotations

import re
from typing import Any


def safe_context(value: Any, *, limit: int = 12_000, restricted: bool = False, depth: int = 0) -> Any:
    if depth > 12:
        return None
    if isinstance(value, str):
        text = value[:limit]
        text = re.sub(
            r"(?i)\b(password|token|secret|api[_-]?key|authorization)\s*[:=]\s*(?:(?:bearer|basic)\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
            r"\1=[redacted]", text,
        )
        text = re.sub(r"(密码|口令|密钥)\s*[:：=]\s*[^\s,，;；]+", r"\1=[redacted]", text)
        return re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[redacted]@", text)
    if isinstance(value, dict):
        restricted = restricted or value.get("restricted") is True or value.get("raw_rows_restricted") is True
        blocked = ("password", "secret", "credential", "authorization", "token", "api_key", "apikey",
                   "raw_rows", "source_rows", "encrypted", "sealed", "protected", "input")
        return {
            str(key): safe_context(item, limit=limit, restricted=restricted, depth=depth + 1)
            for key, item in list(value.items())[:200]
            if not any(token in str(key).lower() for token in blocked)
            and not (restricted and str(key).lower() in {"rows", "records", "values", "items", "data", "dataset"})
        }
    if isinstance(value, list):
        # Inherited restrictions apply to nested row arrays, not just their parent.
        return [] if restricted else [safe_context(item, limit=limit, depth=depth + 1) for item in value[:200]]
    return value if isinstance(value, (int, float, bool)) or value is None else None


def pending_questions(turns: list[dict[str, Any]], revision: int) -> list[dict[str, Any]]:
    """Derive explicit state without rewriting legacy history or losing old questions."""
    answered = {
        item.get("decision", {}).get("reply_to_clarification_id")
        for item in turns if item.get("status") in {"completed", "queued", "running", "waiting"}
    }
    result = []
    for item in turns:
        pending = item.get("decision", {}).get("pending_clarification")
        if item.get("status") != "completed" or not isinstance(pending, dict) or pending.get("id") in answered:
            continue
        state = "pending" if int(pending.get("revision") or 0) == revision else "expired"
        result.append({**pending, "state": state})
    return result


def clarification_state(raw: Any, *, question: dict[str, str], revision: int,
                        turn: int, identifier: str, target: dict[str, Any]) -> dict[str, Any]:
    """Validate new provider output; read old summary-only replies compatibly."""
    if raw is None:
        # Legacy replies have no choice schema. Multiple questions cannot be
        # confirmed with a bare yes; the user must provide a substantive answer.
        multiple = any(str(text).count("?") + str(text).count("？") > 1 for text in question.values())
        confirmable = not multiple and all(re.search(
            r"(?i)是否|要不要|可否|可以吗|请确认|请批准|\b(confirm|do you|would you|can you|should we)\b", str(text))
            for text in question.values())
        raw = {"question": question, "options": [], "answer_mode": "confirm" if confirmable else "text", "target": target}
    if not isinstance(raw, dict) or raw.get("answer_mode") not in {"confirm", "choice", "text"}:
        raise ValueError("runtime_agent_feedback_invalid")
    supplied = raw.get("question")
    if not isinstance(supplied, dict) or not all(isinstance(supplied.get(lang), str) and 0 < len(supplied[lang]) <= 4000 for lang in ("zh", "en")):
        raise ValueError("runtime_agent_feedback_invalid")
    options = raw.get("options", [])
    if not isinstance(options, list) or len(options) > 10:
        raise ValueError("runtime_agent_feedback_invalid")
    ids = set()
    for option in options:
        if (not isinstance(option, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(option.get("id") or ""))
                or option["id"] in ids or not isinstance(option.get("label"), dict)
                or not all(isinstance(option["label"].get(lang), str) and 0 < len(option["label"][lang]) <= 500 for lang in ("zh", "en"))):
            raise ValueError("runtime_agent_feedback_invalid")
        ids.add(option["id"])
    if (raw["answer_mode"] == "choice" and len(options) < 2) or (raw["answer_mode"] != "choice" and options):
        raise ValueError("runtime_agent_feedback_invalid")
    # Providers may not invent target references. The server uses the verified
    # request target, never a CSS selector/coordinate/model-supplied reference.
    return {"id": identifier, "question": supplied, "options": options, "answer_mode": raw["answer_mode"],
            "target": target, "revision": revision, "turn": turn, "state": "pending"}


def conversation_context(turns: list[dict[str, Any]], revision: int, before_turn: int) -> list[dict[str, Any]]:
    previous = [item for item in turns if int(item["turn"]) < before_turn]
    decisions = []
    recent = []
    for item in previous:
        if item.get("kind") != "feedback" or item.get("status") != "completed":
            continue
        decision = item.get("decision") or {}
        answer = (decision.get("context") or {}).get("clarification")
        if isinstance(answer, dict):
            decisions.append({"turn": item["turn"], "clarification": answer,
                              "user_answer": item.get("user_message"), "base_revision": item.get("base_revision")})
        recent.append({"turn": item["turn"], "user_message": item.get("user_message"),
                       "assistant_message": decision.get("assistant_message") or decision.get("summary"),
                       "action": decision.get("action"), "base_revision": item.get("base_revision"),
                       "result_revision": item.get("result_revision")})
    # Pending questions and confirmed decisions are outside the recent-turn
    # window. Exceeding a provider context budget fails explicitly; never drop
    # an unresolved question or option to make the request fit.
    projection = {"kind": "platform_context", "revision": revision,
                  "pending_clarifications": pending_questions(previous, revision), "decisions": decisions}
    projection["pending_clarifications"] = [safe_context(item) for item in projection["pending_clarifications"]]
    projection["decisions"] = [safe_context(item) for item in decisions]
    return [projection, *[safe_context(item) for item in recent[-20:]]]
