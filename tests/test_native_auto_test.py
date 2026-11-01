from __future__ import annotations

import asyncio
import copy
import json
import subprocess
from pathlib import Path

import httpx
import pytest

from sap_business_agents_test_agent.common import Store, TestAgentError, digest, now, read_json, write_json
from sap_business_agents_test_agent.comparison import compare, project
from sap_business_agents_test_agent.inventory import discover
from sap_business_agents_test_agent.plans import prepare
from sap_business_agents_test_agent.runner import finish_case, run_case
from sap_business_agents_test_agent import state, targets, workspace


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True).stdout.decode().strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / ".gitignore").write_text(".local-data/\n.env\n__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths=["tests"]\n', encoding="utf-8")
    (root / "src/a.py").write_text("VALUE = 0\n", encoding="utf-8")
    (root / "src/b.py").write_text("VALUE = 0\n", encoding="utf-8")
    (root / "src/stable.py").write_text("VALUE = 3\n", encoding="utf-8")
    (root / "tests/test_free_query_lifecycle.py").write_text("def test_existing():\n    assert True\n", encoding="utf-8")
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "test@localhost")
    git(root, "config", "user.name", "Test")
    git(root, "add", "--all")
    git(root, "commit", "-m", "Base")
    return root


def cases(count=2, kind="native", mode="simulated"):
    return [{"id": f"case-{i}", "feature": "free-query", "operation": "query", "scenario": f"scenario-{i}",
             "kind": kind, "mode": mode, "assertions": ["business_result_matches"],
             "impact_paths": [f"src/{'a' if i == 0 else 'b' if i == 1 else 'stable'}.py"], "depends_on": []}
            for i in range(count)]


def start_task(repository, definitions=None, limit=3):
    store = Store(repository)
    plan = prepare(store, ["free-query"], definitions or cases(), max_repairs=limit)
    task = state.start(store, plan["id"], plan["digest"])
    return store, plan, task


def outcome(store, task_id, case_id, status="passed", code="check_passed"):
    owned = state.claim(store, task_id, case_id)
    task = store.get("task", task_id)
    path = f"evidence/{task['round']}/{case_id}/native-observation.json"
    write_json(store.task_dir(task_id) / path, {"assertions": {"actual_observation": status}, "at": now()})
    return state.record(store, task_id, case_id, owned["ticket"]["token"],
                        {"status": status, "code": code, "evidence": [path]})


def drain(store, task, statuses=None):
    task = store.get("task", task["id"])
    for case_id in task["queue"]:
        status, code = (statuses or {}).get(case_id, ("passed", "checks_passed"))
        outcome(store, task["id"], case_id, status, code)
    return store.get("task", task["id"])


def verify_native_fix(repository):
    store, plan, task = start_task(repository)
    drain(store, task, {"case-0": ("failed", "first_fault"), "case-1": ("failed", "second_fault")})
    state.begin_repair(store, task["id"])
    candidate = Path(task["workspace"]["source"])
    (candidate / "src/a.py").write_text("VALUE = 3\n", encoding="utf-8")
    (candidate / "src/b.py").write_text("VALUE = 3\n", encoding="utf-8")
    state.verify_repair(store, task["id"])
    drain(store, task)
    state.begin_final(store, task["id"])
    drain(store, task)
    state.complete(store, task["id"])
    return store, task


def test_inventory_discovers_all_categories_and_current_objects(repository):
    agent = repository / "agents/MM/example/agent.json"
    agent.parent.mkdir(parents=True)
    write_json(agent, {"slug": "example", "version": "1.2.3", "validation": {"verdict": "BLOCKED"}})
    value = discover(repository)
    assert len(value["features"]) == 8
    assert {f["id"] for f in value["features"] if f["default"]} == {"free-query", "workflows", "agent-management"}
    fixed = next(f for f in value["features"] if f["id"] == "fixed-agents")
    assert fixed["children"][0]["validation"]["verdict"] == "BLOCKED"
    assert fixed["children"][0]["source"] == "agents/MM/example/agent.json"


def test_selection_generation_do_not_start_or_read_sap(repository):
    store = Store(repository)
    inventory = discover(repository)
    plan = prepare(store, ["free-query"], cases(), inventory["digest"])
    assert plan["selected"] == ["free-query"]
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM documents WHERE kind='task'").fetchone()[0] == 0
    assert (store.root / "suites" / plan["id"] / "case-0.py").is_file()
    assert not list((store.root / "runs").glob("*"))


