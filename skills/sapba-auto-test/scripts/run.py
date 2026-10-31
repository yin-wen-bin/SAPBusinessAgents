"""Installed skill entrypoint, resolved against the registered repository."""
from pathlib import Path
import json
import sys

skill = Path(__file__).resolve().parents[1]
registration = skill / "repository.json"
repository = Path(json.loads(registration.read_text(encoding="utf-8"))["repository"]) if registration.exists() else skill.parents[1]
sys.path.insert(0, str(repository / "src"))
from sap_business_agents_test_agent.cli import main

raise SystemExit(main(["--repository", str(repository), *sys.argv[1:]]))
