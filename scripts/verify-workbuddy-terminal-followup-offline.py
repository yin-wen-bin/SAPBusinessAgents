"""Freeze/compare offline-only terminal diagnostics changes, never live calls."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.workbuddy_environment import WorkBuddyEnvironment, atomic_json, file_hash

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / ".local-data/workbuddy-terminal-followup-offline-20261012"
ALLOWED = {"src/sap_business_agents_platform/workbuddy_worker.py", "src/sap_business_agents_platform/workbuddy_diagnostics.py",
           "src/sap_business_agents_platform/workbuddy_supervisor.py"}


def controls():
    settings = Settings.from_env(ROOT)
    freeze = json.loads((ROOT / ".local-data/workbuddy-validation/china-20261012-equivalent-feedback-release/freeze.json").read_text(encoding="utf-8"))
    paths = [ROOT / name for name in freeze["controls"]["files"]]
    paths += [settings.sdk_runtime_state_path, settings.data_root / "sdk-runtimes/authentication.json",
              ROOT / ".local-data/runtimes/workbuddy/state.json"]
    paths += [ROOT / ".local-data/workbuddy-native-terminal-offline-20261012" / name for name in ("result-summary.json", "repair-plan.json")]
    return {"files": {str(p.relative_to(ROOT)).replace("\\", "/"): file_hash(p) for p in paths},
        "dependencies": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "finish"])
    parser.add_argument("--xml")
    args = parser.parse_args()
    environment = WorkBuddyEnvironment(ROOT)
    release = environment.release()
    if args.command == "prepare":
        assert not DESTINATION.exists(), "offline_fresh_directory_required"
        assert release["environment_digest"] == "2223d96a09fb68ed853496c1c0daead9f64eae437c92f85042706676b8045cf4"
        atomic_json(DESTINATION / "before.json", {"controls": controls(), "environment_digest": release["environment_digest"],
            "manifest_sha256": file_hash(Path(release["directory"]) / "environment.json")})
        result = {"phase": "prepared", "model_calls": 0, "sap_calls": 0}
    else:
        import xml.etree.ElementTree as ET
        before = json.loads((DESTINATION / "before.json").read_text(encoding="utf-8"))
        current = controls()
        assert current["dependencies"] == before["controls"]["dependencies"]
        changed = [name for name, value in current["files"].items() if value != before["controls"]["files"][name]]
        assert set(changed) <= ALLOWED and set(changed) == ALLOWED, "offline_unexpected_source_change"
        assert release["environment_digest"] == before["environment_digest"]
        assert file_hash(Path(release["directory"]) / "environment.json") == before["manifest_sha256"]
        xml = Path(args.xml).resolve(strict=True)
        assert xml.is_relative_to(ROOT / ".local-data")
        suites = ET.parse(xml).getroot().findall(".//testsuite")
        failures = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
        assert suites and not failures
        tests = sum(int(s.get("tests", 0)) for s in suites)
        skipped = sum(int(s.get("skipped", 0)) for s in suites)
        result = {"phase": "offline_fixes_passed_live_not_run", "changed_sources": changed,
            "tests": {"passed": tests-skipped, "skipped": skipped, "failures": failures, "xml": str(xml)},
            "installed_release_unchanged": True, "environment_digest": release["environment_digest"],
            "codex_configuration_source_account_dependencies_unchanged": True,
            "old_qualification_and_reports_unchanged": True,
            "model_calls": 0, "authentication_calls": 0, "sap_calls": 0,
            "schema_or_candidate_repaired": False, "worker_release_prepared": False,
            "native_timeout_strategy": "Supervisor deadline; native CLI timeout propagation remains unverified",
            "workbuddy_enabled": False, "committed_or_pushed": False}
        atomic_json(DESTINATION / "result-summary.json", result)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
