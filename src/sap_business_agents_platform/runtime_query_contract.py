"""Shared admission over the existing Provider contract (no SAP I/O)."""
from __future__ import annotations

import copy
import json
import re
from itertools import product

from jsonschema import Draft202012Validator

from .runtime_contract import RuntimeContractError


def obj(properties, required=()):
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(required)}


IDENTIFIER = {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]*$"}
SERVICE = {"type": "string", "pattern": "^[A-Za-z0-9_]+(?:;v=[0-9]+)?$"}
FIELDS = {"type": "array", "items": IDENTIFIER, "uniqueItems": True}
FILTER = obj({"field": IDENTIFIER, "operator": {"enum": ["eq", "ne", "gt", "ge", "lt", "le", "contains", "in"]},
              "value": {}, "value_type": {"type": "string"}, "chunk_size": {"type": "integer", "minimum": 1, "maximum": 20}},
             ("field", "value"))
BINDING = obj({"field": IDENTIFIER, "source_step_id": {"type": "string"}, "source_field": IDENTIFIER,
               "fanout": {"type": "boolean"}, "fetch_all_for_binding": {"type": "boolean"}},
              ("field", "source_step_id", "source_field"))
PROPERTIES = {
    "schema_version": {"const": "1.0"},
    "service_name": SERVICE, "odata_version": {"enum": ["2.0", "4.0"]}, "entity_set": IDENTIFIER,
    "http_method": {"const": "GET"}, "httpMethod": {"const": "GET"}, "method": {"const": "GET"}, "step_id": {"type": "string"},
    "plan_kind": {"enum": ["direct", "lookup", "multi_step", "function", "function_import"]},
    "filters": {"type": "array", "items": FILTER}, "select_fields": FIELDS,
    "response_summary_fields": FIELDS, "order_by": FIELDS,
    "top": {"type": ["integer", "null"], "minimum": 1},
    "filter_from_previous": {"type": "array", "items": BINDING},
    "function_parameters": {"type": "array", "items": obj({"name": IDENTIFIER, "value": {}}, ("name", "value"))},
    "tuple_filters": {"type": "array", "maxItems": 1, "items": obj({"fields": FIELDS,
        "values": {"type": "array", "items": {"type": "array"}, "minItems": 1},
        "chunk_size": {"type": "integer", "minimum": 1, "maximum": 20}}, ("fields", "values"))},
    "partition": obj({"field": IDENTIFIER, "strategy": {"const": "adaptive_date"},
        "from": {"type": "string"}, "to": {"type": "string"},
        "maxPartitions": {"type": "integer"}, "maxTotalResults": {"type": "integer"}}, ("field", "strategy", "from", "to")),
    "max_concurrent_chunks": {"type": "integer", "minimum": 1, "maximum": 8},
    "chunk_error_policy": {"enum": ["fail", "record_gap"]},
    "retry_failed_chunks_individually": {"type": "boolean"},
    "rationale": {"type": "string"}, "response_directive": {"type": "string"},
    "output_contract": obj({"mode": {"enum": ["explicit", "inferred"]}, "display_grain": {"type": "string"},
        "requested_fields": FIELDS, "display_fields": FIELDS, "support_fields": FIELDS,
        "reason": {"type": "string"}}),
}
PLAN_SCHEMA = obj({**PROPERTIES, "steps": {"type": "array", "minItems": 1,
    "items": obj(PROPERTIES, ("entity_set",))}})
PLAN_HELP = (
    "Canonical GET plan members are filters:[{field,operator,value}], select_fields, response_summary_fields, "
    "order_by (bare ascending field names), top and http_method. Never use filter, keys, select, skip or raw URLs. "
    "Direct plans require service_name, odata_version and entity_set. multi_step/lookup use steps with step_id, "
    "entity_set and filter_from_previous:[{field,source_step_id,source_field}]. Preserve confirmed constants and limits.\n"
    + json.dumps({"example": {"service_name": "API_PURCHASEORDER_PROCESS_SRV", "odata_version": "2.0",
        "entity_set": "A_PurchaseOrder", "http_method": "GET", "filters": [{"field": "PurchaseOrder",
        "operator": "eq", "value": "<confirmed purchase order>"}], "select_fields": ["PurchaseOrder"], "top": 1},
        "plan_schema": PLAN_SCHEMA})
)


