"""Discover bounded company customers; freeze 3/50-customer canonical cases.

No SAP values are printed; discovery is not accepted as complete AR evidence.
"""
import argparse
import copy
import json
from pathlib import Path

from scripts.direct_sap_read import run, read_encrypted_rows
from scripts.build_ar_collection_direct_baseline import _request, _literal


def discover(template_path, profile_path, output, discover_company=False, discovery_snapshot=None):
    if output.exists():
        raise ValueError("discovery_immutable")
    template = json.loads(template_path.read_text(encoding="utf-8"))
    company = template["input"]["company_code"]
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    request = _request("ar_capacity_customers", "API_BUSINESS_PARTNER", "A_CustomerCompany",
                       ["Customer", "CompanyCode"], "CompanyCode ne ''" if discover_company else "CompanyCode eq " + _literal(company),
                       ["CompanyCode", "Customer"], max_rows=5000 if discover_company else 100)
    if discovery_snapshot is None:
        run(profile, request, output / "discovery", encrypt_rows=True)
        discovery_snapshot = output / "discovery"
    else:
        from scripts.direct_sap_read import _hash_json
        manifest = json.loads((discovery_snapshot / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("query_hash") != _hash_json({k: v for k, v in request.items() if k != "source_id"}):
            raise ValueError("discovery_snapshot_scope_mismatch")
    discovered = read_encrypted_rows(discovery_snapshot)
    if discover_company:
        from collections import Counter
        counts = Counter(row.get("CompanyCode") for row in discovered if row.get("Customer"))
        if not counts:
            raise ValueError("test_data_gap_fifty_customers")
        company = sorted(counts, key=lambda key: (-counts[key], key))[0]
    seed = template["input"]["customers"] if company == template["input"]["company_code"] else []
    customers = list(dict.fromkeys([*seed, *[
        str(row["Customer"]).strip() for row in discovered
        if row.get("CompanyCode") == company and row.get("Customer")]]))
    sizes = [size for size in (3, 50) if len(customers) >= size]
    for count in sizes:
        case = copy.deepcopy(template)
        case["case_id"] = f"ar-current-{count}-customers-" + template["input"]["business_date"]
        case["input"]["customers"] = customers[:count]
        case["input"]["company_code"] = company
        # Conditions carry business scope and must be updated alongside input.
        conditions = case.get("business_conditions", {})
        if not isinstance(conditions, dict):
            raise ValueError("canonical_business_conditions_invalid")
        conditions.update({"company_code": company, "customer_count": count, "sample_source": "live_discovery"})
        case["question"] = {
            "zh": f"核对全部{count}个客户当前应收账龄和催收状态，保留每个客户的结果。",
            "en": f"Compare current AR aging and dunning for all {count} requested customers; retain per-customer results."
        }
        from sap_business_agents_platform.acceptance import CanonicalTestCase
        CanonicalTestCase.from_dict(case)
        path = output / str(count)
        path.mkdir(parents=True)
        with (path / "case.json").open("x", encoding="utf-8") as stream:
            json.dump(case, stream, ensure_ascii=False, indent=2)
    return {"discovered_candidate_count": len(customers), "frozen_case_sizes": sizes,
            "gap_code": None if 50 in sizes else "test_data_gap_fifty_customers"}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--template", type=Path, required=True)
    p.add_argument("--profile", type=Path, default=Path.home() / ".codex/secure/sap-direct-readonly.json")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--discover-company", action="store_true")
    p.add_argument("--discovery-snapshot", type=Path)
    a = p.parse_args()
    print(json.dumps(discover(a.template, a.profile, a.output, a.discover_company, a.discovery_snapshot)))
