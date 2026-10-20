"""Private offline qualification inventory. No SDK/model/auth/SAP dispatch.

Default: read-only. Optional writes only append history/reuse receipts after
platform-owned provenance checks. Never edits legacy PASS or installation data.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
from sap_business_agents_platform.workbuddy_environment import WorkBuddyError, atomic_json
from sap_business_agents_platform.sdk_manager import SDKManagerError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--model", required=True)
    parser.add_argument("--migrate-history", action="store_true", help="Append legacy audit index; does not grant eligibility")
    parser.add_argument("--legacy-snapshot", type=Path, action="append", default=[], help="Exact saved Runtime snapshot JSON for history attribution, never a new PASS")
    parser.add_argument("--reuse-record", help="Explicitly append a proven cross-release business reuse receipt")
    parser.add_argument("--reuse-model-check", action="store_true", help="Append a proven model-check reuse receipt; no model call")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    manager = WorkBuddyManager(args.root)
    try:
        if args.migrate_history:
            snapshots = []
            for path in args.legacy_snapshot:
                path = path.resolve()
                if (args.root.resolve() / ".local-data") not in path.parents or path.stat().st_size > 2 * 1024 * 1024:
                    parser.error("Legacy snapshots must be bounded repository-local artifacts")
                snapshots.append(json.loads(path.read_text(encoding="utf-8")))
            manager.migrate_qualification_history(snapshots)
        if args.reuse_model_check:
            manager.apply_model_check_reuse(args.model)
        if args.reuse_record:
            manager.apply_qualification_reuse(args.model, args.reuse_record)
        report = manager.qualification_plan(args.model)
    except SDKManagerError as exc:
        report = {"status": "blocked", "error": {"code": exc.code}}
        print(json.dumps(report, ensure_ascii=False))
        return 1
    if args.output:
        destination = args.output.resolve()
        allowed = (args.root.resolve() / ".local-data/workbuddy-qualification-reports").resolve()
        if allowed not in destination.parents:
            parser.error("Output must be inside .local-data/workbuddy-qualification-reports")
        atomic_json(destination, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
