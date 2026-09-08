import importlib.util
from pathlib import Path

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
