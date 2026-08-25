from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

OUTPUT_TITLES = {
    "company_code": {"zh": "公司代码", "en": "Company code"},
    "supplier": {"zh": "供应商", "en": "Supplier"},
    "customer": {"zh": "客户", "en": "Customer"},
    "material": {"zh": "物料", "en": "Material"},
    "plant": {"zh": "工厂", "en": "Plant"},
    "purchase_order": {"zh": "采购订单", "en": "Purchase order"},
    "sales_order": {"zh": "销售订单", "en": "Sales order"},
    "rfq": {"zh": "询价单", "en": "RFQ"},
    "purchasing_organization": {"zh": "采购组织", "en": "Purchasing organization"},
    "as_of": {"zh": "查询基准日", "en": "As-of date"},
    "business_status": {"zh": "业务状态", "en": "Business status"},
    "source_complete": {"zh": "查询源完整性", "en": "Query-source completeness"},
    "business_report": {"zh": "结构化业务报告", "en": "Structured business report"},
}

BUSINESS_KEYS = {
    "ap-payment": ["company_code", "fiscal_year", "accounting_document", "accounting_document_item"],
    "ar-collection": ["company_code", "fiscal_year", "accounting_document", "accounting_document_item", "ledger"],
    "gr-ir-clearing": ["purchase_order", "purchase_order_item"],
    "month-end-closing": ["company_code", "fiscal_year", "period"],
    "procure-to-pay-status": ["purchase_order", "purchase_order_item"],
    "material-shortage-procurement-response": ["material", "plant", "requirement_id"],
    "inventory-health-balancing": ["material", "plant", "storage_location", "batch"],
    "intelligent-sourcing-rfq": ["rfq", "rfq_item", "supplier"],
    "supplier-performance-risk": ["purchase_order", "purchase_order_item", "schedule_line"],
    "order-to-cash-status": ["sales_order", "sales_order_item"],
    "billing-block-diagnosis": ["sales_order", "sales_order_item"],
    "billing-completeness-check": ["billing_document", "billing_document_item"],
    "billing-dispute-classification": ["billing_document", "billing_document_item"],
    "billing-output-monitor": ["billing_document", "output_request"],
    "delivered-not-billed": ["delivery_document", "delivery_document_item"],
    "delivery-delay-prediction": ["sales_order", "sales_order_item", "schedule_line"],
    "due-delivery-prioritization": ["sales_order", "sales_order_item", "schedule_line"],
    "order-to-cash-anomaly-monitor": ["sales_order", "sales_order_item"],
    "returns-credit-anomaly": ["customer_return", "customer_return_item"],
    "shortage-allocation-advisor": ["sales_order", "sales_order_item", "schedule_line"],
    "demand-forecast-planning": ["material", "plant", "requirement_date"],
    "mrp-exception-analysis": ["material", "plant", "mrp_element"],
    "production-order-monitoring": ["manufacturing_order", "operation"],
    "production-scheduling-capacity": ["plant", "work_center", "capacity_date"],
    "production-variance-analysis": ["manufacturing_order"],
    "budget-rolling-forecast": ["company_code", "cost_center", "fiscal_year", "period"],
    "co-month-end-allocation-settlement": ["controlling_area", "fiscal_year", "period", "object_id"],
    "cost-center-expense-anomaly": ["controlling_area", "cost_center", "fiscal_year", "period"],
    "internal-order-project-control": ["object_type", "object_id", "fiscal_year", "period"],
    "product-cost-variance": ["manufacturing_order", "fiscal_year", "period"],
}


def _localized_title(name: str, schema: dict[str, Any]) -> dict[str, str]:
    title = schema.get("title")
    if isinstance(title, dict) and title.get("zh") and title.get("en"):
        return {"zh": str(title["zh"]), "en": str(title["en"])}
    known = OUTPUT_TITLES.get(name)
    if known:
        return dict(known)
    words = name.replace("_", " ")
    return {"zh": words, "en": words.title()}


