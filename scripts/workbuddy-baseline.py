"""Capture a credential-free Codex control before/after WorkBuddy changes."""
import hashlib
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
files = ["pyproject.toml", "config/sdks.json", "src/sap_business_agents_platform/codex_planner.py",
         "src/sap_business_agents_platform/workflow_authoring_runtime.py",
         "src/sap_business_agents_platform/runtime_execution.py"]
selection = root / ".local-data/sdk-runtimes/default.json"
if selection.exists():
    files.append(selection.relative_to(root).as_posix())
result = {"schema_version": "1.0", "commit": subprocess.check_output(
    ["git", "-c", f"safe.directory={root.as_posix()}", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
    "python": sys.version, "executable": sys.executable,
    "files": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files},
    "dependencies": {item.metadata["Name"]: item.version for item in importlib.metadata.distributions()}}
destination = root / ".local-data/workbuddy-baseline" / (sys.argv[1] if len(sys.argv) > 1 else "before.json")
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
print(destination)
