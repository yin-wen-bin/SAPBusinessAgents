"""Offline, no Runtime/SAP calls. All adapters are deterministic test doubles."""
import asyncio
import copy
import json
import time
from dataclasses import asdict, replace
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from sap_business_agents_platform.runtime_validation import (
    Registry, ValidationCase, Authorization, ValidationCaseResult, ValidationError,
    check_native_call, digest, execution_scope, plan, run,
)
from sap_business_agents_platform.runtime_validation_store import (
    BatchStore, atomic, artifact, capture, process_binding, read, verify_archive,
)
from sap_business_agents_platform import runtime_validation_qualification as q


def runtime(provider="workbuddy"):
    return {"provider_id": provider, "model": "model-a", "actual_model": "model-a", "model_identity_source": "assistant_message",
        "service_site": "china", "service_site_revision": 1, "service_site_digest": "site", "authentication_revision": 4}


def grant(case, binding=None, **kw):
    return Authorization(case.id, case.digest, case.mode, "isolated", digest({"po": "123"}), digest(binding or runtime()),
        time.time() + 60, "request-" + case.id, case.sap, **kw)


def test_required_cases_modes_and_acceptance_closure():
    registry = Registry()
    assert len(registry.requirements("free_query")) == 2
    assert {c.mode for c in registry.requirements("review_agent_feedback")} == {"bounded", "trusted_local"}
    assert registry.closure(["acceptance.campaign"]) == ["environment.current", "infrastructure.current", "acceptance.campaign"]
    assert registry.closure(["role.feedback"])[2:] == ["role.analysis", "role.feedback"]
    assert registry.closure(["sample.scope"])[2:] == ["sample.scope"]


def test_registry_unknown_cycles_and_per_case_versions():
    c = ValidationCase("a", ("author_draft",))
    with pytest.raises(ValidationError): Registry({"a": replace(c, requires=("b",))})
    with pytest.raises(ValidationError): Registry({"a": replace(c, requires=("a",))})
    first = c.digest
    registry = Registry({"a": c, "b": replace(c, id="b")})
    assert registry.entries["a"].digest == first
    assert replace(c, version="2").digest != first


@pytest.mark.parametrize("change", ["mode", "parameters", "target", "runtime", "expiry"])
def test_authorization_is_enforced_not_a_display_field(change):
    case = Registry().entries["agent.explain"]
    auth = grant(case)
    binding, target, params = runtime(), "isolated", {"po": "123"}
    if change == "mode": auth = replace(auth, mode="trusted_local")
    if change == "parameters": params["po"] = "all"
    if change == "target": target = "production"
    if change == "runtime": binding["model"] = "other"
    if change == "expiry": auth = replace(auth, expires_at=0)
    with pytest.raises(ValidationError): auth.check(case, binding, target, params)


def test_dependency_expansion_does_not_expand_authorization():
    registry = Registry()
    c = registry.entries["role.feedback"]
    value = plan(registry, [c.id], runtime(), {c.id: grant(c)}, {c.id: {"po": "123"}}, {c.id: "isolated"})
    assert value.cases[-2]["reason"] == "validation_not_authorized"
    assert value.cases[-1]["action"] == "run"


def test_native_scope_cannot_escalate_mode_or_tools():
    c = Registry().entries["agent.explain"]
    with execution_scope(c, grant(c), runtime(), "isolated", {"po": "123"}, "job"):
        check_native_call(runtime(), c.operations[0], "bounded")
        for op, mode, tools in [(c.operations[0], "trusted_local", False), ("free_query", "bounded", False), (c.operations[0], "bounded", True)]:
            with pytest.raises(ValidationError): check_native_call(runtime(), op, mode, tools=tools)
    check_native_call({"legacy": True}, "old-operation", "original-mode")


class FakeAdapter:
    def __init__(self, failed=(), dirty=False):
        self.failed, self.dirty, self.calls = set(failed), dirty, []
    def check_binding(self, value): pass
    def cleanup_confirmed(self, job_id): return not self.dirty
    async def execute(self, case, scope):
        self.calls.append(case.id)
        if case.id in self.failed: raise ValidationError("simulated_failure")
        result = ValidationCaseResult(case.id, case.digest, "passed", scope["job_id"], case.mode,
            digest(scope["binding"]), checks={name: True for name in case.assertions}, artifacts=[{"fixture": True}])
        return asdict(result)


