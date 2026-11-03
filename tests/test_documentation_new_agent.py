"""Newly authored Agents may have narrative README files before publication."""
import copy
import json

import pytest

from tests.test_documentation import docs
from sap_business_agents_platform.agent_lifecycle import AgentLifecycleService


def test_new_agent_readme_can_be_documented_without_touching_business_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(docs, 'ROOT', tmp_path)
    (tmp_path/'README.md').write_text('Project\n<!-- generated:agents-zh:start -->\n<!-- generated:agents-zh:end -->\n'
        '<!-- generated:agents-en:start -->\n<!-- generated:agents-en:end -->\n', encoding='utf-8')
    for module in docs.MODULE_NAMES:
        directory=tmp_path/'agents'/module;directory.mkdir(parents=True)
        (directory/'README.md').write_text('Module\n<!-- generated:agents:start -->\n<!-- generated:agents:end -->\n',encoding='utf-8')
    package=AgentLifecycleService._blank_package('documentation-new-agent','MM',{'zh':'新 Agent','en':'New Agent'})
    manifest=package['manifest'];original=copy.deepcopy(manifest)
    directory=tmp_path/'agents/MM/documentation-new-agent';directory.mkdir()
    (directory/'agent.json').write_text(json.dumps(manifest),encoding='utf-8')
    narrative='# Author-owned narrative\n\nKeep every word and whitespace.  \n'
    readme=directory/'README.md';readme.write_text(narrative,encoding='utf-8')
    (tmp_path/'workflows').mkdir()
    first=dict(docs.document_updates(tmp_path))
    generated=first[readme]
    assert generated.startswith(narrative)
    assert generated.count('<!-- generated:facts:start -->')==generated.count('<!-- generated:facts:end -->')==1
    assert 'Manifest and complete I/O contract' in generated and '尚未验收' in generated
    for path, text in first.items():path.write_text(text,encoding='utf-8')
    assert dict(docs.document_updates(tmp_path))==first
    assert json.loads((directory/'agent.json').read_text())==original


@pytest.mark.parametrize('broken',[
    '<!-- generated:facts:start -->\nmissing end',
    'missing start\n<!-- generated:facts:end -->',
    '<!-- generated:facts:start --><!-- generated:facts:start --><!-- generated:facts:end -->',
])
def test_malformed_existing_facts_markers_still_rejected(broken):
    with pytest.raises(ValueError,match='Missing or duplicate documentation block'):
        docs.agent_readme_facts(broken,'new facts')


def test_existing_facts_section_updates_only_its_managed_contents():
    original='Custom intro\n<!-- generated:facts:start -->\nold\n<!-- generated:facts:end -->\nCustom conclusion'
    value=docs.agent_readme_facts(original,'new facts')
    assert value.startswith('Custom intro\n') and value.endswith('\nCustom conclusion')
    assert '\nnew facts\n' in value and '\nold\n' not in value