def test_stale_selection_plan_and_duplicate_start(repository):
    store = Store(repository)
    inventory = discover(repository)
    plan = prepare(store, ["free-query"], cases(), inventory["digest"])
    first = state.start(store, plan["id"], plan["digest"])
    assert state.start(store, plan["id"], plan["digest"])["id"] == first["id"]
    (repository / "src/a.py").write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(TestAgentError, match="selection_stale"):
        prepare(store, ["free-query"], cases(), inventory["digest"])
    with pytest.raises(TestAgentError, match="plan_stale"):
        state.start(store, plan["id"], plan["digest"], "another-start")


def test_snapshot_includes_dirty_untracked_and_deleted_but_not_secrets(repository):
    (repository / "src/a.py").write_text("VALUE = 9\n", encoding="utf-8")
    (repository / "src/extra.py").write_text("EXTRA = True\n", encoding="utf-8")
    (repository / "src/b.py").unlink()
    (repository / ".env").write_text("SAP_PASSWORD=never-copy\n", encoding="utf-8")
    before = git(repository, "status", "--porcelain")
    store, plan, task = start_task(repository)
    source = Path(task["workspace"]["source"])
    assert (source / "src/a.py").read_text() == "VALUE = 9\n"
    assert (source / "src/extra.py").is_file()
    assert not (source / "src/b.py").exists()
    assert not (source / ".env").exists()
    assert git(source, "branch", "--show-current") == "main"
    assert before == git(repository, "status", "--porcelain")


def test_collects_multiple_errors_before_batch_repair_and_targets_only_impacted(repository):
    store, plan, task = start_task(repository, cases(3))
    outcome(store, task["id"], "case-0", "failed", "first_fault")
    with pytest.raises(TestAgentError, match="round_not_finished"):
        state.begin_repair(store, task["id"])
    outcome(store, task["id"], "case-1", "failed", "second_fault")
    outcome(store, task["id"], "case-2")
    task = state.begin_repair(store, task["id"])
    assert len(task["repair_history"][0]["groups"]) == 2
    source = Path(task["workspace"]["source"])
    (source / "src/a.py").write_text("VALUE = 3\n", encoding="utf-8")
    (source / "src/b.py").write_text("VALUE = 3\n", encoding="utf-8")
    task = state.verify_repair(store, task["id"])
    assert task["queue"] == ["case-0", "case-1"]
    assert task["round"] == 1
    assert task["phase"] == "verifying"


def test_fixture_lifecycle_writes_do_not_change_repair_candidate(repository):
    store, plan, task = start_task(repository)
    target = workspace.prepare_target(store, task["id"])
    (target / "agents/test/object").mkdir(parents=True)
    (target / "agents/test/object/agent.json").write_text("{}", encoding="utf-8")
    assert workspace.changes(store, task["id"]) == {}
    assert state.claim(store, task["id"], "case-0")["ticket"]
    assert not (repository / "agents/test").exists()


def test_no_silent_replay_after_native_interruption(repository):
    store, plan, task = start_task(repository)
    first = state.claim(store, task["id"], "case-0")
    assert first["ticket"]
    with pytest.raises(TestAgentError, match="case_already_running"):
        state.claim(store, task["id"], "case-0")
    with pytest.raises(TestAgentError, match="owned_cases_require_reconciliation"):
        state.resume(store, task["id"])
    task = state.resume(store, task["id"], ["case-0"])
    assert task["results"]["0"]["case-0"]["code"] == "interrupted_execution_unknown"
    assert state.claim(store, task["id"], "case-0")["already_recorded"] is True


def test_cancel_does_not_count_late_success_as_passed(repository):
    store, plan, task = start_task(repository)
    claim = state.claim(store, task["id"], "case-0")
    state.cancel(store, task["id"])
    evidence = store.task_dir(task["id"]) / "evidence/cancel.json"
    write_json(evidence, {"stopped": True})
    result = state.record(store, task["id"], "case-0", claim["ticket"]["token"],
                          {"status": "passed", "evidence": ["evidence/cancel.json"]})
    assert result["status"] == "cancelled"
    assert store.get("task", task["id"])["phase"] == "cancelled"


