from __future__ import annotations

import json
import os
import sys
import tomllib
from pathlib import Path

from .common import TestAgentError, atomic_bytes, digest, read_json, safe_path, write_json


def install(repository, codex_home=None, register_mcp=False):
    repository = Path(repository).resolve()
    home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve()
    source = repository / "skills/sapba-auto-test"
    if not (source / "SKILL.md").is_file():
        raise TestAgentError("skill_source_missing")
    destination = safe_path(home, "skills/sapba-auto-test")
    receipt_file = destination / "installation.json"
    previous = read_json(receipt_file).get("files", {}) if receipt_file.exists() else {}
    files = {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    for name, data in files.items():
        target = safe_path(destination, name)
        if target.exists() and digest(target.read_bytes()) not in {previous.get(name), digest(data)}:
            raise TestAgentError("skill_install_conflict", path=name)
    executable = repository / ".venv/Scripts/python.exe"
    if not executable.exists():
        executable = Path(sys.executable)
    registration = {"command": str(executable), "args": ["-m", "sap_business_agents_test_agent.mcp", "--repository", str(repository)],
                    "env": {"PYTHONPATH": str(repository / "src"), "PYTHONUTF8": "1"}}
    configuration = home / "config.toml"
    configuration_bytes = configuration.read_bytes() if configuration.exists() else b""
    configuration_text = configuration_bytes.decode("utf-8")
    configuration_hash = digest(configuration_bytes)
    old = tomllib.loads(configuration_text).get("mcp_servers", {}).get("sapba_auto_test")
    if register_mcp and old is not None and old != registration:
        raise TestAgentError("mcp_registration_conflict")
    for name, data in files.items():
        atomic_bytes(safe_path(destination, name), data)
    write_json(receipt_file, {"repository": str(repository), "files": {name: digest(data) for name, data in files.items()}})
    write_json(destination / "repository.json", {"repository": str(repository)})
    if register_mcp and old is None:
        # Preserve all existing settings, comments, plugins and MCP registrations verbatim.
        block = "\n[mcp_servers.sapba_auto_test]\ncommand = " + json.dumps(registration["command"]) + "\nargs = " + json.dumps(registration["args"]) + "\n"
        block += "\n[mcp_servers.sapba_auto_test.env]\n" + "\n".join(
            key + " = " + json.dumps(value) for key, value in registration["env"].items()) + "\n"
        combined = configuration_text.rstrip() + "\n" + block
        tomllib.loads(combined)
        if configuration.exists() and digest(configuration.read_bytes()) != configuration_hash:
            raise TestAgentError("mcp_configuration_changed")
        atomic_bytes(configuration, combined.encode("utf-8"))
    return {"skill_path": str(destination), "mcp_registered": register_mcp,
            "mcp_configuration": registration, "reload_required": True,
            "note": "Start a new Codex chat/reload tool configuration to discover the new skill and MCP tools."}
