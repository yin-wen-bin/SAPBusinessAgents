from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


ALLOWED = {
    "SAP_BASE_URL": "base_url",
    "SAP_USERNAME": "username",
    "SAP_PASSWORD": "password",
    "SAP_CLIENT": "client",
    "SAP_VERIFY_SSL": "verify_ssl",
    "SAP_ODATA_TIMEOUT_MS": "timeout_ms",
}


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() in ALLOWED:
            values[key.strip()] = value.strip().strip('"').strip("'")
    missing = {"SAP_BASE_URL", "SAP_USERNAME", "SAP_PASSWORD", "SAP_CLIENT"} - set(values)
    if missing:
        raise ValueError(f"source environment is missing {sorted(missing)!r}")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-time migration of a read-only SAP connection into Codex-owned secure storage."
    )
    parser.add_argument("--source-env", type=Path, required=True)
    parser.add_argument(
        "--target",
        type=Path,
        default=Path.home() / ".codex" / "secure" / "sap-direct-readonly.json",
    )
    args = parser.parse_args()
    values = _read_env(args.source_env.resolve())
    target = args.target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {destination: values.get(source, "") for source, destination in ALLOWED.items()}
    payload["read_only"] = True
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    print(json.dumps({"configured": True, "read_only": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