def test_patch_application_preserves_initial_edits_and_git_index(repository):
    (repository / "src/stable.py").write_text("VALUE = 8 # user edit\n", encoding="utf-8")
    git(repository, "add", "src/stable.py")
    initial_index = git(repository, "diff", "--cached")
    store, task = verify_native_fix(repository)
    result = workspace.apply_patch(store, task["id"])
    assert result["status"] == "applied"
    assert (repository / "src/a.py").read_text() == "VALUE = 3\n"
    assert (repository / "src/stable.py").read_text() == "VALUE = 8 # user edit\n"
    assert git(repository, "diff", "--cached") == initial_index
    assert workspace.apply_patch(store, task["id"])["status"] == "applied"


def test_application_conflict_does_not_partially_write(repository):
    store, task = verify_native_fix(repository)
    (repository / "src/b.py").write_text("VALUE = 11 # later edit\n", encoding="utf-8")
    with pytest.raises(TestAgentError, match="application_conflict"):
        workspace.apply_patch(store, task["id"])
    assert (repository / "src/a.py").read_text() == "VALUE = 0\n"
    assert "later edit" in (repository / "src/b.py").read_text()


def test_apply_failure_rolls_back_already_written_files(repository, monkeypatch):
    store, task = verify_native_fix(repository)
    original = workspace.atomic_bytes
    def fail_second(path, data):
        if path == repository / "src/b.py" and data.replace(b"\r\n", b"\n") == b"VALUE = 3\n":
            raise OSError("simulated disk failure")
        return original(path, data)
    monkeypatch.setattr(workspace, "atomic_bytes", fail_second)
    with pytest.raises(OSError):
        workspace.apply_patch(store, task["id"])
    assert (repository / "src/a.py").read_text() == "VALUE = 0\n"
    assert (repository / "src/b.py").read_text() == "VALUE = 0\n"
    assert read_json(store.task_dir(task["id"]) / "application.json")["status"] == "rolled_back"


def test_corrupted_evidence_cannot_authorize_application(repository):
    store, task = verify_native_fix(repository)
    final = state.current_results(store.get("task", task["id"]))
    ref = final["case-0"]["evidence"][0]
    (store.task_dir(task["id"]) / ref).write_text("changed", encoding="utf-8")
    with pytest.raises(TestAgentError, match="evidence_changed_after_verification"):
        workspace.apply_patch(store, task["id"])


def test_failed_final_review_never_applies_patch(repository):
    store, plan, task = start_task(repository)
    drain(store, task, {"case-0": ("failed", "fault")})
    state.begin_repair(store, task["id"])
    (Path(task["workspace"]["source"]) / "src/a.py").write_text("VALUE = 3\n", encoding="utf-8")
    state.verify_repair(store, task["id"])
    drain(store, task)
    state.begin_final(store, task["id"])
    drain(store, task, {"case-1": ("failed", "new_regression")})
    state.complete(store, task["id"])
    with pytest.raises(TestAgentError, match="application_not_verified"):
        workspace.apply_patch(store, task["id"])


def test_stagnant_root_is_retired_after_two_rounds(repository):
    store, plan, task = start_task(repository, cases(1))
    drain(store, task, {"case-0": ("failed", "same_fault")})
    for index in range(2):
        state.begin_repair(store, task["id"])
        (Path(task["workspace"]["source"]) / "src/a.py").write_text(f"VALUE = {index + 1}\n", encoding="utf-8")
        state.verify_repair(store, task["id"])
        drain(store, task, {"case-0": ("failed", "same_fault")})
    assert store.get("task", task["id"])["stalled_groups"]
    with pytest.raises(TestAgentError, match="no_repairable_failures"):
        state.begin_repair(store, task["id"])


def test_repair_budget_and_dependency_chains(repository):
    definitions = cases(2)
    definitions[1]["depends_on"] = ["case-0"]
    store, plan, task = start_task(repository, definitions, limit=0)
    with pytest.raises(TestAgentError, match="dependencies_pending"):
        state.claim(store, task["id"], "case-1")
    drain(store, task, {"case-0": ("failed", "fault")})
    with pytest.raises(TestAgentError, match="repair_limit_reached"):
        state.begin_repair(store, task["id"])


def envelope(rows):
    return {"rows": rows, "scope": {"company": "1710"}, "source_complete": True, "source_truncated": False}


