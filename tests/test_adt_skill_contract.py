from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from sap_business_agents_platform.skills import (
    SkillError,
    SkillRegistry,
    _load_trusted_schema,
    _skill_subprocess_environment,
    _validate_adt_input,
    _validate_adt_connection_contract,
    _validate_adt_manifest,
    _validate_adt_output,
)


def _input() -> dict[str, object]:
    return {
        "schema_version": 1,
        "source_type": "table",
        "object": "EKET",
        "fields": ["EBELN", "EBELP", "ETENR", "EINDT"],
        "filters": [
            {"field": "EBELN", "operator": "eq", "value": "4500000001"}
        ],
        "order_by": ["EBELN", "EBELP", "ETENR"],
        "max_rows": 2,
    }


def test_skill_registry_preserves_supply_chain_error_for_configured_skill(
    tmp_path: Path,
) -> None:
    skillhub = tmp_path / "skillhub"
    package = skillhub / "skills" / "FI" / "test-skill"
    references = package / "references"
    references.mkdir(parents=True)
    (package / "run.py").write_text("print('unused')\n", encoding="utf-8")
    (package / "manifest.json").write_text(
        json.dumps(
            {
                "skill_id": "test-skill",
                "read_only": True,
                "validated": True,
                "allowed_http_methods": [],
                "allowed_endpoints": [],
            }
        ),
        encoding="utf-8",
    )
    schema = {"type": "object", "additionalProperties": False, "properties": {}}
    for name in ("input.schema.json", "output.schema.json"):
        (references / name).write_text(json.dumps(schema), encoding="utf-8")
    allowlist = tmp_path / "skills.json"
    allowlist.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "skill_id": "test-skill",
                        "entrypoint": "skills/FI/test-skill/run.py",
                        "manifest_path": "skills/FI/test-skill/manifest.json",
                        "input_schema_path": "skills/FI/test-skill/references/input.schema.json",
                        "output_schema_path": "skills/FI/test-skill/references/output.schema.json",
                        "read_only": True,
                        "validated": True,
                        "output_policy": "public",
                        "allowed_http_methods": [],
                        "allowed_endpoints": [],
                        "expected_package_sha256": "0" * 64,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    registry = SkillRegistry(skillhub, allowlist)

    assert registry.list_all_approved_skills() == []
    with pytest.raises(SkillError) as error:
        registry.get("test-skill")
    assert error.value.code == "skill_package_digest_mismatch"
    assert error.value.detail == {
        "skill_id": "test-skill",
        "validation_issues": [
            {
                "code": "skill_package_digest_mismatch",
                "message": "Skill package digest does not match the approved package.",
            }
        ],
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


def test_adt_input_accepts_only_bounded_read_only_task() -> None:
    _validate_adt_input(_input())
    with pytest.raises(SkillError) as forbidden:
        _validate_adt_input({**_input(), "connection_profile": "caller-selected"})
    assert forbidden.value.code == "skill_input_connection_forbidden"
    assert forbidden.value.detail == {
        "field": "connection_profile",
        "connection_owner": "SAPSkillhub",
    }
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

    _validate_adt_input({**_input(), "max_rows": 30_000})
    _validate_adt_input({**_input(), "object": "/ACME/EKET"})
    with pytest.raises(SkillError, match="30000"):
        _validate_adt_input({**_input(), "max_rows": 30_001})


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
    required = set(skill["input_schema"]["required"])
    properties = set(skill["input_schema"]["properties"])
    assert required >= {"source_type", "object", "fields", "filters", "max_rows"}
    assert "connection_profile" not in required
    assert "connection_profile" not in properties
    assert set(skill["output_schema"]["required"]) >= {"skill_id", "completeness", "artifacts"}
    _validate_adt_connection_contract(skill["input_schema"])

    outside = tmp_path / "outside.json"
    outside.write_text('{"type":"object"}', encoding="utf-8")
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    with pytest.raises(SkillError, match="escapes SAPSkillhub root"):
        _load_trusted_schema(trusted, "../outside.json", "input_schema")


def _install_new_contract_fixture(skillhub_root: Path) -> None:
    skill_root = skillhub_root / "skills" / "Common" / "sap-adt-table-export"
    references = skill_root / "references"
    references.mkdir(parents=True)
    input_schema = {
        "type": "object",
        "properties": {
            "schema_version": {"type": "integer", "const": 1},
            "source_type": {"type": "string", "enum": ["table", "cds"]},
            "object": {"type": "string"},
            "fields": {"type": "array", "items": {"type": "string"}},
            "filters": {"type": "array", "items": {"type": "object"}},
            "order_by": {"type": "array"},
            "max_rows": {"type": "integer", "minimum": 1, "maximum": 30000},
        },
        "required": [
            "schema_version",
            "source_type",
            "object",
            "fields",
            "filters",
            "max_rows",
        ],
        "additionalProperties": False,
    }
    output_schema = {"type": "object", "additionalProperties": True}
    (references / "input.schema.json").write_text(
        json.dumps(input_schema), encoding="utf-8"
    )
    (references / "output.schema.json").write_text(
        json.dumps(output_schema), encoding="utf-8"
    )
    (skill_root / "run.py").write_text(
        """from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

input_path = Path(sys.argv[sys.argv.index(\"--input\") + 1])
output_path = Path(sys.argv[sys.argv.index(\"--output\") + 1])
request = json.loads(input_path.read_text(encoding=\"utf-8\"))
connection_keys = {
    \"SAP_ADT_CA_PATH\",
    \"SAP_ADT_PROFILES_FILE\",
    \"SAP_AUTH_TYPE\",
    \"SAP_BASE_URL\",
    \"SAP_CLIENT\",
    \"SAP_ODATA_BASE_URL\",
    \"SAP_ODATA_TIMEOUT_MS\",
    \"SAP_PASSWORD\",
    \"SAP_SYSTEM\",
    \"SAP_USERNAME\",
    \"SAP_VERIFY_SSL\",
}
result = {
    \"schema_version\": 1,
    \"skill_id\": \"sap-adt-table-export\",
    \"run_id\": \"fixture-run\",
    \"status\": \"complete\",
    \"read_only\": True,
    \"validated\": True,
    \"source\": {
        \"input_has_connection_profile\": \"connection_profile\" in request,
        \"inherited_connection_keys\": sorted(connection_keys.intersection(os.environ)),
    },
    \"scope\": {},
    \"rows\": [],
    \"row_count\": 0,
    \"completeness\": {\"source_complete\": True, \"paging_complete\": True},
    \"validation_issues\": [],
    \"started_at\": \"2026-08-18T00:00:00Z\",
    \"completed_at\": \"2026-08-18T00:00:01Z\",
    \"artifacts\": [],
}
payload = (json.dumps(result, sort_keys=True) + \"\\n\").encode(\"utf-8\")
output_path.write_bytes(payload)
manifest = {
    \"skill_id\": result[\"skill_id\"],
    \"run_id\": result[\"run_id\"],
    \"read_only\": result[\"read_only\"],
    \"status\": result[\"status\"],
    \"output_sha256\": hashlib.sha256(payload).hexdigest(),
}
output_path.with_name(output_path.name + \".manifest.json\").write_text(
    json.dumps(manifest), encoding=\"utf-8\"
)
""",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("base_url", "username"),
    [
        ("https://first.invalid", "first-user"),
        ("https://second.invalid", "second-user"),
    ],
)
def test_new_skill_contract_runs_without_profile_and_isolates_caller_sap_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
    username: str,
) -> None:
    skillhub_root = tmp_path / "skillhub"
    _install_new_contract_fixture(skillhub_root)
    repository = Path(__file__).resolve().parents[1]
    registry = SkillRegistry(skillhub_root, repository / "config" / "skills.json")
    monkeypatch.setenv("SAP_BASE_URL", base_url)
    monkeypatch.setenv("SAP_USERNAME", username)
    monkeypatch.setenv("SAP_PASSWORD", "not-a-real-secret")
    monkeypatch.setenv("SAP_CLIENT", "001")
    monkeypatch.setenv("SAP_VERIFY_SSL", "false")
    monkeypatch.setenv("SAP_AUTH_TYPE", "basic")
    monkeypatch.setenv("SAP_ODATA_BASE_URL", base_url + "/odata")
    monkeypatch.setenv("SAP_ODATA_TIMEOUT_MS", "1000")
    monkeypatch.setenv("SAP_ADT_PROFILES_FILE", "caller-owned-file")
    monkeypatch.setenv("SAP_ADT_CA_PATH", "caller-owned-ca")

    result = asyncio.run(registry.execute("sap-adt-table-export", _input()))

    assert result["status"] == "complete"
    assert result["source"] == {
        "input_has_connection_profile": False,
        "inherited_connection_keys": [],
    }
    assert any(item.get("verified") is True for item in result["artifacts"])