def test_scheduler_collects_independent_failures_and_never_retries(tmp_path):
    a = ValidationCase("a", ("author_draft",))
    b = replace(a, id="b", requires=("a",))
    c = replace(a, id="c")
    registry = Registry({x.id: x for x in (a, b, c)})
    grants = {x.id: grant(x) for x in (a, b, c)}
    parameters = {x.id: {"po": "123"} for x in (a, b, c)}
    targets = {x.id: "isolated" for x in (a, b, c)}
    value = plan(registry, ["b", "c"], runtime(), grants, parameters, targets)
    adapter, store = FakeAdapter(["a"]), BatchStore(tmp_path)
    first = asyncio.run(run(value, registry, store, adapter, grants, parameters, targets))
    assert adapter.calls == ["a", "c"]
    assert first["a"]["status"] == "failed" and first["b"]["status"] == "blocked" and first["c"]["status"] == "passed"
    asyncio.run(run(value, registry, store, adapter, grants, parameters, targets))
    assert adapter.calls == ["a", "c"]


def test_cleanup_unknown_stops_independent_dispatch(tmp_path):
    c = ValidationCase("a", ("author_draft",))
    registry = Registry({"a": c, "b": replace(c, id="b")})
    grants = {k: grant(v) for k, v in registry.entries.items()}
    params = {k: {"po": "123"} for k in grants}
    targets = dict.fromkeys(grants, "isolated")
    adapter = FakeAdapter(["a"], dirty=True)
    result = asyncio.run(run(plan(registry, list(grants), runtime(), grants, params, targets), registry,
        BatchStore(tmp_path), adapter, grants, params, targets))
    assert adapter.calls == ["a"] and result["a"]["status"] == "cleanup_pending"


def test_claim_atomic_conflict_response_loss_and_late_result(tmp_path):
    store, case = BatchStore(tmp_path), ValidationCase("a", ("author_draft",))
    auth = grant(case)
    def claim(): return store.claim(case, runtime(), auth, "isolated", {"po": "123"})
    with ThreadPoolExecutor(4) as pool: values = list(pool.map(lambda _: claim(), range(8)))
    assert sum(new for new, _ in values) == 1
    record = values[0][1]
    assert len({r["job_id"] for _, r in values}) == 1
    assert store.existing_result(record)["status"] == "claimed"
    with pytest.raises(ValidationError, match="conflict"):
        store.claim(case, runtime(), replace(auth, request_id="other"), "isolated", {"po": "123"})
    store.cancel(record)
    with pytest.raises(ValidationError, match="late"):
        store.finish(record, {"status": "passed"})
    with pytest.raises(ValidationError, match="unproven"):
        store.reconcile(record, {"job_id": record["job_id"], "cleanup_complete": True})


def test_source_archive_contains_dirty_bytes_and_rejects_wrong_loaded_process(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "untracked.py").write_text("value = 'dirty'", encoding="utf-8")
    saved = capture(root, ["untracked.py"], tmp_path / "archives")
    assert capture(root, ["untracked.py"], tmp_path / "archives") == saved
    assert (Path(saved["directory"]) / "source/untracked.py").read_text() == "value = 'dirty'"
    with pytest.raises(ValidationError, match="not_frozen"): process_binding(saved)
    with pytest.raises(ValidationError): capture(root, ["../private.json"], tmp_path / "archives")
    with pytest.raises(ValidationError): capture(root, [".env"], tmp_path / "archives")
    (Path(saved["directory"]) / "source/untracked.py").write_text("changed")
    with pytest.raises(ValidationError, match="changed"): verify_archive(saved["directory"], saved["digest"])


class Environment:
    def __init__(self, root): self.root, self.value = root, {}
    def state(self): return copy.deepcopy(self.value)
    def mutate(self, fn): fn(self.value)
    def maintenance(self):
        from contextlib import nullcontext
        return nullcontext()