def test_comparison_preserves_keys_currency_and_decimal_accuracy():
    base = envelope([{"id": "0001", "currency": "CNY", "amount": "0.30"}])
    contract = {"keys": ["id", "currency"], "required_fields": ["id", "currency", "amount"],
                "fields": {"amount": {"type": "decimal", "tolerance": "0"}}}
    assert compare(base, envelope([{"id": "0001", "currency": "CNY", "amount": "0.3"}]), contract)["status"] == "passed"
    assert compare(base, envelope([{"id": "1", "currency": "CNY", "amount": "0.3"}]), contract)["status"] == "failed"
    assert compare(base, envelope([{"id": "0001", "currency": "USD", "amount": "0.3"}]), contract)["status"] == "failed"
    with pytest.raises(TestAgentError, match="baseline_measure_dimensions_missing"):
        project(base["rows"], {"group_by": ["id"], "aggregates": {"sum": {"operation": "sum", "field": "amount", "dimensions": ["currency"]}}})


def test_zero_results_truncation_drift_duplicates_and_missing_fields():
    contract = {"keys": ["id"], "required_fields": ["id", "status"]}
    assert compare(envelope([]), envelope([]), contract)["status"] == "passed"
    assert compare({**envelope([]), "source_complete": False}, envelope([]), contract)["status"] == "inconclusive"
    base = envelope([{"id": "1", "status": "open"}])
    assert compare(base, base, contract, envelope([]))["code"] == "sap_data_changed"
    assert compare(base, envelope([{"id": "1"}]), contract)["code"] == "required_fields_missing"
    assert compare(base, envelope(base["rows"] * 2), contract)["code"] == "duplicate_business_keys"


def test_target_binding_proves_disposable_site_and_api(repository):
    store, plan, task = start_task(repository)
    proof = targets.marker(store, task["id"])
    data_root = store.task_dir(task["id"]) / "targets/0/source/.local-data"
    def handler(request):
        return httpx.Response(200, json=proof if request.url.path.endswith(targets.MARKER) else {"data_root": str(data_root)})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        binding = targets.bind(store, task["id"], "http://127.0.0.1:45671", "http://127.0.0.1:45672", client)
    assert binding["round"] == 0
    with pytest.raises(TestAgentError, match="target_must_be_loopback"):
        targets.bind(store, task["id"], "https://example.com", "http://127.0.0.1:45672")


def test_install_is_repeatable_preserves_configuration_and_refuses_conflicts(repository):
    from sap_business_agents_test_agent.install import install
    home = repository.parent / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text('# preserve this\nmodel = "existing-model"\n', encoding="utf-8")
    source = repository / "skills/sapba-auto-test"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("test skill", encoding="utf-8")
    first = install(repository, home, True)
    assert first["mcp_registered"] is True
    assert install(repository, home, True)["mcp_registered"] is True
    text = (home / "config.toml").read_text()
    assert text.startswith('# preserve this\nmodel = "existing-model"')
    assert text.count("[mcp_servers.sapba_auto_test]") == 1
    (home / "skills/sapba-auto-test/SKILL.md").write_text("user modification", encoding="utf-8")
    with pytest.raises(TestAgentError, match="skill_install_conflict"):
        install(repository, home, True)


def test_mcp_arguments_cannot_supply_credentials_urls_or_execute_during_generation(repository):
    from sap_business_agents_test_agent.mcp import response
    store = Store(repository)
    result = response(store, {"id": 1, "method": "tools/call", "params": {"name": "test_sap_read",
        "arguments": {"task_id": "x", "case_id": "x", "role": "baseline", "url": "https://example.com", "password": "secret"}}})
    assert result["result"]["isError"] is True
    assert "secret" not in json.dumps(result)
    assert response(store, {"method": "notifications/initialized"}) is None
    assert len(response(store, {"id": 2, "method": "tools/list"})["result"]["tools"]) == 6


def test_report_does_not_call_offline_seed_complete_business_coverage(repository):
    from sap_business_agents_test_agent.report import report
    store, plan, task = start_task(repository, cases(1))
    drain(store, task)
    state.begin_final(store, task["id"])
    drain(store, task)
    state.complete(store, task["id"])
    value = report(store, task["id"])
    assert value["coverage_gaps"]
    assert value["verdict"] == "inconclusive"
    assert value["formal_acceptance_granted"] is False


