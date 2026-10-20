"""Opaque fixture failures require a later complete live case, never a waiver."""
import copy
from pathlib import Path

import pytest

from sap_business_agents_platform import runtime_validation_qualification as q
from sap_business_agents_platform.runtime_validation import digest
from sap_business_agents_platform.runtime_validation_store import atomic, read
from tests.test_runtime_validation import pass_fixture


def fixture(tmp_path):
    return pass_fixture(tmp_path, dependencies={"business": "same", "case_data": "frozen-inputs"})


def failure_body(bound, **updates):
    return {"kind": "failure", "scope": "unknown", "failure_code": "validation_fixture_failed",
        "binding": copy.deepcopy(bound), "environment_digest": "env", "cleanup_pending": False, **updates}


def register_retest(env, case, original):
    result = copy.deepcopy(original["result"])
    result["job_id"] += "-retest"
    root = original["artifact_root"]
    atomic(Path(root) / "checker-retest.json", result)
    source_job = read(env.root / "jobs" / (digest(original["owned_jobs"][0]["job_id"]) + ".json"))
    source_job["task_id"] += "-retest"
    source_job["validation_parent_job"] = result["job_id"]
    atomic(env.root / "jobs" / (digest(source_job["task_id"]) + ".json"), source_job)
    return q.register(env, case, original["binding"], result,
        source_snapshot=original["source_snapshot"], artifact_root=root,
        checker_report={"path": "checker-retest.json", "digest": digest(result)},
        owned_jobs=[{"job_id": source_job["task_id"], "digest": digest(source_job)}], execution_kind="live")


@pytest.mark.parametrize("code", ["validation_execution_error", "validation_fixture_failed"])
def test_new_live_pass_resolves_actual_wrapper_code_append_only(tmp_path, code):
    env, case, bound, old_id, _ = fixture(tmp_path)
    failure_id = q.append(env.value, failure_body(bound, failure_code=code))
    saved = copy.deepcopy(env.state())
    assert q.select(env, env.state(), bound, "env") is None
    new_id = register_retest(env, case, saved["validation_ledger"][old_id])
    assert new_id != old_id
    assert q.select(env, env.state(), bound, "env")["id"] == new_id
    for key, value in saved["validation_ledger"].items():
        assert env.value["validation_ledger"][key] == value
    resolutions = [r for r in q.ledger(env.state()).values() if r.get("kind") == "resolution"]
    assert len(resolutions) == 1
    assert resolutions[0]["failure_id"] == failure_id and resolutions[0]["pass_id"] == new_id
    before = env.state()
    assert register_retest(env, case, saved["validation_ledger"][old_id]) == new_id
    assert env.state() == before


@pytest.mark.parametrize("change", ["unknown_code", "component", "environment", "cleanup", "cleanup_missing",
    "case", "case_version", "case_data", "data_missing", "model", "site", "account", "mode", "release"])
def test_wrapper_resolution_never_crosses_failure_or_binding_boundary(tmp_path, change):
    env, case, bound, old_id, _ = fixture(tmp_path)
    failure = failure_body(bound)
    if change == "unknown_code": failure["failure_code"] = "unknown_provider_fault"
    elif change in {"component", "environment"}:
        failure["scope"] = change
        failure["components"] = {"business": "same"}
    elif change == "cleanup": failure["cleanup_pending"] = True
    elif change == "cleanup_missing": failure.pop("cleanup_pending")
    elif change == "case": failure["binding"]["case_id"] = "another-case"
    elif change == "case_version": failure["binding"]["case_digest"] = "another-version"
    elif change == "case_data": failure["binding"]["dependencies"]["case_data"] = "other-inputs"
    elif change == "data_missing": failure["binding"]["dependencies"].pop("case_data")
    elif change == "model": failure["binding"]["runtime"]["actual_model"] = "another-model"
    elif change == "site": failure["binding"]["runtime"]["service_site"] = "international"
    elif change == "account": failure["binding"]["runtime"]["authentication_revision"] += 1
    elif change == "mode": failure["binding"]["mode"] = "trusted_local"
    elif change == "release": failure["environment_digest"] = "another-release"
    failure_id = q.append(env.value, failure)
    new_id = register_retest(env, case, env.state()["validation_ledger"][old_id])
    assert q.valid(env.value["validation_ledger"][new_id])
    assert not any(r.get("failure_id") == failure_id for r in q.ledger(env.state()).values())
    if change in {"unknown_code", "component", "environment", "cleanup", "cleanup_missing", "case_version", "case_data", "data_missing"}:
        assert failure_id in q.blocks(env.state(), bound, "env", environment=env)


def test_resolution_is_rechecked_against_real_provenance_and_newer_failure(tmp_path):
    env, case, bound, old_id, _ = fixture(tmp_path)
    first = q.append(env.value, failure_body(bound))
    new_id = register_retest(env, case, env.state()["validation_ledger"][old_id])
    assert not q.blocks(env.state(), bound, "env", environment=env)
    record = env.value["validation_ledger"][new_id]
    report_path = Path(record["artifact_root"]) / record["checker_report"]["path"]
    original_report = read(report_path)
    atomic(report_path, {"status": "passed"})
    assert first in q.blocks(env.state(), bound, "env", environment=env)
    atomic(report_path, original_report)
    # A new opaque failure is not covered by the earlier successful attempt.
    later = q.append(env.value, failure_body(bound, job_id="later-attempt"))
    assert later in q.blocks(env.state(), bound, "env", environment=env)
    assert q.select(env, env.state(), bound, "env") is None


def test_reuse_or_missing_environment_cannot_resolve_wrapper_failure(tmp_path):
    env, case, bound, old_id, _ = fixture(tmp_path)
    failed = q.append(env.value, failure_body(bound))
    original = env.state()["validation_ledger"][old_id]
    reused = q.append(env.value, {**{k: v for k, v in original.items() if k not in {"id", "sequence", "created_at"}},
        "execution_kind": "reuse"})
    q.append(env.value, {"kind": "resolution", "resolution": "unknown_attempt_superseded_by_live_case",
        "failure_id": failed, "pass_id": reused})
    assert failed in q.blocks(env.state(), bound, "env", environment=env)
    live = register_retest(env, case, original)
    assert failed in q.blocks(env.state(), bound, "env")
    assert q.verify_pass(env, env.value["validation_ledger"][live])


def test_shared_resolution_proof_is_verified_once_per_inspection(tmp_path, monkeypatch):
    env, case, bound, old_id, _ = fixture(tmp_path)
    for index in range(3):
        q.append(env.value, failure_body(bound, job_id=f"old-{index}"))
    new_id = register_retest(env, case, env.state()["validation_ledger"][old_id])
    real_verify, seen = q.verify_pass, []
    def verify(environment, record):
        seen.append(record["id"])
        return real_verify(environment, record)
    monkeypatch.setattr(q, "verify_pass", verify)
    assert not q.blocks(env.state(), bound, "env", environment=env)
    assert seen == [new_id]
    assert not q.blocks(env.state(), bound, "env", environment=env)
    assert seen == [new_id, new_id]  # No persisted/cross-call validity cache.
