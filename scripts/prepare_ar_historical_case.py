"""Freeze one ignored historical AR case from an approved Skill gate input."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from sap_business_agents_platform.acceptance import CanonicalTestCase, canonical_hash


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-input", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--business-date", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("historical_case_immutable")
    skill_input = json.loads(args.skill_input.read_text(encoding="utf-8"))
    value = json.loads(args.template.read_text(encoding="utf-8"))
    customers = [str(item).strip() for item in skill_input.get("customers") or []]
    if len(customers) != 1 or not customers[0] or args.as_of >= args.business_date:
        raise ValueError("historical_case_input_invalid")
    value.update(
        case_id="ar-historical-dunning-clearing-" + args.as_of.isoformat(),
        question={
            "zh": "按历史截止日重建该客户应收、清账冲销时间线及已执行催收事件。",
            "en": "Reconstruct this customer's receivables, clearing-reversal timeline, and executed dunning events at the historical cutoff.",
        },
    )
    value["input"].update(
        company_code=skill_input["company_code"],
        customers=customers,
        as_of=args.as_of.isoformat(),
        business_date=args.business_date.isoformat(),
    )
    value["business_conditions"] = {
        "scope": "historical",
        "sample_source": "independent_dunning_gate",
        "as_of": args.as_of.isoformat(),
        "business_date": args.business_date.isoformat(),
        "company_code": skill_input["company_code"],
        "customer_count": 1,
    }
    value["expected_output"]["minimum_primary_evidence_rows"] = 1
    value["expected_output"]["allow_empty_result"] = False
    CanonicalTestCase.from_dict(value)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"case_id": value["case_id"], "case_hash": canonical_hash(value)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