def test_real_subprocess_fault_batch_repair_and_final_review(repository):
    definitions = cases(3, kind="pytest", mode="offline")
    for index, name in enumerate(("a", "b", "stable")):
        path = repository / "tests" / f"test_{name}.py"
        path.write_text(f"from {name} import VALUE\ndef test_value():\n    assert VALUE == 3\n", encoding="utf-8")
        definitions[index]["test_refs"] = [path.relative_to(repository).as_posix()]
        definitions[index]["timeout_seconds"] = 20
    store, plan, task = start_task(repository, definitions)
    first = run_case(store, task["id"], "case-0")
    assert first["status"] == "failed"
    assert run_case(store, task["id"], "case-1")["status"] == "failed"
    assert run_case(store, task["id"], "case-2")["status"] == "passed"
    state.begin_repair(store, task["id"])
    candidate = Path(task["workspace"]["source"])
    (candidate / "src/a.py").write_text("VALUE = 3\n", encoding="utf-8")
    (candidate / "src/b.py").write_text("VALUE = 3\n", encoding="utf-8")
    state.verify_repair(store, task["id"])
    assert store.get("task", task["id"])["queue"] == ["case-0", "case-1"]
    for key in ("case-0", "case-1"):
        assert run_case(store, task["id"], key)["status"] == "passed"
    state.begin_final(store, task["id"])
    for key in ("case-0", "case-1", "case-2"):
        assert run_case(store, task["id"], key)["status"] == "passed"
    state.complete(store, task["id"])
    assert workspace.apply_patch(store, task["id"])["status"] == "applied"


def live_cases():
    value = cases(1, mode="live_sap")
    value[0]["baseline"] = {"plan": {"service_name": "API_PURCHASEORDER_PROCESS_SRV", "odata_version": "2.0",
        "entity_set": "A_PurchaseOrder", "http_method": "GET", "select_fields": ["PurchaseOrder"],
        "filters": [{"field": "PurchaseOrder", "operator": "eq", "value": "4500000001"}], "top": 10}}
    value[0]["comparison"] = {"keys": ["PurchaseOrder"], "required_fields": ["PurchaseOrder"]}
    return value


class FakeReadProvider:
    def __init__(self, rows=None, configured=True):
        self.rows = rows if rows is not None else [{"PurchaseOrder": "4500000001"}]
        self.calls = 0
        self.configured = configured

    async def health(self):
        return {"ok": self.configured}

    async def execute_plan(self, plan, query=""):
        self.calls += 1
        return {"ok": True, "provider_id": "test-fake", "source_complete": True,
                "source_truncated": False, "data": {"results": self.rows}}


def test_sap_recipe_is_task_bound_deduplicated_and_has_no_arbitrary_arguments(repository):
    from sap_business_agents_test_agent.sap import read
    store, plan, task = start_task(repository, live_cases())
    fake = FakeReadProvider()
    first = asyncio.run(read(store, task["id"], "case-0", "baseline", fake))
    second = asyncio.run(read(store, task["id"], "case-0", "baseline", fake))
    assert first["status"] == "completed"
    assert second["reused"] is True
    assert fake.calls == 1
    assert "rows" not in first
    with pytest.raises(TestAgentError, match="case_not_in_round"):
        asyncio.run(read(store, task["id"], "unselected-case", "baseline", fake))
    state.cancel(store, task["id"])
    with pytest.raises(TestAgentError, match="task_not_executing"):
        asyncio.run(read(store, task["id"], "case-0", "anchor", fake))


def test_sap_unavailable_is_blocked_and_unknown_reads_never_repeat(repository):
    from sap_business_agents_test_agent.sap import read
    store, plan, task = start_task(repository, live_cases())
    fake = FakeReadProvider(configured=False)
    receipt = asyncio.run(read(store, task["id"], "case-0", "baseline", fake))
    assert receipt["status"] == "blocked"
    assert fake.calls == 0
    with pytest.raises(TestAgentError, match="sap_read_outcome_unknown"):
        asyncio.run(read(store, task["id"], "case-0", "baseline", FakeReadProvider()))


