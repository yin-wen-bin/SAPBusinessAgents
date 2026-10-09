"""Offline verification assertions, not a new business policy or execution path.

The limited sample proves honest incomplete reporting. Only a separate complete
unique-key case may qualify free_query. Neither case rewrites a platform run.
"""
from __future__ import annotations

from sap_business_agents_platform.runtime_query_contract import plans

SERVICE = "API_PURCHASEORDER_PROCESS_SRV"
ENTITY = "A_PurchaseOrder"
FIELDS = {"PurchaseOrder", "CompanyCode", "Supplier"}
CASES = {"limited_sample", "complete_unique_key"}


class VerificationFailure(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def frozen_runtime_binding(value):
    """Use the same typed projection as saved runs; retain all Provider bindings."""
    from sap_business_agents_platform.models import RuntimeSnapshot
    return RuntimeSnapshot.model_validate(value).model_dump(mode="json")


def query(case, purchase_order):
    if case not in CASES:
        raise VerificationFailure("verification_case_unknown")
    range_text = "明确 top=1，并如实说明有限样本不代表完整来源" if case == "limited_sample" else (
        "先读取本次实时 Schema，确认采购订单号是完整唯一键；按该键精确过滤，"
        "不设置显式 top 截断，保留平台读取、分页及时间限制")
    return (f"只读查询采购订单{purchase_order}的抬头，显示采购订单号、公司代码和供应商；"
            f"{range_text}。不扩展到收货、付款、库存、其它订单或邮件。")


def checked_plan(plan, *, case, purchase_order, schema_results=()):
    if case not in CASES:
        raise VerificationFailure("verification_case_unknown")
    steps = plans(plan)
    if len(steps) != 1:
        raise VerificationFailure("verification_read_scope_rejected")
    step = steps[0]
    filters = step.get("filters") or []
    if (step.get("service_name") != SERVICE or step.get("odata_version") != "2.0"
            or step.get("entity_set") != ENTITY or set(step.get("select_fields") or []) != FIELDS
            or step.get("plan_kind") not in {None, "direct"}
            or any(step.get(key) for key in ("filter_from_previous", "function_parameters", "tuple_filters", "partition"))
            or len(filters) != 1 or filters[0].get("field") != "PurchaseOrder"
            or filters[0].get("operator", "eq") != "eq" or filters[0].get("value") != purchase_order):
        raise VerificationFailure("verification_read_scope_rejected")
    if case == "limited_sample":
        if step.get("top") != 1:
            raise VerificationFailure("verification_read_scope_rejected")
        return step
    if step.get("top") is not None:
        raise VerificationFailure("verification_read_scope_rejected")
    # Only the current run's successfully returned authoritative metadata counts.
    for response in schema_results:
        data = response.get("data") or {}
        if (response.get("ok") is not True or data.get("schema_authority") is not True
                or data.get("fields_truncated") is not False
                or data.get("compatibility_status") != "compatible" or not data.get("metadata_timestamp")):
            continue
        for entity in data.get("entities") or []:
            if (entity.get("service_name") == SERVICE and entity.get("odata_version") == "2.0"
                    and entity.get("entity_set") == ENTITY and entity.get("key_fields") == ["PurchaseOrder"]
                    and entity.get("runtime_available") is True):
                return step
    raise VerificationFailure("verification_live_unique_key_required")


def checked_result(record, calls, *, case):
    if case not in CASES:
        raise VerificationFailure("verification_case_unknown")
    result = record.result
    expected = "inconclusive" if case == "limited_sample" else "completed"
    if str(record.status) != expected or result is None:
        raise VerificationFailure("free_query_platform_not_completed")
    if (not result.presentation or not result.presentation.validation_ref or not result.artifacts
            or "harness_runtime_unavailable" in result.completeness.missing_evidence
            or not any(c.get("tool_name") == "sap_query_execute" and c.get("status") == "completed" for c in calls)
            or not any(c.get("tool_name") == "sap_final_report_validate" and c.get("status") == "completed"
                       and (c.get("output") or {}).get("ok") is True for c in calls)):
        raise VerificationFailure("verification_report_or_evidence_invalid")
    complete = result.completeness.source_complete
    if case == "limited_sample" and complete is not False:
        raise VerificationFailure("verification_limited_sample_claimed_complete")
    if case == "complete_unique_key" and (complete is not True or result.completeness.business_complete is not True
                                        or result.completeness.missing_evidence):
        raise VerificationFailure("verification_complete_source_required")
    return {"case": case, "platform_status": str(record.status), "report_evidence_validated": True,
            "source_complete": complete, "business_complete": result.completeness.business_complete,
            "qualifies_operation": case == "complete_unique_key"}


def failed_attempt(error, record):
    """Qualification failure is separate from the run; no Store mutation."""
    code = getattr(error, "code", "verification_failed")
    if not isinstance(code, str) or not code.replace("_", "").isalnum() or len(code) > 120:
        code = "verification_failed"
    return {"phase": "failed", "failure_code": code, "platform_status": str(record.status),
            "platform_status_overridden": False}