def reject(code, path="/", constraint="scope"):
    error = RuntimeContractError(code)
    error.detail = {"validation_issues": [{"code": code, "path": path, "constraint": constraint}]}
    raise error


def plans(value):
    """Accept the legacy harness envelope, not invented Provider aliases."""
    if isinstance(value, dict) and value.get("kind") == "sap_business_agents_harness":
        if not isinstance(value.get("steps"), list) or not value["steps"]:
            reject("runtime_plan_contract_invalid", "/steps", "required")
        result = []
        for step in value["steps"]:
            if isinstance(step, dict) and step.get("tool") == "skill":
                if not step.get("skill_id") or not isinstance(step.get("input"), dict):
                    reject("runtime_plan_contract_invalid", "/steps", "skill_input")
                continue  # Admission is still owned by the approved Skill service.
            if not isinstance(step, dict) or step.get("tool") != "sap_read":
                reject("runtime_plan_contract_invalid", "/steps", "unsupported_tool")
            result.extend(plans(step.get("plan")))
        return result
    if not isinstance(value, dict):
        reject("runtime_plan_contract_invalid", constraint="type")
    for error in Draft202012Validator(PLAN_SCHEMA).iter_errors(value):
        # Schema-owned names only; never expose model-supplied unknown keys/values.
        path = "/" + "/".join(str(p) for p in error.absolute_path if isinstance(p, int) or p in PROPERTIES or p == "steps")
        reject("runtime_plan_contract_invalid", path, str(error.validator))
    nested = value.get("steps")
    if nested is not None and value.get("plan_kind") not in {"lookup", "multi_step"}:
        reject("runtime_plan_contract_invalid", "/steps", "plan_kind")
    if value.get("plan_kind") in {"lookup", "multi_step"} and not nested:
        reject("runtime_plan_contract_invalid", "/steps", "required")
    result = []
    for step in nested or [value]:
        step = {**{k: value[k] for k in ("service_name", "odata_version") if k in value}, **step}
        if not all(step.get(k) for k in ("service_name", "odata_version", "entity_set")):
            reject("runtime_plan_contract_invalid", constraint="required")
        result.append(step)
    return result


def preserve_grounding(before, after, *, schemas=None):
    if isinstance(before, dict) and before.get("kind") == "sap_business_agents_harness":
        if not isinstance(after, dict) or after.get("kind") != before["kind"]:
            reject("runtime_grounding_scope_changed", "/kind")
        old_steps, new_steps = before.get("steps") or [], after.get("steps") or []
        if len(old_steps) != len(new_steps):
            reject("runtime_grounding_scope_changed", "/steps")
        for left, right in zip(old_steps, new_steps):
            if not isinstance(left, dict) or not isinstance(right, dict) or any(left.get(key) != right.get(key) for key in ("id", "tool")):
                reject("runtime_grounding_scope_changed", "/steps")
            if left.get("tool") == "skill" and any(left.get(key) != right.get(key) for key in ("skill_id", "input")):
                reject("runtime_grounding_scope_changed", "/steps/input")
    def canonical_filters(items):
        result = []
        for item in items or []:
            item = copy.deepcopy(item)
            item.setdefault("operator", "eq")
            if item.get("value_type") is None or isinstance(item.get("value"), str) and item.get("value_type") in {"string", "Edm.String"}:
                item.pop("value_type", None)
            result.append(json.dumps(item, sort_keys=True))
        return sorted(result)
    old, new = plans(before), plans(after)
    if len(old) != len(new):
        reject("runtime_grounding_scope_changed", "/steps")
    for left, right in zip(old, new):
        for name in ("service_name", "odata_version", "entity_set", "filters", "tuple_filters",
                     "function_parameters", "filter_from_previous", "partition"):
            same = canonical_filters(left.get(name)) == canonical_filters(right.get(name)) if name == "filters" else left.get(name) == right.get(name)
            if not same:
                reject("runtime_grounding_scope_changed", "/" + name)
        confirmed = None
        for response in schemas or []:
            data = response.get("data") or {}
            service = data.get("service") or {}
            fields = data.get("fields") or []
            matching = [item for item in fields if isinstance(item, dict) and
                (item.get("service_name") or service.get("service_name"),
                 item.get("odata_version") or service.get("odata_version"), item.get("entity_set")) ==
                tuple(left.get(key) for key in ("service_name", "odata_version", "entity_set"))]
            if (response.get("ok") is True and matching and data.get("schema_authority") is True
                    and data.get("fields_truncated") is False):
                confirmed = (confirmed or set()) | {item.get("field_name") for item in matching}
        for name in ("select_fields", "response_summary_fields"):
            required = set(left.get(name) or [])
            # Grounding may remove an optional field proven absent in metadata,
            # but never a confirmed field. Lack of metadata is not proof of absence.
            if confirmed is not None:
                required &= confirmed
            if not required.issubset(right.get(name) or []):
                reject("runtime_grounding_scope_changed", "/" + name)
        if left.get("top") is not None and (right.get("top") is None or right["top"] > left["top"]):
            reject("runtime_grounding_scope_changed", "/top")