def test_live_boolean_cannot_fake_a_comparison_and_data_drift_is_inconclusive(repository):
    from sap_business_agents_test_agent.sap import read
    store, plan, task = start_task(repository, live_cases())
    baseline = asyncio.run(read(store, task["id"], "case-0", "baseline", FakeReadProvider()))
    owned = state.claim(store, task["id"], "case-0")
    path = "evidence/0/case-0/execution/observation.json"
    observed = {"rows": [{"PurchaseOrder": "4500000001"}], "scope": live_cases()[0]["baseline"]["plan"],
                "source_complete": True, "source_truncated": False, "captured_at": now()}
    write_json(store.task_dir(task["id"]) / path, observed)
    result = {"status": "passed", "comparison_verified": True, "evidence": [path]}
    with pytest.raises(TestAgentError, match="live_comparison_required"):
        state.record(store, task["id"], "case-0", owned["ticket"]["token"], result)
    asyncio.run(read(store, task["id"], "case-0", "anchor", FakeReadProvider(rows=[])))
    value = finish_case(store, task["id"], "case-0", owned["ticket"]["token"], {**result, "observation": path})
    assert value["status"] == "inconclusive"
    assert value["code"] == "sap_data_changed"


def test_sample_binds_same_row_keys_without_guessing_and_budgets_are_checked(repository):
    from sap_business_agents_test_agent.sap import read, bind_sample
    definitions = live_cases()
    definitions[0]["sample"] = {"plan": {**definitions[0]["baseline"]["plan"], "filters": [], "top": 1}}
    definitions[0]["baseline"]["plan"]["filters"][0]["value"] = "$sample.PurchaseOrder"
    store, plan, task = start_task(repository, definitions)
    provider = FakeReadProvider()
    asyncio.run(read(store, task["id"], "case-0", "sample", provider))
    result = asyncio.run(read(store, task["id"], "case-0", "baseline", provider))
    body = read_json(store.task_dir(task["id"]) / result["artifact"])
    assert body["scope"]["filters"][0]["value"] == "4500000001"
    with pytest.raises(TestAgentError, match="sample_binding_missing"):
        bind_sample("$sample.Unknown", {"PurchaseOrder": "4500000001"})


def test_invalid_live_recipe_never_contacts_provider(repository):
    from sap_business_agents_test_agent.sap import read
    definitions = live_cases()
    definitions[0]["baseline"]["plan"]["url"] = "https://example.com"
    store, plan, task = start_task(repository, definitions)
    provider = FakeReadProvider()
    with pytest.raises(Exception):
        asyncio.run(read(store, task["id"], "case-0", "baseline", provider))
    assert provider.calls == 0


def test_operation_and_object_selection_are_enforced(repository):
    store = Store(repository)
    definition = cases(1)
    with pytest.raises(TestAgentError, match="case_outside_operation_selection"):
        prepare(store, ["free-query/feedback"], definition)
    definition[0]["operation"] = "feedback"
    plan = prepare(store, ["free-query/feedback"], definition)
    assert plan["operation_selection"] == {"free-query": ["feedback"]}
    agent = repository / "agents/MM/example/agent.json"
    agent.parent.mkdir(parents=True)
    write_json(agent, {"slug": "example"})
    definition[0].update(feature="fixed-agents", operation="execute", object_id="example")
    plan = prepare(store, ["fixed-agents@example"], definition)
    assert plan["object_selection"] == {"fixed-agents": ["example"]}


def test_environment_diagnosis_does_not_edit_away_a_blocker(repository):
    from sap_business_agents_test_agent.report import report
    store, plan, task = start_task(repository, cases(1))
    drain(store, task, {"case-0": ("failed", "authentication_failed")})
    state.diagnose(store, task["id"], [{"root_cause_id": "auth", "case_ids": ["case-0"],
        "category": "environment", "reason": "The configured account is unavailable."}])
    assert report(store, task["id"])["counts"] == {"blocked": 1}
    assert state.failure_groups(store, store.get("task", task["id"]))[0]["automatic_repair"] is False
    with pytest.raises(TestAgentError, match="no_repairable_failures"):
        state.begin_repair(store, task["id"])


class FakeSkillRegistry:
    def __init__(self, approved=True):
        self.approved, self.calls = approved, 0

    def get(self, skill_id):
        return {"read_only": self.approved, "validated": self.approved}

    def validate_input(self, skill_id, payload):
        assert "url" not in payload

    async def execute(self, skill_id, payload):
        self.calls += 1
        return {"status": "complete", "rows": [{"CompanyCode": "1710"}], "read_only": True,
                "completeness": {"source_complete": True, "paging_complete": True}}


