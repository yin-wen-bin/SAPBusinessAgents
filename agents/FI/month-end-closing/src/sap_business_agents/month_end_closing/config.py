"""TOML checklist loading and validation."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import tomllib

from .models import CheckDefinition, Checklist, Severity


VALID_OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte"}


def load_checklist(path: str | Path) -> Checklist:
    config_path = Path(path)
    with config_path.open("rb") as stream:
        raw = tomllib.load(stream)

    header = raw.get("checklist")
    raw_checks = raw.get("checks")
    if not isinstance(header, dict) or not isinstance(raw_checks, list) or not raw_checks:
        raise ValueError("checklist config requires [checklist] and at least one [[checks]] entry")

    checks = tuple(_parse_check(item) for item in raw_checks)
    ids = [check.check_id for check in checks]
    if len(ids) != len(set(ids)):
        raise ValueError("checklist contains duplicate check ids")

    return Checklist(
        checklist_id=_required_text(header, "id"),
        version=_required_text(header, "version"),
        description=_required_text(header, "description"),
        currency=_required_text(header, "currency"),
        not_recommended_at=Severity(_required_text(header, "not_recommended_at")),
        checks=checks,
    )


def _parse_check(item: object) -> CheckDefinition:
    if not isinstance(item, dict):
        raise ValueError("each check must be a TOML table")
    operator = _required_text(item, "operator")
    if operator not in VALID_OPERATORS:
        raise ValueError(f"unsupported operator: {operator}")
    tables = item.get("tables")
    if not isinstance(tables, list) or not tables or not all(isinstance(x, str) and x for x in tables):
        raise ValueError(f"check {_required_text(item, 'id')} requires a non-empty tables list")
    try:
        threshold = Decimal(str(item["threshold"]))
    except (KeyError, InvalidOperation) as exc:
        raise ValueError(f"check {_required_text(item, 'id')} has an invalid threshold") from exc

    return CheckDefinition(
        check_id=_required_text(item, "id"),
        name=_required_text(item, "name"),
        module=_required_text(item, "module"),
        handler=_required_text(item, "handler"),
        metric_name=_required_text(item, "metric_name"),
        operator=operator,
        threshold=threshold,
        severity=Severity(_required_text(item, "severity")),
        blocking=_required_bool(item, "blocking"),
        owner_department=_required_text(item, "owner_department"),
        owner=_required_text(item, "owner"),
        remediation=_required_text(item, "remediation"),
        requires_human_confirmation=_required_bool(item, "requires_human_confirmation"),
        tcode=_required_text(item, "tcode"),
        tables=tuple(tables),
    )


def _required_text(item: dict[str, object], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing or invalid text field: {key}")
    return value.strip()


def _required_bool(item: dict[str, object], key: str) -> bool:
    value = item.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"missing or invalid boolean field: {key}")
    return value

