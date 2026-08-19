from __future__ import annotations

from typing import Any, Protocol


class SapReadError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "sap_read_error",
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class SapReadProvider(Protocol):
    async def health(self) -> dict[str, Any]: ...

    async def catalog(
        self, query: str = "", skip: int = 0, limit: int = 100
    ) -> dict[str, Any]: ...

    async def guidance(self, query: str) -> dict[str, Any]: ...

    async def schema(
        self,
        service_name: str,
        entity_sets: list[str] | str,
        query: str = "",
        *,
        odata_version: str,
        include_fields: bool = True,
        max_fields: int = 5000,
    ) -> dict[str, Any]: ...

    async def validate_plan(
        self, plan: dict[str, Any], query: str = ""
    ) -> dict[str, Any]: ...

    async def execute_plan(
        self,
        plan: dict[str, Any],
        query: str = "",
        conversation_id: str | None = None,
    ) -> dict[str, Any]: ...

    async def execute_get(self, request: dict[str, Any]) -> dict[str, Any]: ...

    async def page(self, case_id: str, skip: int = 0) -> dict[str, Any]: ...