def test_approved_skill_evidence_reuses_registry_without_claiming_get_transport(repository):
    from sap_business_agents_test_agent.sap import read
    definitions = live_cases()
    definitions[0]["baseline"] = {"skill_id": "sap-adt-table-export", "input": {"max_rows": 10}}
    registry = FakeSkillRegistry()
    store, plan, task = start_task(repository, definitions)
    receipt = asyncio.run(read(store, task["id"], "case-0", "baseline", skill_registry=registry))
    assert receipt["status"] == "completed"
    assert registry.calls == 1
    body = read_json(store.task_dir(task["id"]) / receipt["artifact"])
    assert body["method"] == "approved_skill"
    assert body["semantic_read_only"] is True
    assert body["source_complete"] is True
    assert body["rows"] == [{"CompanyCode": "1710"}]


def test_unapproved_skill_cannot_execute_and_adt_sample_is_bounded(repository):
    from sap_business_agents_test_agent.sap import read
    definitions = live_cases()
    definitions[0]["baseline"] = {"skill_id": "sap-adt-table-export", "input": {"max_rows": 10}}
    store, plan, task = start_task(repository, definitions)
    registry = FakeSkillRegistry(approved=False)
    with pytest.raises(TestAgentError, match="skill_not_approved"):
        asyncio.run(read(store, task["id"], "case-0", "baseline", skill_registry=registry))
    assert registry.calls == 0
    definitions[0]["sample"] = {"skill_id": "sap-adt-table-export", "input": {"max_rows": 101}}
    plan = prepare(store, ["free-query"], definitions)
    task = state.start(store, plan["id"], plan["digest"])
    registry = FakeSkillRegistry()
    with pytest.raises(TestAgentError, match="skill_row_budget_invalid"):
        asyncio.run(read(store, task["id"], "case-0", "sample", skill_registry=registry))
    assert registry.calls == 0


def test_generated_script_integrity_is_checked_before_execution(repository):
    store, plan, task = start_task(repository)
    path = store.root / "suites" / plan["id"] / "browser.mjs"
    path.write_text("// bypass assertions", encoding="utf-8")
    with pytest.raises(TestAgentError, match="generated_script_changed"):
        run_case(store, task["id"], "case-0")
    assert store.get("task", task["id"])["claims"] == {}


def test_frozen_regression_cannot_be_rewritten_to_get_pass(repository):
    definitions = cases(1, kind="pytest", mode="offline")
    definitions[0]["test_refs"] = ["tests/test_free_query_lifecycle.py"]
    store, plan, task = start_task(repository, definitions)
    drain(store, task, {"case-0": ("failed", "original_failure")})
    state.begin_repair(store, task["id"])
    path = Path(task["workspace"]["source"]) / "tests/test_free_query_lifecycle.py"
    path.write_text("def test_bypassed():\n    pass\n", encoding="utf-8")
    with pytest.raises(TestAgentError, match="frozen_regression_test_modified"):
        state.verify_repair(store, task["id"])


def test_locator_fix_is_audited_without_changing_oracle_or_original_plan(repository):
    from sap_business_agents_test_agent.plans import revise_locators
    definitions = cases(1, kind="browser")
    definitions[0]["actions"] = [{"type": "expect_text", "selector": "#old", "text": "correct result"}]
    store, plan, task = start_task(repository, definitions)
    drain(store, task, {"case-0": ("failed", "browser_locator_missing")})
    state.begin_repair(store, task["id"])
    evidence = state.all_results(store.get("task", task["id"]))["case-0"]["evidence"]
    with pytest.raises(TestAgentError, match="locator_revision_changes_oracle"):
        revise_locators(store, task["id"], [{"case_id": "case-0", "action_index": 0, "locator": {"text": "weaker assertion"}}], "observed selector change", evidence)
    corrections = [{"case_id": "case-0", "action_index": 0, "locator": {"selector": "#new"}}]
    corrected = revise_locators(store, task["id"], corrections, "observed selector change", evidence)
    assert corrected["id"] != plan["id"]
    assert corrected["cases"][0]["actions"][0]["text"] == "correct result"
    assert store.get("plan", plan["id"])["cases"][0]["actions"][0]["selector"] == "#old"
    assert revise_locators(store, task["id"], corrections, "observed selector change", evidence)["id"] == corrected["id"]
    task = state.verify_repair(store, task["id"])
    assert task["queue"] == ["case-0"]


