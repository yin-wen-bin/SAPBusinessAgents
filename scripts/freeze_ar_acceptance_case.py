"""Create a new dated case from selected inputs; never rewrite old cases."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from sap_business_agents_platform.acceptance import CanonicalTestCase, canonical_hash


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--business-date", default=date.today().isoformat())
    args = parser.parse_args()
    day = date.fromisoformat(args.business_date).isoformat()
    value = CanonicalTestCase.from_dict(json.loads(args.source.read_text(encoding="utf-8"))).as_dict()
    if value["agent_id"] != "ar-collection":
        raise ValueError("this helper freezes current AR collection cases only")
    value["case_id"] = value["case_id"] + "-" + day
    for scope in ("input", "business_conditions"):
        value[scope].update({"as_of": day, "business_date": day})
    CanonicalTestCase.from_dict(value)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"case_id": value["case_id"], "case_hash": canonical_hash(value),
                      "business_date": day}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
