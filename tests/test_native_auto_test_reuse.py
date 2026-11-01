from pathlib import Path
from types import SimpleNamespace
import asyncio
import copy
import json

import httpx
import pytest

from tests.test_native_auto_test import repository, cases, start_task, drain, FakeReadProvider
from sap_business_agents_test_agent import state, targets
from sap_business_agents_test_agent.common import TestAgentError, read_json
from sap_business_agents_test_agent.plans import prepare
from sap_business_agents_test_agent.cli import parser


def finished_task(repository):
    definitions = cases(1, kind="pytest", mode="offline")
    definitions[0]["test_refs"] = ["tests/test_free_query_lifecycle.py"]
    store, plan, task = start_task(repository, definitions)
    drain(store, task, {"case-0": ("passed", "ok")})
    state.begin_final(store, task["id"])
    drain(store, store.get("task", task["id"]), {"case-0": ("passed", "ok")})
    state.complete(store, task["id"])
    return store, plan, task, definitions


def test_fixture_revision_preserves_old_plan_and_assertions(repository):
    store, old, task, definitions = finished_task(repository)
    old_document = (store.root / "suites" / old["id"] / "plan.json").read_bytes()
    path = repository / "tests/test_free_query_lifecycle.py"
    path.write_text("def test_existing():\n    fixture = 1\n    assert True\n", encoding="utf-8")
    new = prepare(store, ["free-query"], definitions, parent_task=task["id"], revision_reason="Initialize obsolete fixture")
    assert new["id"] != old["id"]
    assert new["test_revision"]["files"][0]["path"] == "tests/test_free_query_lifecycle.py"
    assert (store.root / "suites" / old["id"] / "plan.json").read_bytes() == old_document
    assert store.get("task", task["id"])["plan_id"] == old["id"]


@pytest.mark.parametrize("body", ["    assert False\n", "    if False:\n        assert True\n", "    return\n    assert True\n",
                                  "    if True:\n        return\n    assert True\n", "    for x in []:\n        assert True\n"])
def test_fixture_revision_rejects_changed_or_bypassed_assertions(repository, body):
    store, old, task, definitions = finished_task(repository)
    (repository / "tests/test_free_query_lifecycle.py").write_text("def test_existing():\n" + body, encoding="utf-8")
    with pytest.raises(TestAgentError, match="fixture_revision_assertions_changed"):
        prepare(store, ["free-query"], definitions, parent_task=task["id"], revision_reason="Unsafe change")


def test_fixture_revision_cannot_change_business_baseline(repository):
    store, old, task, definitions = finished_task(repository)
    definitions[0]["assertions"] = ["weakened"]
    with pytest.raises(TestAgentError, match="fixture_revision_contract_changed"):
        prepare(store, ["free-query"], definitions, parent_task=task["id"], revision_reason="Change oracle")


def test_platform_binding_reuses_configured_api_but_rejects_lifecycle_scope(repository, monkeypatch):
    from sap_business_agents_platform.config import Settings
    monkeypatch.setattr(Settings, "from_env", lambda *args: SimpleNamespace(internal_api_url="http://127.0.0.1:8765"))
    store, plan, task = start_task(repository, cases(1))
    targets.marker(store, task["id"])
    saved = store.get("task", task["id"])
    def reply(request):
        value = ({"task_id": task["id"], "token": saved["target_token"]} if request.url.path.endswith(targets.MARKER)
                 else {"service": "sap-business-agents-local", "loopback_only": True,
                       "startup_binding": {"verified": True, "boot_id": "boot", "source_digest": "platform"}})
        return httpx.Response(200, json=value)
    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        bound = targets.bind_platform(store, task["id"], "http://127.0.0.1:4321", client)
        assert bound["api_url"] == "http://127.0.0.1:8765"
        assert bound["query_only"] is True
        with store.transaction() as db:
            modified = store.get("plan", plan["id"], db)
            modified["selected"] = ["agent-management"]
            # This scope guard is checked after the normal immutable-plan check;
            # pass an explicit alternate checked plan to isolate the guard here.
        monkeypatch.setattr(state, "task_plan", lambda *args: modified)
        with pytest.raises(TestAgentError, match="platform_reuse_requires_query_only_scope"):
            targets.bind_platform(store, task["id"], "http://127.0.0.1:4321", client)


def test_read_cli_has_only_task_case_role_not_credentials_or_urls():
    args = parser().parse_args(["sap-read", "--task", "t", "--case", "c", "--role", "baseline"])
    assert args.role == "baseline"
    with pytest.raises(SystemExit):
        parser().parse_args(["sap-read", "--task", "t", "--case", "c", "--role", "baseline", "--url", "https://foreign"])


def test_read_tools_use_platform_plugin_route(repository):
    from sap_business_agents_platform.config import Settings
    from sap_business_agents_platform.read_capabilities import build_read_capabilities
    from sap_business_agents_test_agent.sap import PlatformReadTool
    class RegisteredFake(FakeReadProvider):
        async def execute_plan(self, plan, query="", conversation_id=None):
            return await super().execute_plan(plan, query)
    fake = RegisteredFake()
    context = build_read_capabilities(Settings.from_env(repository), embedded_provider=fake)
    tool = PlatformReadTool(context)
    async def invoke():
        assert (await tool.health())["ok"] is True
        return await tool.execute_plan({"service_name": "TEST", "entity_set": "A", "odata_version": "2.0", "top": 1})
    result = asyncio.run(invoke())
    assert fake.calls == 1
    assert result["ok"] is True
    assert context.sap_read.plugin_metadata("execute_plan")["plugin_id"] == "embedded-sap-odata"