def test_registry_preserves_hash_verified_failed_adt_result(
    tmp_path: Path,
) -> None:
    skillhub_root = tmp_path / "skillhub"
    _install_new_contract_fixture(skillhub_root)
    run_path = skillhub_root / "skills" / "Common" / "sap-adt-table-export" / "run.py"
    script = run_path.read_text(encoding="utf-8")
    script = script.replace('"status": "complete"', '"status": "failed"')
    script = script.replace('"validated": True', '"validated": False')
    script = script.replace(
        '"completeness": {"source_complete": True, "paging_complete": True}',
        '"completeness": {"source_complete": False, "paging_complete": False}',
    )
    script = script.replace(
        '"validation_issues": []',
        '"validation_issues": [{"code": "field_not_found", "message": "Field unavailable."}]',
    )
    run_path.write_text(script + "\nsys.exit(1)\n", encoding="utf-8")
    repository = Path(__file__).resolve().parents[1]
    registry = SkillRegistry(skillhub_root, repository / "config" / "skills.json")

    result = asyncio.run(registry.execute("sap-adt-table-export", _input()))

    assert result["status"] == "failed"
    assert result["validation_issues"] == [
        {"code": "field_not_found", "message": "Field unavailable."}
    ]
    assert any(item.get("verified") is True for item in result["artifacts"])


def test_new_adt_schema_is_accepted_and_non_adt_environment_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    new_schema = {
        "type": "object",
        "properties": {"schema_version": {"type": "integer"}},
        "additionalProperties": False,
    }
    _validate_adt_connection_contract(new_schema)
    monkeypatch.setenv("SAP_BASE_URL", "https://caller.invalid")
    assert "SAP_BASE_URL" not in _skill_subprocess_environment("sap-adt-table-export")
    assert _skill_subprocess_environment("another-skill")["SAP_BASE_URL"] == "https://caller.invalid"


def test_all_agent_adt_inputs_are_connection_agnostic() -> None:
    repository = Path(__file__).resolve().parents[1]
    found = 0
    for path in repository.glob("agents/*/*/agent.json"):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for step in manifest.get("execution", {}).get("steps", []):
            if step.get("skillId") != "sap-adt-table-export":
                continue
            found += 1
            assert "connection_profile" not in step.get("inputMapping", {}), path
    assert found > 0
    for path in repository.glob("agents/MM/*/config/policy.toml"):
        assert "adt_connection_profile" not in path.read_text(encoding="utf-8"), path