def test_new_intent_runs_fresh_after_completion_but_active_and_retry_intents_coalesce(repository):
    from sap_business_agents_test_agent.cards import render
    store, plan, task = start_task(repository, cases(1))
    first_card = render(store, repository / ".local-data/first.html", plan_id=plan["id"])["payload"]
    second_card = render(store, repository / ".local-data/second.html", plan_id=plan["id"])["payload"]
    assert first_card["request_id"] != second_card["request_id"]
    assert state.start(store, plan["id"], plan["digest"], first_card["request_id"])["id"] == task["id"]
    drain(store, task)
    state.begin_final(store, task["id"])
    drain(store, task)
    state.complete(store, task["id"])
    assert state.start(store, plan["id"], plan["digest"], first_card["request_id"])["id"] == task["id"]
    fresh = state.start(store, plan["id"], plan["digest"], second_card["request_id"])
    assert fresh["id"] != task["id"]
    assert fresh["results"] == {"0": {}}
    assert state.start(store, plan["id"], plan["digest"], second_card["request_id"])["id"] == fresh["id"]


def test_cancelling_task_interrupts_owned_sap_read(repository):
    from sap_business_agents_test_agent.sap import read
    store, plan, task = start_task(repository, live_cases())
    class SlowProvider(FakeReadProvider):
        def __init__(self):
            super().__init__()
            self.started, self.stopped = False, False
        async def execute_plan(self, plan, query=""):
            self.started = True
            try:
                await asyncio.sleep(60)
            finally:
                self.stopped = True
    async def scenario():
        provider = SlowProvider()
        operation = asyncio.create_task(read(store, task["id"], "case-0", "baseline", provider))
        while not provider.started:
            await asyncio.sleep(0)
        state.cancel(store, task["id"])
        receipt = await asyncio.wait_for(operation, timeout=2)
        assert receipt["status"] == "cancelled"
        assert provider.stopped is True
    asyncio.run(scenario())


def test_skill_registry_cancellation_cleans_up_only_its_owned_subprocess(repository, monkeypatch):
    from sap_business_agents_platform.skills import SkillRegistry
    import sap_business_agents_platform.skills as skills_module
    registry = SkillRegistry(repository / "skillhub", repository / "config/skills.json")
    monkeypatch.setattr(registry, "get", lambda _: {"available": True, "read_only": True, "validated": True,
                                                  "entrypoint": "read-only-fixture.py", "timeout": 30})
    monkeypatch.setattr(registry, "validate_input", lambda *_: None)
    class OwnedProcess:
        returncode = None
        killed = False
        def __init__(self):
            self.started = False
        async def communicate(self):
            self.started = True
            await asyncio.sleep(60)
        def kill(self):
            self.killed = True
            self.returncode = -1
        async def wait(self):
            return self.returncode
    process = OwnedProcess()
    async def create(*args, **kwargs):
        return process
    monkeypatch.setattr(skills_module.asyncio, "create_subprocess_exec", create)
    async def scenario():
        operation = asyncio.create_task(registry.execute("fixture-skill", {}))
        while not process.started:
            await asyncio.sleep(0)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert process.killed is True
    asyncio.run(scenario())


def test_native_ui_execution_without_verified_target_is_blocked(repository):
    store, plan, task = start_task(repository, cases(1))
    result = run_case(store, task["id"], "case-0")
    assert result["status"] == "blocked"
    assert result["code"] == "isolated_target_not_bound"


def test_unrelated_environment_blocker_does_not_prevent_verified_product_fix(repository):
    store, plan, task = start_task(repository, cases(2))
    drain(store, task, {"case-0": ("failed", "product_fault"), "case-1": ("failed", "account_unavailable")})
    state.diagnose(store, task["id"], [
        {"root_cause_id": "product", "case_ids": ["case-0"], "category": "product", "reason": "Observed product mismatch."},
        {"root_cause_id": "account", "case_ids": ["case-1"], "category": "environment", "reason": "Independent account unavailable."}])
    state.begin_repair(store, task["id"])
    (Path(task["workspace"]["source"]) / "src/a.py").write_text("VALUE = 3\n", encoding="utf-8")
    verified = state.verify_repair(store, task["id"])
    assert verified["queue"] == ["case-0"]
    drain(store, task)
    state.begin_final(store, task["id"])
    drain(store, task, {"case-1": ("blocked", "account_unavailable")})
    state.complete(store, task["id"])
    assert workspace.apply_patch(store, task["id"])["status"] == "applied"
    from sap_business_agents_test_agent.report import report
    assert report(store, task["id"])["counts"] == {"passed": 1, "blocked": 1}
