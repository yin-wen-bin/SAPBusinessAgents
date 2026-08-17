from __future__ import annotations

from typing import Any

import httpx


class SapClawError(RuntimeError):
    def __init__(self, message: str, *, code: str = "sapclaw_error", detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class SapClawClient:
    """Async client for the guarded SAPClaw Thin Runtime."""

    def __init__(self, base_url: str, api_key: str = "", timeout_seconds: float = 300) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/runtime/health")

    async def catalog(self, query: str = "", skip: int = 0, limit: int = 100) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/v1/runtime/catalog", {"query": query, "skip": skip, "limit": limit}
        )

    async def guidance(self, query: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/runtime/guidance",
            {"user_input": query, "service_names": [], "max_feedback_memories": 5},
        )

    async def schema(
        self,
        service_name: str,
        entity_sets: list[str] | str,
        query: str = "",
        *,
        include_fields: bool = True,
        max_fields: int = 5000,
    ) -> dict[str, Any]:
        requested_entities = (
            [entity_sets] if isinstance(entity_sets, str) else list(dict.fromkeys(entity_sets))
        )
        return await self._request(
            "POST",
            "/api/v1/runtime/schema",
            {
                "service_name": service_name,
                "entity_sets": requested_entities,
                "query": query,
                "include_fields": include_fields,
                "max_fields": max_fields,
            },
        )

    async def validate_plan(self, plan: dict[str, Any], query: str = "") -> dict[str, Any]:
        return await self._request(
            "POST", "/api/v1/runtime/validate-plan", {"plan": plan, "user_input": query}
        )

    async def execute_plan(
        self,
        plan: dict[str, Any],
        query: str = "",
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"plan": plan, "user_input": query}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        return await self._request("POST", "/api/v1/runtime/execute-plan", payload)

    async def execute_get(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/v1/runtime/execute-get", request)

    async def page(self, case_id: str, skip: int = 0) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/v1/runtime/page", {"case_id": case_id, "skip": skip}
        )

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                trust_env=False,
                headers=headers,
            ) as client:
                response = await client.request(method, path, json=payload)
        except httpx.TimeoutException as exc:
            raise SapClawError("SAPClaw request timed out.", code="sapclaw_timeout") from exc
        except httpx.HTTPError as exc:
            raise SapClawError(
                "SAPClaw is not reachable. Start its local runtime on 127.0.0.1 first.",
                code="sapclaw_unavailable",
                detail=str(exc),
            ) from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise SapClawError(
                "SAPClaw returned non-JSON content.", code="sapclaw_invalid_response"
            ) from exc
        if response.status_code >= 400:
            raise SapClawError(
                f"SAPClaw returned HTTP {response.status_code}.",
                code="sapclaw_http_error",
                detail=data,
            )
        if not isinstance(data, dict):
            raise SapClawError("SAPClaw returned an invalid response object.", code="sapclaw_invalid_response")
        return data
