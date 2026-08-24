from __future__ import annotations

import argparse
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

try:
    from scripts import direct_sap_read
    from scripts.build_material_shortage_direct_baseline import (
        _date,
        _hash_json,
        _literal,
        _request,
        _run_source,
        _truthy,
    )
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    import direct_sap_read
    from build_material_shortage_direct_baseline import (
        _date,
        _hash_json,
        _literal,
        _request,
        _run_source,
        _truthy,
    )


JsonObject = dict[str, Any]
PRIORITY_RANK = {"unknown": -1, "none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
EXCEPTION_TYPES = {
    "06": "start_date_past",
    "07": "finish_date_past",
    "10": "reschedule_in",
    "15": "reschedule_out",
    "20": "cancel",
    "26": "reduce",
    "30": "schedule_adjusted",
}


def _load(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _strict_decimal(value: Any) -> Decimal | None:
    if value in {None, ""} or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _strict_int(value: Any) -> int | None:
    if value in {None, ""} or isinstance(value, bool):
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


def _date_text(value: Any) -> str | None:
    parsed = _date(value)
    return parsed.isoformat() if parsed else None


def _exception_priority(exception_type: str, open_quantity: Decimal | None) -> str:
    if exception_type == "reschedule_in":
        return "high"
    if exception_type in {"start_date_past", "finish_date_past"}:
        return "high" if open_quantity is not None and open_quantity > 0 else "medium"
    if exception_type in {"reschedule_out", "cancel", "reduce", "other_exception"}:
        return "medium"
    return "low"


def build(case_path: Path, profile_path: Path, output: Path, artifacts: Path) -> JsonObject:
    case = _load(case_path)
    if case.get("schema_version") != "2.0" or case.get("agent_id") != "mrp-exception-analysis":
        raise ValueError("case must be an mrp-exception-analysis v2 case")
    values = case.get("input") if isinstance(case.get("input"), dict) else {}
    expected = {"material", "plant", "mrp_area", "shortage_profile", "shortage_counter"}
    if set(values) != expected:
        raise ValueError("case input has unexpected or missing fields")

    profile = direct_sap_read._load_object(profile_path.resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)
    material = _literal(values["material"])
    plant = _literal(values["plant"])
    mrp_area = _literal(values["mrp_area"])
    shortage_profile = _literal(values["shortage_profile"])
    shortage_counter = _literal(values["shortage_counter"])
    sources: list[JsonObject] = []

    master_manifest, master_rows = _run_source(
        profile,
        _request(
            "mrp_master",
            "API_MRP_MATERIALS_SRV_01",
            "A_MRPMaterial",
            [
                "Material", "MRPArea", "MRPPlant", "MRPController", "MRPType",
                "MaterialProcurementCategory", "PlanningTimeFenceInDays",
                "SafetyStockQuantity", "MaterialPlannedDeliveryDurn",
                "MaterialPlannedProductionDurn", "TotalReplenishmentLeadDuration", "BaseUnit",
            ],
            f"Material eq {material} and MRPPlant eq {plant} and MRPArea eq {mrp_area}",
            ["Material", "MRPPlant", "MRPArea"],
            max_rows=100,
        ),
        artifacts,
    )
    sources.append(master_manifest)

    coverage_manifest, coverage_rows = _run_source(
        profile,
        _request(
            "mrp_coverages",
            "API_MRP_MATERIALS_SRV_01",
            "MaterialCoverages",
            [
                "Material", "MaterialShortageProfile", "MaterialShortageProfileCount",
                "MRPArea", "MRPPlant", "MRPPlanningSegmentNumber", "MRPPlanningSegmentType",
                "MaterialShortageDuration", "MaterialShortageDurnInWorkdays",
                "DaysOfSupplyDuration", "MaterialShortageQuantity", "MaterialShortageStartDate",
                "MaterialShortageEndDate", "MaterialReplnmtLeadDurnEndDate", "TimeHorizonInDays",
                "HasAcceptedShortage", "MaterialBaseUnit",
            ],
            (
                f"Material eq {material} and MRPArea eq {mrp_area} and MRPPlant eq {plant} "
                f"and MaterialShortageProfile eq {shortage_profile} "
                f"and MaterialShortageProfileCount eq {shortage_counter}"
            ),
            [
                "Material", "MaterialShortageProfile", "MaterialShortageProfileCount", "MRPArea",
                "MRPPlanningSegmentNumber", "MRPPlanningSegmentType", "MRPPlant",
            ],
            max_rows=100,
        ),
        artifacts,
        primary=True,
    )
    sources.append(coverage_manifest)

    element_manifest, element_rows = _run_source(
        profile,
        _request(
            "mrp_supply_demand",
            "API_MRP_MATERIALS_SRV_01",
            "SupplyDemandItems",
            [
                "Material", "MRPArea", "MRPPlant", "MaterialShortageProfile",
                "MaterialShortageProfileCount", "MRPElement", "MRPElementItem",
                "MRPElementScheduleLine", "MRPElementCategory", "MRPElementCategoryName",
                "MRPElementCategoryShortName", "MRPElementDocumentType",
                "MRPElementDocumentTypeName", "MRPElementAvailyOrRqmtDate",
                "MRPElementReschedulingDate", "MRPElementOpenQuantity",
                "MRPElementQuantityIsFirm", "MRPElementIsReleased", "MRPAvailableQuantity",
                "MaterialBaseUnit", "ExceptionMessageNumber", "ExceptionMessageText",
                "ExceptionMessageNumber2", "ExceptionMessageText2",
            ],
            (
                f"Material eq {material} and MRPArea eq {mrp_area} and MRPPlant eq {plant} "
                f"and MaterialShortageProfile eq {shortage_profile} "
                f"and MaterialShortageProfileCount eq {shortage_counter}"
            ),
            [
                "Material", "MaterialShortageProfile", "MaterialShortageProfileCount", "MRPArea",
                "MRPPlanningSegment", "MRPPlanningSegmentType", "MRPPlant", "MRPElement",
                "MRPElementItem", "MRPElementScheduleLine",
            ],
        ),
        artifacts,
    )
    sources.append(element_manifest)

    analysis_date = date.today()
    gaps: list[str] = []
    unit_aliases = {"EA": "PCE", "PC": "PCE", "PCE": "PCE", "ST": "PCE"}

    def comparable_unit(value: str) -> str:
        normalized = value.strip().upper()
        return unit_aliases.get(normalized, normalized)

    if len(master_rows) != 1:
        gaps.append("mrp_master_scope_evidence")
    signatures = {
        (
            str(row.get("MaterialShortageQuantity") or ""),
            _date_text(row.get("MaterialShortageStartDate")),
            _date_text(row.get("MaterialShortageEndDate")),
            str(row.get("DaysOfSupplyDuration") or ""),
            _date_text(row.get("MaterialReplnmtLeadDurnEndDate")),
            str(row.get("MaterialBaseUnit") or ""),
        )
        for row in coverage_rows
    }
    if not coverage_rows:
        gaps.append("mrp_coverage_scope_evidence")
    if len(signatures) > 1:
        gaps.append("mrp_coverage_conflict")
    coverage = coverage_rows[0] if coverage_rows else {}
    shortage_quantity = _strict_decimal(coverage.get("MaterialShortageQuantity"))
    shortage_start = _date(coverage.get("MaterialShortageStartDate"))
    shortage_end = _date(coverage.get("MaterialShortageEndDate"))
    days_of_supply = _strict_int(coverage.get("DaysOfSupplyDuration"))
    lead_end = _date(coverage.get("MaterialReplnmtLeadDurnEndDate"))
    coverage_unit = str(coverage.get("MaterialBaseUnit") or "")
    master_unit = str(master_rows[0].get("BaseUnit") or "") if master_rows else ""
    if (
        master_unit
        and coverage_unit
        and comparable_unit(master_unit) != comparable_unit(coverage_unit)
    ):
        gaps.append("mrp_master_coverage_unit_conflict")
    if shortage_quantity is None or shortage_quantity < 0:
        shortage_status, shortage_priority = "unknown", "unknown"
        gaps.append("mrp_shortage_quantity_evidence")
    elif shortage_quantity == 0 and shortage_start is None and shortage_end is None:
        shortage_status, shortage_priority = "none", "none"
    elif shortage_quantity == 0:
        shortage_status, shortage_priority = "unknown", "unknown"
        gaps.append("mrp_shortage_date_conflict")
    elif shortage_start is None:
        shortage_status, shortage_priority = "unknown", "unknown"
        gaps.append("mrp_shortage_start_date_evidence")
    elif shortage_start <= analysis_date or (days_of_supply is not None and days_of_supply <= 0):
        shortage_status, shortage_priority = "active", "critical"
    elif lead_end is None:
        shortage_status, shortage_priority = "unknown", "unknown"
        gaps.append("mrp_replenishment_lead_time_evidence")
    elif shortage_start <= lead_end:
        shortage_status, shortage_priority = "imminent", "high"
    else:
        shortage_status, shortage_priority = "future", "medium"

    records: list[JsonObject] = []
    for row in element_rows:
        open_quantity = _strict_decimal(row.get("MRPElementOpenQuantity"))
        available_quantity = _strict_decimal(row.get("MRPAvailableQuantity"))
        unit = str(row.get("MaterialBaseUnit") or "")
        if (
            unit
            and coverage_unit
            and comparable_unit(unit) != comparable_unit(coverage_unit)
        ):
            gaps.append("mrp_supply_demand_unit_conflict")
        element = str(row.get("MRPElement") or "")
        category = str(row.get("MRPElementCategory") or "")
        if not element and category == "WB":
            element = "_STOCK"
        elif not element:
            element = category
        for number_field, text_field in (
            ("ExceptionMessageNumber", "ExceptionMessageText"),
            ("ExceptionMessageNumber2", "ExceptionMessageText2"),
        ):
            number = str(row.get(number_field) or "")
            message = str(row.get(text_field) or "")
            if not number and not message:
                continue
            exception_type = EXCEPTION_TYPES.get(number, "other_exception")
            records.append(
                {
                    "material": str(row.get("Material") or values["material"]),
                    "plant": str(row.get("MRPPlant") or values["plant"]),
                    "mrp_area": str(row.get("MRPArea") or values["mrp_area"]),
                    "mrp_element": element,
                    "mrp_element_item": str(row.get("MRPElementItem") or ""),
                    "mrp_element_schedule_line": str(row.get("MRPElementScheduleLine") or ""),
                    "exception_number": number,
                    "element_category": category,
                    "requirement_or_receipt_date": _date_text(row.get("MRPElementAvailyOrRqmtDate")) or "",
                    "open_quantity": str(open_quantity) if open_quantity is not None else None,
                    "available_quantity": str(available_quantity) if available_quantity is not None else None,
                    "unit": unit,
                    "exception_type": exception_type,
                    "sap_exception_text": message,
                    "rescheduling_date": _date_text(row.get("MRPElementReschedulingDate")) or "",
                    "priority_level": _exception_priority(exception_type, open_quantity),
                }
            )

    records = list({
        (
            row["material"], row["plant"], row["mrp_area"], row["mrp_element"],
            row["mrp_element_item"], row["mrp_element_schedule_line"],
            row["exception_number"], row["sap_exception_text"],
        ): row
        for row in records
    }.values())
    priorities = [shortage_priority, *(str(row["priority_level"]) for row in records)]
    confirmed = [value for value in priorities if value != "unknown"]
    priority = max(confirmed, key=PRIORITY_RANK.get) if confirmed else "unknown"
    source_complete = all(source.get("source_complete") is True for source in sources)
    evidence_complete = source_complete and not gaps
    business_status = (
        "inconclusive" if not evidence_complete
        else "critical" if priority == "critical"
        else "attention" if priority in {"high", "medium", "low"}
        else "normal" if priority == "none"
        else "inconclusive"
    )
    for row in records:
        row["business_status"] = business_status

    limitations: list[str] = []
    if _strict_int(coverage.get("TimeHorizonInDays")) is not None:
        limitations.append("sap_shortage_time_horizon_applies")
    if _truthy(coverage.get("HasAcceptedShortage")):
        limitations.append("accepted_shortage_not_returned_as_first")
    affected = {
        (row["mrp_element"], row["mrp_element_item"], row["mrp_element_schedule_line"])
        for row in records
    }
    normalized = {
        "records": records,
        "metrics": {
            "shortage_quantity": str(shortage_quantity) if shortage_quantity is not None else None,
            "days_of_supply": days_of_supply,
            "exception_count": len(records),
            "affected_element_count": len(affected),
        },
        "limitations": limitations,
        "source_complete": source_complete,
    }
    qualification = {
        "status": "qualified" if evidence_complete else "blocked",
        "reasons": sorted(set(gaps)),
        "evidence_source_ids": ["mrp_master", "mrp_coverages", "mrp_supply_demand"],
        "evidence_hash": _hash_json({"master": master_rows, "coverage": coverage_rows, "elements": element_rows}),
    }
    baseline = {
        "schema_version": "2.0",
        "runtime": "codex_app_direct_sap",
        "used_sap_business_agents": False,
        "http_methods": ["GET"],
        "qualification": qualification,
        "sources": sources,
        "result_hash": _hash_json(normalized),
        "normalized_result": normalized,
        "diagnostic": {
            "analysis_date": analysis_date.isoformat(),
            "shortage_status": shortage_status,
            "priority_level": priority,
            "evidence_complete": evidence_complete,
        },
    }
    output.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return baseline


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an independent GET-only direct-SAP MRP exception baseline.")
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path.home() / ".codex" / "secure" / "sap-direct-readonly.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    baseline = build(args.case.resolve(), args.profile.resolve(), args.output.resolve(), args.artifacts.resolve())
    print(
        json.dumps(
            {
                "qualification": baseline["qualification"]["status"],
                "source_complete": baseline["normalized_result"]["source_complete"],
                "record_count": len(baseline["normalized_result"]["records"]),
                "result_hash": baseline["result_hash"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if baseline["qualification"]["status"] == "qualified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
