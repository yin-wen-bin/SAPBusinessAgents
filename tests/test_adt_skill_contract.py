from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from sap_business_agents_platform.skills import (
    SkillError,
    SkillRegistry,
    _load_trusted_schema,
    _validate_adt_input,
    _validate_adt_manifest,
    _validate_adt_output,
)


def _input() -> dict[str, object]:
    return {
        "schema_version": 1,
        "connection_profile": "mm-read-only",
        "source_type": "table",
        "object": "EKET",
        "fields": ["EBELN", "EBELP", "ETENR", "EINDT"],
        "filters": [
            {"field": "EBELN", "operator": "eq", "value": "4500000001"}
        ],
        "order_by": ["EBELN", "EBELP", "ETENR"],
        "max_rows": 2,
    }


def _output() -> dict[str, object]:
    return {
        "schema_version": 1,
        "skill_id": "sap-adt-table-export",
        "run_id": "run-1",
        "status": "complete",
        "read_only": True,
        "validated": True,
        "source": {},
        "scope": {},
        "rows": [{"EBELN": "4500000001"}],
        "row_count": 1,
        "completeness": {"source_complete": True, "paging_complete": True},
        "validation_issues": [],
        "started_at": "2026-08-17T00:00:00Z",
        "completed_at": "2026-08-17T00:00:01Z",
        "artifacts": [],
    }


def test_adt_input_accepts_only_static_bounded_read_only_task() -> None:
    _validate_adt_input(_input())
    for key, value in (
        ("url", "https://sap.example.invalid"),
        ("password", "secret"),
        ("sql", "select * from eket"),
    ):
        invalid = {**_input(), key: value}
        with pytest.raises(SkillError, match="prohibited key"):
            _validate_adt_input(invalid)

    unbounded = {**_input(), "filters": []}
    with pytest.raises(SkillError, match="non-empty"):
        _validate_adt_input(unbounded)

    descending = {**_input(), "order_by": [{"field": "EBELN", "direction": "desc"}]}
    with pytest.raises(SkillError, match="ascending"):
        _validate_adt_input(descending)


def test_adt_complete_requires_coherent_validation_and_completeness() -> None:
    _validate_adt_output(_output())
    for mutation in (
        {"validated": False},
        {"completeness": {"source_complete": False, "paging_complete": True}},
        {"validation_issues": [{"code": "paging_incomplete"}]},
    ):
        with pytest.raises(SkillError, match="complete result"):
            _validate_adt_output({**_output(), **mutation})


def test_adt_adjacent_manifest_must_match_exact_output_bytes(tmp_path: Path) -> None:
    output = _output()
    output_path = tmp_path / "output.json"
    payload = (json.dumps(output, indent=2) + "\n").encode()
    output_path.write_bytes(payload)
    manifest = {
        "skill_id": output["skill_id"],
        "run_id": output["run_id"],
        "read_only": output["read_only"],
        "status": output["status"],
        "output_sha256": hashlib.sha256(payload).hexdigest(),
    }
    output_path.with_name("output.json.manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    _validate_adt_manifest(output_path, payload, output)

    manifest["output_sha256"] = "0" * 64
    output_path.with_name("output.json.manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    with pytest.raises(SkillError, match="SHA-256"):
        _validate_adt_manifest(output_path, payload, output)


def test_skill_registry_loads_full_published_schemas_and_blocks_path_escape(tmp_path: Path) -> None:
    source_root = Path(r"C:\Users\wenbi\Documents\SAPSkillhub")
    if not source_root.is_dir():
        pytest.skip("Local SAPSkillhub checkout is unavailable for schema-drift verification.")
    registry = SkillRegistry(source_root, Path(__file__).resolve().parents[1] / "config" / "skills.json")
    skill = registry.get("sap-adt-table-export")
    assert skill["input_schema"]["additionalProperties"] is False
    assert set(skill["input_schema"]["required"]) >= {"connection_profile", "filters", "max_rows"}
    assert set(skill["output_schema"]["required"]) >= {"skill_id", "completeness", "artifacts"}

    outside = tmp_path / "outside.json"
    outside.write_text('{"type":"object"}', encoding="utf-8")
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    with pytest.raises(SkillError, match="escapes SAPSkillhub root"):
        _load_trusted_schema(trusted, "../outside.json", "input_schema")