# Public input spelling -> actual business field. Unknown supplied parameters
# stay unresolved; the model cannot decide to discard them.
ALIASES = {
    "purchase_order": ("PurchaseOrder", "PurchasingDocument"),
    "sales_order": ("SalesOrder", "SalesDocument"), "material": ("Material",),
    "plant": ("Plant",), "company_code": ("CompanyCode",), "supplier": ("Supplier",),
    "purchasing_organization": ("PurchasingOrganization",), "purchasing_group": ("PurchasingGroup",),
    "schedule_line_delivery_date": ("ScheduleLineDeliveryDate",), "cost_center": ("CostCenter",),
    "controlling_area": ("ControllingArea",), "fiscal_year": ("FiscalYear",),
}
LABELS = {"purchase_order": r"采购订单(?:号)?|purchase\s+order|\bPO\b",
          "sales_order": r"销售订单(?:号)?|sales\s+order",
          "material": r"物料(?:号)?|material", "plant": r"工厂|plant",
          "company_code": r"公司代码|company\s+code", "supplier": r"供应商|supplier"}


class ReadScope:
    """Frozen explicit scope; bounded joins derive only from scoped evidence."""
    def __init__(self, query, supplied=None):
        inputs = copy.deepcopy(supplied or {})
        # Acceptance adds platform-owned public input JSON on this exact line.
        for marker in ("Confirmed public inputs: ", "Canonical query inputs: "):
            if marker in query:
                try:
                    value, _ = json.JSONDecoder().raw_decode(query.split(marker, 1)[1])
                    if isinstance(value, dict):
                        inputs.update(value)
                except ValueError:
                    self.unresolved = True
                    reject("runtime_scope_input_invalid")
        for name, label in LABELS.items():
            match = re.search(r"(?:" + label + r")[\s:=：\"']*([A-Za-z0-9][A-Za-z0-9_-]*[0-9][A-Za-z0-9_-]*)", query, re.I)
            if match:
                inputs.setdefault(name, match[1])
        self.constraints, self.unresolved = [], False
        for name, value in inputs.items():
            if value is None or value == "" or value == []:
                continue
            fields = ALIASES.get(name) or ((name,) if re.fullmatch(r"[A-Z][A-Za-z0-9]*", name) else ())
            if not fields or not isinstance(value, (str, int, float, list)) or isinstance(value, bool):
                self.unresolved = True
            else:
                self.constraints.append((fields, [str(v) for v in value] if isinstance(value, list) else [str(value)]))
        self.derived_rows = []
        self.schemas = {}
        self.normalizer = None

    def allowed_values(self, names, values, step):
        allowed = set(values)
        normalizer = self.normalizer
        if normalizer is not None and callable(getattr(normalizer, "field_rule", None)):
            for name in names:
                rule = normalizer.field_rule(*(step.get(k, "") for k in ("service_name", "odata_version", "entity_set")), name)
                allowed.update(str(normalizer.normalize_structured_value(value, rule=rule)) for value in values)
        return allowed

    def record_schema(self, response):
        if response.get("ok") is not True:
            return
        data = response.get("data") or {}
        service = data.get("service") or {}
        for field in data.get("fields") or []:
            key = (field.get("service_name") or service.get("service_name"),
                   field.get("odata_version") or service.get("odata_version"), field.get("entity_set"))
            self.schemas.setdefault(key, set()).add(field.get("field_name"))

    def check(self, plan):
        candidates = plans(plan)
        if self.unresolved:
            reject("runtime_scope_clarification_required")
        if not self.constraints:
            return  # Unconstrained questions still use Provider/budget bounds.
        scoped_steps = {}
        for step in candidates:
            fields = self.schemas.get(tuple(step.get(k) for k in ("service_name", "odata_version", "entity_set")))
            filters = step.get("filters") or []
            exact = {f["field"]: ([str(v) for v in f["value"]] if isinstance(f["value"], list) else [str(f["value"])])
                     for f in filters if f.get("operator", "eq") in {"eq", "in"}}
            applicable = [(names, self.allowed_values(names, values, step)) for names, values in self.constraints
                          if fields is None or set(names).intersection(fields)]
            if applicable and all(any(name in exact and set(exact[name]).issubset(values) and exact[name]
                                      for name in names) for names, values in applicable):
                if step.get("step_id"):
                    scoped_steps[step["step_id"]] = set(exact)
                continue
            bindings = step.get("filter_from_previous") or []
            inherited = {b["field"] for b in bindings if b.get("source_step_id") in scoped_steps
                and b.get("source_field") in scoped_steps[b["source_step_id"]]
                and b["field"] == b["source_field"]}
            literal_constraints_ok = all(any(name in inherited or name in exact and exact[name]
                and set(exact[name]).issubset(values) for name in names) for names, values in applicable)
            if applicable and inherited and literal_constraints_ok:
                if step.get("step_id"):
                    scoped_steps[step["step_id"]] = inherited | set(exact)
                continue
            # Do not allow independent field sets to cross-product unrelated rows.
            if not applicable and exact and self.derived_rows and all(
                any(all(str(row.get(k)) == v for k, v in zip(exact, values)) for row in self.derived_rows)
                for values in product(*exact.values())):
                continue
            reject("runtime_query_scope_rejected", "/filters")

    def remember(self, rows):
        # Only stable linking keys, never descriptive fields such as Currency.
        linking = {"MaterialDocument", "MaterialDocumentYear", "PurchaseOrder", "PurchaseOrderItem",
                   "SalesOrder", "SalesOrderItem", "Material", "Plant"}
        for row in rows[:200]:
            if isinstance(row, dict):
                self.derived_rows.append({k: v for k, v in row.items() if k in linking and v is not None})
        self.derived_rows = self.derived_rows[-500:]

    def check_skill(self, arguments):
        if not self.constraints:
            return
        value = arguments.get("input") or {}
        if not isinstance(value, dict):
            reject("runtime_skill_scope_rejected", "/input", "type")
        if any(key in value for key in ("where", "sql", "filter", "query")):
            reject("runtime_skill_scope_rejected", "/input", "unsupported_scope_expression")
        supplied = {field: [str(v) for v in raw] if isinstance(raw, list) else [str(raw)]
                    for name, raw in value.items() for field in ALIASES.get(name, (name,))}
        for item in value.get("filters") or []:
            if isinstance(item, dict) and item.get("operator", item.get("option", "eq")) in {"eq", "in"} and item.get("sign", "I") == "I":
                raw = item.get("value", item.get("low", item.get("values")))
                supplied[item.get("field")] = [str(v) for v in raw] if isinstance(raw, list) else [str(raw)]
        if not all(any(name in supplied and supplied[name] and set(supplied[name]).issubset(values)
                       for name in names) for names, values in self.constraints):
            reject("runtime_skill_scope_rejected", "/input")