def test_frozen_platform_ui_requires_an_approved_origin(repository, monkeypatch):
    from sap_business_agents_platform.config import Settings
    monkeypatch.setattr(Settings, "from_env", lambda *args: SimpleNamespace(
        internal_api_url="http://127.0.0.1:8765", local_ui_origins=("http://127.0.0.1:4321",)))
    store, plan, task = start_task(repository, cases(1))
    targets.marker(store, task["id"])
    with pytest.raises(TestAgentError, match="platform_ui_origin_not_approved"):
        targets.bind_platform(store, task["id"], "http://127.0.0.1:5555", frozen_ui=True)


def test_native_transport_revision_preserves_cases_and_rejects_later_script_edits(repository):
    from sap_business_agents_test_agent.plans import revise_native_executor
    from sap_business_agents_test_agent.runner import check_scripts
    store, old, task = start_task(repository, cases(1))
    drain(store, task, {"case-0": ("failed", "response_body_navigation_race")})
    state.begin_repair(store, task["id"])
    path=store.task_dir(task["id"])/"evidence/native-v2.mjs"
    path.write_text("// Capture the one actual response before frontend navigation.\n",encoding="utf-8")
    new=revise_native_executor(store,task["id"],["case-0"],"Fix response collector", "evidence/native-v2.mjs")
    assert new["cases"] == old["cases"]
    assert store.get("plan",old["id"])["cases"] == old["cases"]
    verified=state.verify_repair(store,task["id"])
    assert verified["queue"] == ["case-0"]
    check_scripts(store,new)
    path.write_text("// Mutated after audit\n",encoding="utf-8")
    with pytest.raises(TestAgentError,match="native_executor_changed"):
        check_scripts(store,new)


def test_owned_native_identity_rejects_wrong_source(repository):
    store,plan,task=start_task(repository)
    targets.marker(store,task['id'])
    task=store.get('task',task['id'])
    proof={'task_id':task['id'],'token':task['target_token']}
    api={**proof,'data_root':str(store.task_dir(task['id'])/'targets/0/source/.local-data'),
         'source_digest':'wrong-source'}
    def respond(request):
        return httpx.Response(200,json=api if request.url.port==8765 else proof)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(TestAgentError,match='api_source_identity_not_proven'):
            targets.bind(store,task['id'],'http://127.0.0.1:4321','http://127.0.0.1:8765',client,native_identity=True)


def test_query_environment_references_existing_runtime_without_credentials(repository):
    from sap_business_agents_test_agent.workspace import dependency_environment
    store,plan,task=start_task(repository)
    environment=dependency_environment(store,task['id'],reuse_platform=True)
    assert environment['runtime_state_reference']==str(repository/'.local-data/sdk-runtimes')
    assert environment['platform_configuration_root']==str(repository)
    assert environment['native_identity'] is True
    assert not any(key in json.dumps(environment).lower() for key in ['sap_password','sap_username'])
    assert Path(environment['api_command'][1]).is_file()


def test_insufficient_evidence_storage_is_recorded_before_native_dispatch(repository,monkeypatch):
    from sap_business_agents_test_agent.runner import run_case
    store,plan,task=start_task(repository)
    monkeypatch.setattr('sap_business_agents_test_agent.runner.shutil.disk_usage',lambda _:SimpleNamespace(free=0))
    result=run_case(store,task['id'],'case-0')
    assert result['status']=='blocked' and result['code']=='evidence_storage_insufficient'
    saved=store.get('task',task['id'])
    assert saved['claims']=={}
    assert (store.task_dir(task['id'])/'evidence/0/case-0/execution/storage.json').is_file()


def test_full_scope_current_passes_can_be_frozen_as_final_review(repository):
    from sap_business_agents_test_agent import workspace
    store,plan,task=start_task(repository)
    drain(store,task,{'case-0':('failed','bug'),'case-1':('passed','ok')})
    state.begin_repair(store,task['id'])
    source=store.task_dir(task['id'])/'source'
    (source/'src/a.py').write_text('VALUE = 1\n',encoding='utf8')
    (source/'src/b.py').write_text('VALUE = 2\n',encoding='utf8')
    verifying=state.verify_repair(store,task['id'])
    drain(store,verifying,{'case-0':('passed','ok'),'case-1':('passed','ok')})
    reviewed=state.begin_final(store,task['id'],reuse_complete_round=True)
    assert reviewed['round']==verifying['round']==reviewed['final_round']
    assert reviewed['final_review']['mode']=='completed_full_scope_round'
    assert state.complete(store,task['id'])['regressions']==[]
    assert workspace.apply_patch(store,task['id'])['status']=='applied'


def test_prior_passes_cannot_replace_missing_current_round_tests(repository):
    store,plan,task=start_task(repository)
    drain(store,task,{'case-0':('failed','bug'),'case-1':('passed','ok')})
    state.begin_repair(store,task['id'])
    (store.task_dir(task['id'])/'source/src/a.py').write_text('VALUE = 1\n',encoding='utf8')
    verifying=state.verify_repair(store,task['id'])
    drain(store,verifying,{'case-0':('passed','ok')})
    with pytest.raises(TestAgentError,match='final_review_requires_complete_current_round'):
        state.begin_final(store,task['id'],reuse_complete_round=True)


def test_full_scope_final_review_rejects_evidence_tampering(repository):
    store,plan,task=start_task(repository)
    drain(store,task,{'case-0':('passed','ok'),'case-1':('passed','ok')})
    result=state.current_results(store.get('task',task['id']))['case-0']
    (store.task_dir(task['id'])/result['evidence_manifest'][0]['path']).write_text('tampered',encoding='utf8')
    with pytest.raises(TestAgentError,match='evidence_changed_after_verification'):
        state.begin_final(store,task['id'],reuse_complete_round=True)
