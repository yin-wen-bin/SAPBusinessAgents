from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .common import Store, TestAgentError, read_json


def parser():
    root = argparse.ArgumentParser(description="SAPBusinessAgents native Codex test helpers; no AI runtime is started.")
    root.add_argument("--repository", type=Path, default=Path.cwd())
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("inventory")
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--select", nargs="+")
    prepare.add_argument("--cases", type=Path)
    prepare.add_argument("--inventory-digest")
    prepare.add_argument("--max-repairs", type=int, default=3)
    start = sub.add_parser("start")
    start.add_argument("--plan", required=True)
    start.add_argument("--digest", required=True)
    start.add_argument("--request-id")
    card = sub.add_parser("card")
    card.add_argument("--output", type=Path, required=True)
    card.add_argument("--plan")
    card.add_argument("--task")
    for name in ("status", "report", "cancel", "resume", "begin-repair", "verify-repair", "begin-final", "complete", "retest", "apply", "recover-apply", "environment", "target-marker", "run-round"):
        command = sub.add_parser(name)
        command.add_argument("--task", required=True)
        if name == "resume":
            command.add_argument("--interrupted-case", action="append", default=[])
    run = sub.add_parser("run-case")
    run.add_argument("--task", required=True)
    run.add_argument("--case", required=True)
    finish = sub.add_parser("finish-case")
    finish.add_argument("--task", required=True)
    finish.add_argument("--case", required=True)
    finish.add_argument("--token", required=True)
    finish.add_argument("--result", type=Path, required=True)
    target = sub.add_parser("bind-target")
    target.add_argument("--task", required=True)
    target.add_argument("--site-url", required=True)
    target.add_argument("--api-url", required=True)
    install = sub.add_parser("install")
    install.add_argument("--codex-home", type=Path)
    install.add_argument("--register-mcp", action="store_true")
    return root


def dispatch(args):
    from . import cards, inventory, plans, report, runner, state, targets, workspace
    store = Store(args.repository)
    command = args.command
    if command == "inventory":
        return inventory.discover(store.repository)
    if command == "prepare":
        cases = read_json(args.cases) if args.cases else None
        return plans.prepare(store, args.select, cases, args.inventory_digest, args.max_repairs)
    if command == "start":
        return state.start(store, args.plan, args.digest, args.request_id)
    if command == "card":
        return cards.render(store, args.output, args.plan, args.task)
    if command == "status":
        return {"task": store.get("task", args.task), "events": store.events(args.task)}
    if command == "report":
        return report.report(store, args.task)
    if command == "run-case":
        return runner.run_case(store, args.task, args.case)
    if command == "finish-case":
        return runner.finish_case(store, args.task, args.case, args.token, read_json(args.result))
    if command == "run-round":
        task = store.get("task", args.task)
        output = []
        for case_id in task["queue"]:
            latest = store.get("task", args.task)
            if latest["cancel_requested"]:
                break
            if case_id in state.current_results(latest) or case_id in latest["claims"]:
                continue
            try:
                output.append({"case_id": case_id, "result": runner.run_case(store, args.task, case_id)})
            except TestAgentError as error:
                if error.code != "dependencies_pending":
                    raise
                output.append({"case_id": case_id, "status": "awaiting_dependencies"})
        return {"cases": output, "phase": store.get("task", args.task)["phase"]}
    if command == "cancel":
        return state.cancel(store, args.task)
    if command == "resume":
        return state.resume(store, args.task, args.interrupted_case)
    if command == "begin-repair":
        return state.begin_repair(store, args.task)
    if command == "verify-repair":
        return state.verify_repair(store, args.task)
    if command == "begin-final":
        return state.begin_final(store, args.task)
    if command == "complete":
        return state.complete(store, args.task)
    if command == "retest":
        return state.retest(store, args.task)
    if command == "apply":
        return workspace.apply_patch(store, args.task)
    if command == "recover-apply":
        return workspace.recover_application(store, args.task)
    if command == "environment":
        return workspace.dependency_environment(store, args.task)
    if command == "target-marker":
        return targets.marker(store, args.task)
    if command == "bind-target":
        return targets.bind(store, args.task, args.site_url, args.api_url)
    if command == "install":
        from .install import install
        return install(store.repository, args.codex_home, args.register_mcp)
    raise TestAgentError("command_unknown")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        value = dispatch(args)
    except TestAgentError as error:
        print(json.dumps({"ok": False, "code": error.code, "detail": error.detail}, ensure_ascii=False))
        return 2
    except (OSError, ValueError, KeyError) as error:
        # Raw exception messages can contain credentials or business values.
        print(json.dumps({"ok": False, "code": "test_helper_error", "type": type(error).__name__}))
        return 2
    print(json.dumps({"ok": True, "data": value}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
