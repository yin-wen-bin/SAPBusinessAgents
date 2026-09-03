from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = ("sapclaw_runtime", "Thin Runtime", "Thin SAPClaw")
ACTIVE_ROOTS = (
    ROOT / "agents",
    ROOT / "config",
    ROOT / "src",
    ROOT / "site" / "src",
    ROOT / "workflows",
)
ACTIVE_SUFFIXES = {".json", ".py", ".ts", ".tsx", ".astro", ".css", ".toml", ".yaml", ".yml"}
HISTORICAL_NAMES = {
    "three-stage-live-acceptance.md",
    "live-sap-test-report.md",
    "multi-po-runtime-live-acceptance.md",
    "p2p-evidence-workflow-live-acceptance.md",
}


def active_violations() -> list[str]:
    violations: list[str] = []
    for root in ACTIVE_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in ACTIVE_SUFFIXES:
                continue
            if path.name in HISTORICAL_NAMES or "generated" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for token in FORBIDDEN:
                if token in text:
                    violations.append(f"{path.relative_to(ROOT)}: forbidden active runtime reference {token!r}")
    return sorted(violations)


def main() -> int:
    violations = active_violations()
    if violations:
        print("\n".join(violations))
        return 1
    print("No retired Thin SAPClaw runtime references were found in active contracts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