def pass_fixture(tmp_path, case=None, dependencies=None):
    env = Environment(tmp_path / "env")
    q.activate(env, lambda: {"all_writers_checked": True, "old_writers": [], "active_jobs": [], "cleanup_pending": [],
        "state_digest": digest(env.state()), "process_inventory_digest": "process-proof", "checked_at": time.time()})
    case = case or ValidationCase("a", ("author_draft",))
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    (source / "code.py").write_text("code")
    archive = capture(source, ["code.py"], tmp_path / "archives")
    bound = q.binding(runtime(), case, dependencies or {"business": "same"}, "execution")
    root = tmp_path / ("proof-" + case.id)
    atomic(root / "output.json", {"redacted": True})
    result = {**asdict(ValidationCaseResult(case.id, case.digest, "passed", "case-" + case.id, case.mode, digest(runtime()),
        checks=dict.fromkeys(case.assertions, True), artifacts=[artifact(root, root / "output.json")])), "environment_digest": "env"}
    atomic(root / "checker.json", result)
    job = {"status": "completed", "cleanup_complete": True, "validation_job": True,
        'validation_parent_job': result['job_id'], 'operation': case.operations[0], 'environment_digest': 'env',
        'runtime_digest': result['binding_digest'], 'permission_mode': case.mode, 'task_id': 'native-' + case.id,
        "result_metadata": {"structured_terminal": True, "actual_model": "model-a", 'identity_source': 'assistant_message'}, "validation_case_binding": bound}
    job_id = "native-" + case.id
    atomic(env.root / "jobs" / (digest(job_id) + ".json"), job)
    key = q.register(env, case, bound, result, source_snapshot=archive, artifact_root=root,
        checker_report={"path": "checker.json", "digest": digest(result)},
        owned_jobs=[{"job_id": job_id, "digest": digest(job)}], execution_kind="live")
    return env, case, bound, key, archive


def test_projection_requires_every_case_not_latest_operation_pass(tmp_path):
    env, case, bound, key, archive = pass_fixture(tmp_path)
    other = replace(case, id="other")
    registry = Registry({case.id: case, other.id: other})
    bindings = {case.id: bound, other.id: q.binding(runtime(), other, {"business": "same"}, "execution")}
    values = q.projection(env, env.state(), {"environment_digest": "env"}, runtime(), bindings, registry)
    assert values["author_draft"]["status"] == "unverified"
    assert values["author_draft"]["missing_cases"] == ["other"]
    assert q.select(env, env.state(), bound, "env")["id"] == key


def test_offline_or_boolean_only_proof_cannot_grant_pass(tmp_path):
    env, case, bound, key, archive = pass_fixture(tmp_path)
    record = env.value["validation_ledger"][key]
    assert q.verify_pass(env, record)
    record = copy.deepcopy(record)
    record["result"]["artifacts"] = []
    assert not q.verify_pass(env, record)
    (Path(archive["directory"]) / "source/code.py").write_text("wrong source")
    assert not q.verify_pass(env, env.value["validation_ledger"][key])


def test_attempt_failure_does_not_erase_business_but_case_failure_blocks(tmp_path):
    env, case, bound, key, _ = pass_fixture(tmp_path)
    q.append(env.value, {"kind": "failure", "binding": bound, "environment_digest": "env", "scope": "attempt", "cleanup_pending": False})
    assert q.select(env, env.state(), bound, "env")
    q.append(env.value, {"kind": "failure", "binding": bound, "environment_digest": "env", "scope": "case", "failure_code": "invalid_enum"})
    assert q.select(env, env.state(), bound, "env") is None
    assert env.value["validation_ledger"][key]["kind"] == "case_pass"


def test_protocol_clearance_requires_writers_not_only_idle_tasks(tmp_path):
    env = Environment(tmp_path)
    with pytest.raises(ValidationError): q.activate(env, lambda: {"active_jobs": []})
    assert not env.value


@pytest.mark.parametrize("provider", ["codex", "workbuddy", "third-sdk"])
def test_common_result_semantics_do_not_depend_on_provider(tmp_path, provider):
    case = ValidationCase("a", ("author_draft",))
    registry, binding = Registry({case.id: case}), runtime(provider)
    grants = {case.id: grant(case, binding)}
    params, targets = {case.id: {"po": "123"}}, {case.id: "isolated"}
    results = asyncio.run(run(plan(registry, [case.id], binding, grants, params, targets), registry,
        BatchStore(tmp_path), FakeAdapter(), grants, params, targets))
    assert results[case.id]["status"] == "passed"
