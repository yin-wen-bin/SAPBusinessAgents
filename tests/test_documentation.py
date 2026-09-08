import importlib.util
import hashlib
import json
from pathlib import Path

from sap_business_agents_platform.acceptance import agent_execution_digest
from sap_business_agents_platform.manifests import AgentRepository

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("documentation", ROOT / "scripts/documentation.py")
docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(docs)


def test_generated_documentation_is_current_and_linked():
    paths = []
    for path, expected in docs.document_updates():
        assert path.read_text(encoding="utf-8") == expected, path
        paths.append(path)
    assert not docs.local_link_errors(paths)


def test_hidden_subtrees_and_conditional_row_inputs():
    schema = {"properties": {
        "internal": {"x-sapba-internal": True, "properties": {"secret": {"type": "string"}}},
        "rows": {"type": "array", "items": {"properties": {"material": {"type": "string", "title": {"zh": "物料", "en": "Material"}}}, "required": ["material"]}},
    }}
    rows = "\n".join(docs.input_rows(schema))
    assert "secret" not in rows and "internal" not in rows
    assert "rows[].material" in rows and "Required when supplied" in rows


def test_narrative_is_not_replaced():
    text = "Human intro\n<!-- generated:facts:start -->\nold\n<!-- generated:facts:end -->\nHuman conclusion"
    result = docs.replace_block(text, "facts", "new")
    assert result.startswith("Human intro") and result.endswith("Human conclusion")
    assert "old" not in result


def test_documentation_releases_retain_original_acceptance_and_execution():
    repo = AgentRepository(ROOT / "agents")
    for current in repo.list_all():
        validation = current.get("validation") or {}
        reuse = validation.get("documentationReuse")
        if not reuse:
            continue
        source = repo.get_version(current["slug"], reuse["sourceVersion"])
        source_validation = source["validation"]
        expected_hash = "sha256:" + hashlib.sha256(json.dumps(source_validation, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        assert reuse["sourceValidationDigest"] == expected_hash
        assert {k: v for k, v in validation.items() if k != "documentationReuse"} == {k: v for k, v in source_validation.items() if k != "documentationReuse"}
        old = repo.package(source["slug"], source["version"])
        new = repo.package(current["slug"])
        assert agent_execution_digest(source, old.get("rules_source")) == reuse["executionDigest"]
        assert agent_execution_digest(current, new.get("rules_source")) == reuse["executionDigest"]
        assert len(source["workflow"]) == len(current["workflow"])
        for before, after in zip(source["workflow"], current["workflow"], strict=True):
            assert {k: v for k, v in before.items() if k not in {"title", "description"}} == {k: v for k, v in after.items() if k not in {"title", "description"}}
