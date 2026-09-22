"""Equivalent input spelling for model tools only; fixed execution is unchanged."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .sap_read.base import SapReadError


def normalize_ascending_order(plan: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    result = deepcopy(plan)
    diagnostics: list[dict[str, str]] = []

    def visit(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        if "order_by" in node:
            values = node["order_by"]
            if not isinstance(values, list):
                raise SapReadError("order_by must be an array of ascending fields.", code="invalid_order_by_expression")
            normalized = []
            for index, value in enumerate(values):
                field = None
                if isinstance(value, str):
                    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\s+asc)?", value.strip(), re.IGNORECASE)
                    field = match.group(1) if match else None
                elif isinstance(value, dict) and set(value) == {"field", "direction"}:
                    if isinstance(value["field"], str) and str(value["direction"]).lower() == "asc":
                        field = value["field"].strip()
                if not field or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field):
                    raise SapReadError(
                        "Only bare fields or equivalent explicit ascending order are supported; descending and expressions are not supported.",
                        code="invalid_order_by_expression",
                        detail={"path": f"{path}/order_by/{index}"},
                    )
                normalized.append(field)
                if value != field:
                    diagnostics.append({"code": "ascending_order_normalized", "path": f"{path}/order_by/{index}"})
            node["order_by"] = normalized
        for key in ("steps",):
            for index, child in enumerate(node.get(key) or []):
                visit(child, f"{path}/{key}/{index}")
        if isinstance(node.get("plan"), dict):
            visit(node["plan"], f"{path}/plan")

    visit(result, "/plan")
    return result, diagnostics