def _odata_refs(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        if all(key in value for key in ("service_name", "odata_version", "entity_set")):
            found.append(
                f"GET {value['service_name']}@{value['odata_version']}/{value['entity_set']}"
            )
        for child in value.values():
            found.extend(_odata_refs(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_odata_refs(child))
    return list(dict.fromkeys(found))


def _workflow_step(step: dict[str, Any]) -> dict[str, Any]:
    step_id = str(step["id"])
    executor = str(step["executor"])
    operation = str(step.get("operation") or "")
    if executor == "sap_read":
        title = {"zh": "读取 SAP 证据", "en": "Read SAP evidence"}
        provider = "Embedded SAP OData Provider"
        kind = "GET-only SAP Provider"
        refs = _odata_refs(step.get("request") or {})
        operations = refs or [f"sap_read.{operation}"]
        description = {
            "zh": f"执行清单步骤 `{step_id}`，通过实时 Schema 校验后的 GET 请求读取证据。",
            "en": f"Run manifest step `{step_id}` using GET requests validated against live schema.",
        }
    elif executor == "skill":
        title = {"zh": "按条件补充表级证据", "en": "Conditionally supplement table evidence"}
        provider = str(step.get("skillId") or "SAPSkillhub skill")
        kind = "Read-only SAPSkillhub Skill"
        operations = [f"{provider}.{operation}"]
        description = {
            "zh": f"仅在清单条件成立时执行 `{step_id}`；条件为假时记录 skipped，不连接 Skill。",
            "en": f"Run `{step_id}` only when its manifest condition is true; otherwise record skipped without calling the Skill.",
        }
    else:
        is_assessment = operation.startswith("assess")
        title = (
            {"zh": "评估证据完整性", "en": "Assess evidence completeness"}
            if is_assessment
            else {"zh": "执行确定性业务规则", "en": "Run deterministic business rules"}
        )
        provider = operation
        kind = "Local deterministic rule"
        operations = [f"rule.{operation}"]
        description = {
            "zh": f"执行清单步骤 `{step_id}`，只解释已经取得的证据，不访问或修改 SAP。",
            "en": f"Run manifest step `{step_id}` and interpret collected evidence without accessing or changing SAP.",
        }
    return {
        "id": f"execute-{step_id}",
        "title": title,
        "description": description,
        "operations": {"zh": operations, "en": operations},
        "tools": [
            {
                "name": provider,
                "kind": kind,
                "purpose": {
                    "zh": f"执行 `{step_id}` / `{operation}`",
                    "en": f"Execute `{step_id}` / `{operation}`",
                },
            }
        ],
        "executionStepIds": [step_id],
    }


def _executed_skill_objects(steps: list[dict[str, Any]]) -> list[str]:
    objects: list[str] = []
    for step in steps:
        if step.get("executor") != "skill":
            continue
        mapping = step.get("inputMapping")
        object_name = mapping.get("object") if isinstance(mapping, dict) else None
        if isinstance(object_name, str) and object_name and "{{" not in object_name:
            objects.append(object_name)
    return list(dict.fromkeys(objects))


def migrate(path: Path) -> None:
    agent = json.loads(path.read_text(encoding="utf-8"))
    execution = agent["execution"]
    input_schema = execution["inputSchema"]
    input_properties = input_schema["properties"]

    agent["systems"] = ["SAP S/4HANA"]
    # Fixed Agents never execute SAP GUI transactions. Keep the public scope
    # derived from the executable manifest instead of legacy documentation.
    agent["transactions"] = []
    agent["tables"] = _executed_skill_objects(execution["steps"])

    input_titles = {
        name: _localized_title(name, schema)
        for name, schema in input_properties.items()
    }
    for name, schema in input_properties.items():
        schema["title"] = input_titles[name]
    agent["inputs"] = {
        locale: [input_titles[name][locale] for name in input_properties]
        for locale in ("zh", "en")
    }

    output_schema = execution.get("outputSchema")
    if not isinstance(output_schema, dict):
        properties: dict[str, Any] = {
            name: {**schema, "title": input_titles[name]}
            for name, schema in input_properties.items()
        }
        properties.update(
            {
                "business_status": {"type": "string", "title": OUTPUT_TITLES["business_status"]},
                "source_complete": {"type": "boolean", "title": OUTPUT_TITLES["source_complete"]},
                "business_report": {"type": "object", "title": OUTPUT_TITLES["business_report"]},
            }
        )
        output_schema = {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }
        execution["outputSchema"] = output_schema
        rule_steps = [step for step in execution["steps"] if step.get("executor") == "rule"]
        if not rule_steps:
            raise ValueError(f"{agent['slug']} has no rule step for output mapping")
        final_step = rule_steps[-1]["id"]
        execution["outputMapping"] = {
            name: f"{{{{steps.{final_step}.output.workflow_output.{name}}}}}"
            for name in properties
        }
    else:
        for name, schema in output_schema.get("properties", {}).items():
            schema.setdefault("title", _localized_title(name, schema))

    output_properties = execution["outputSchema"]["properties"]
    output_titles = {
        name: _localized_title(name, schema)
        for name, schema in output_properties.items()
    }
    agent["outputs"] = {
        locale: [output_titles[name][locale] for name in output_properties]
        for locale in ("zh", "en")
    }

    agent["workflow"] = [_workflow_step(step) for step in execution["steps"]]
    existing_acceptance = execution.get("acceptance")
    if not isinstance(existing_acceptance, dict):
        execution["acceptance"] = {
            "comparisonMode": "business_semantic",
            "businessKeys": BUSINESS_KEYS[agent["slug"]],
            "facts": ["business_status"],
            "metrics": [],
            "currencyAndUnitPolicy": "compare_only_when_same_or_conversion_validated",
            "requiredLimitations": ["source_completeness_not_overstated"],
        }
    if agent["slug"] == "ap-payment" and not existing_acceptance:
        execution["acceptance"].update(
            {
                "facts": [
                    "posting_date",
                    "debit_credit",
                    "as_of_status",
                    "clearing_document",
                    "payment_evidence_status",
                ],
                "metrics": ["open_items"],
                "decimalFields": ["amount"],
                "currencyFields": ["currency"],
                "requiredLimitations": ["bank_settlement_not_proven"],
            }
        )
    path.write_text(json.dumps(agent, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    paths = sorted(ROOT.glob("agents/*/*/agent.json"))
    if len(paths) != 30:
        raise RuntimeError(f"expected 30 Agent manifests, found {len(paths)}")
    missing = sorted({json.loads(path.read_text(encoding="utf-8"))["slug"] for path in paths} - BUSINESS_KEYS.keys())
    if missing:
        raise RuntimeError(f"missing business keys for: {', '.join(missing)}")
    for path in paths:
        migrate(path)
    print(f"Migrated {len(paths)} Agent page and acceptance contracts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
