"""Local CI checks; never installs, pushes, or runs live model/SAP acceptance."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("all", "python", "site", "docs"), default="all")
    parser.add_argument("--require-no-workbuddy", action="store_true",
                        help="Assert the clean CI platform has no WorkBuddy SDK installed.")
    args = parser.parse_args()
    if args.require_no_workbuddy and importlib.util.find_spec("codebuddy_agent_sdk") is not None:
        parser.error("Clean platform verification requires an environment without WorkBuddy SDK.")
    commands: list[tuple[list[str], Path]] = []
    if args.scope in {"all", "docs"}:
        commands.append(([sys.executable, "scripts/documentation.py", "--check"], ROOT))
    if args.scope in {"all", "python"}:
        if importlib.util.find_spec("pip_audit") is None:
            parser.error('pip-audit is missing; install the project with .[dev] in your validation environment.')
        commands.extend([
            ([sys.executable, "-m", "pytest", "-q"], ROOT),
            ([sys.executable, "-m", "pip_audit", "--local", "--skip-editable", "--progress-spinner", "off"], ROOT),
        ])
    if args.scope in {"all", "site"}:
        npm = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
        if not npm:
            parser.error("npm is missing; use the Node version specified by site/package.json.")
        commands.append(([npm, "run", "verify"], ROOT / "site"))
    for command, directory in commands:
        print(f"[{directory.name}] {subprocess.list2cmdline(command)}", flush=True)
        result = subprocess.run(command, cwd=directory)
        if result.returncode:
            print(f"Validation failed (exit {result.returncode}); no later steps were run.", file=sys.stderr)
            return result.returncode
    print("Requested validation scope passed. No commit, push or deployment was performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
