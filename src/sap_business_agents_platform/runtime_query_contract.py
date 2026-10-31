"""Shared admission over the existing Provider contract (no SAP I/O)."""
from __future__ import annotations

import copy
import json
import re
from datetime import date
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
    "scope_relation_ref": {"type": "string", "pattern": "^relation_[0-9a-f]{64}$"},
    "schema_version": {"type": "string", "const": "1.0", "description": 'Query plan version is the JSON string "1.0", never the Skill version integer 1.'},
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
VERSION = "sapba.query-contract/1"
INSTRUCTIONS = ("Use {plan:<GET plan>,query:<business question>} for query tools. "
    'schema_version, when supplied, is exactly the JSON string "1.0", not integer 1. '
    'Skill input schemas are independent: use the returned Skill schema, not the plan version. '
    'odata_version is the JSON string "2.0" or "4.0". '
    "Use declared field names and lowercase operators. Preserve confirmed scope and limits. "
    "After three stagnant plan failures, no new external reads are allowed; report the evidence gap honestly.")


def frozen_contract():
    from .runtime_evidence_bindings import registry
    return {"version": VERSION, "schema": copy.deepcopy(PLAN_SCHEMA), "instructions": INSTRUCTIONS,
            "evidence_bindings": registry()}


def tool_schema():
    return obj({"plan": copy.deepcopy(PLAN_SCHEMA), "query": {"type": "string"},
        "tool_call_id": {"type": "string", "description": "Stable idempotency key for this invocation."}}, ("plan",))


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


def reject(code, path="/", constraint="scope", *, issues=None):
    error = RuntimeContractError(code)
    error.detail = {"validation_issues": issues or [{"code": code, "path": path, "constraint": constraint}]}
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
        if error.validator == "required":
            missing = [name for name in error.validator_value if name not in error.instance]
            reject("runtime_plan_contract_invalid", issues=[{
                "code": "runtime_plan_contract_invalid", "path": path.rstrip("/") + "/" + name,
                "constraint": "required"} for name in missing])
        # Feedback comes only from the frozen schema, never the rejected value.
        # A bare `enum`/`const` leaves the model guessing protocol spellings.
        issue = {"code": "runtime_plan_contract_invalid", "path": path,
                 "constraint": str(error.validator),
                 "repair_action": "Correct the declared plan field; preserve confirmed filters, evidence and limits. "
                     "Do not repeat external reads to repair a plan-structure error."}
        if error.validator in {"const", "enum"}:
            issue["allowed_values"] = copy.deepcopy(
                [error.validator_value] if error.validator == "const" else error.validator_value)
        if isinstance(error.schema, dict) and error.schema.get("type"):
            issue["expected_type"] = error.schema["type"]
        reject("runtime_plan_contract_invalid", issues=[issue])
    nested = value.get("steps")
    if nested is not None and value.get("plan_kind") not in {"lookup", "multi_step"}:
        reject("runtime_plan_contract_invalid", "/steps", "plan_kind")
    if value.get("plan_kind") in {"lookup", "multi_step"} and not nested:
        reject("runtime_plan_contract_invalid", "/steps", "required")
    result = []
    for index, step in enumerate(nested or [value]):
        step = {**{k: value[k] for k in ("service_name", "odata_version") if k in value}, **step}
        missing = [name for name in ("service_name", "odata_version", "entity_set") if not step.get(name)]
        if missing:
            prefix = f"/steps/{index}" if nested else ""
            reject("runtime_plan_contract_invalid", issues=[{
                "code": "runtime_plan_contract_invalid", "path": prefix + "/" + name,
                "constraint": "required"} for name in missing])
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
# Explicit business semantics, not arbitrary *_to/_from name guessing. These
# are calendar-day bounds, never equality aliases or candidate-Agent plans.
DATE_BOUNDS = {
    "posting_date_from": (("PostingDate",), "ge"),
    "posting_date_to": (("PostingDate",), "le"),
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
        self.answer_filters = []
        self.bounds, self.input_issues = [], []
        def input_issue(name, code):
            safe = name if isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name) else None
            self.unresolved = True
            self.input_issues.append({"code": code, "path": "/input" + ("/" + safe if safe else ""),
                                      "constraint": "scope_input", **({"input_field": safe} if safe else {})})
        for name, value in inputs.items():
            if name == 'answer_scope':
                from .report_scope import confirmed_scope
                from types import SimpleNamespace
                self.answer_filters = confirmed_scope(SimpleNamespace(input={'answer_scope': value}))['filters']
                continue
            if value is None or value == "" or value == []:
                continue
            if name in DATE_BOUNDS:
                try:
                    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        raise ValueError()
                    day = date.fromisoformat(value)
                except ValueError:
                    input_issue(name, "runtime_scope_date_invalid")
                    continue
                fields, operator = DATE_BOUNDS[name]
                self.bounds.append({"input_field": name, "fields": fields, "operator": operator, "day": day})
                continue
            fields = ALIASES.get(name) or ((name,) if re.fullmatch(r"[A-Z][A-Za-z0-9]*", name) else ())
            if not fields or not isinstance(value, (str, int, float, list)) or isinstance(value, bool):
                input_issue(name, "runtime_scope_mapping_unsupported" if not fields else "runtime_scope_input_type_invalid")
            else:
                self.constraints.append((fields, [str(v) for v in value] if isinstance(value, list) else [str(value)]))
        lower = next((b["day"] for b in self.bounds if b["operator"] == "ge"), None)
        upper = next((b["day"] for b in self.bounds if b["operator"] == "le"), None)
        if lower is not None and upper is not None and lower > upper:
            input_issue("posting_date_from", "runtime_scope_bounds_conflict")
        self.derived_rows = []
        self.schemas = {}
        self.date_fields = set()
        self.bound_date_fields = {}
        self.normalizer = None
        self.relation_checker = None
        from .runtime_evidence_bindings import registry
        self.evidence_bindings = registry()

    def require_resolved(self):
        if self.unresolved:
            reject("runtime_scope_clarification_required", issues=self.input_issues[:20])

    def allowed_values(self, names, values, step):
        allowed = set(values)
        normalizer = self.normalizer
        if normalizer is not None and callable(getattr(normalizer, "field_rule", None)):
            for name in names:
                rule = normalizer.field_rule(*(step.get(k, "") for k in ("service_name", "odata_version", "entity_set")), name)
                allowed.update(str(normalizer.normalize_structured_value(value, rule=rule)) for value in values)
        # OData V2 date-only properties use a zone-free midnight DateTime literal.
        # This is an authorization comparison, not a rewrite of the plan or input.
        # Never infer date semantics from the field name or model value_type.
        key = tuple(step.get(k) for k in ("service_name", "odata_version", "entity_set"))
        if any((*key, name) in self.date_fields for name in names):
            for value in values:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    continue
                try:
                    date.fromisoformat(value)
                except ValueError:
                    continue
                allowed.add(value + "T00:00:00")
        return allowed

    def record_schema(self, response):
        if response.get("ok") is not True:
            return
        data = response.get("data") or {}
        # An incomplete projection cannot prove that a scope field is absent.
        if data.get("schema_authority") is not True or data.get("fields_truncated") is not False:
            return
        service = data.get("service") or {}
        observed_keys = {(f.get("service_name") or service.get("service_name"),
                          f.get("odata_version") or service.get("odata_version"), f.get("entity_set"))
                         for f in data.get("fields") or [] if isinstance(f, dict)}
        # A replacement authoritative projection revokes disappeared fields;
        # never retain an old mapping merely because it was once observed.
        for key in observed_keys:
            self.schemas[key] = set()
        if not observed_keys:
            for key in list(self.schemas):
                if key[:2] == (service.get('service_name'), service.get('odata_version')):
                    del self.schemas[key]
        # A new full projection can revoke calendar semantics. Do not retain an
        # old type for a field omitted from the replacement entity projection.
        for field_key in list(self.bound_date_fields):
            if field_key[:3] in observed_keys or (not observed_keys and field_key[:2] ==
                    (service.get("service_name"), service.get("odata_version"))):
                del self.bound_date_fields[field_key]
        for field in data.get("fields") or []:
            if not isinstance(field, dict):
                continue
            key = (field.get("service_name") or service.get("service_name"),
                   field.get("odata_version") or service.get("odata_version"), field.get("entity_set"))
            self.schemas.setdefault(key, set()).add(field.get("field_name"))
            field_key = (*key, field.get("field_name"))
            self.date_fields.discard(field_key)
            if (key[1] == "2.0" and field.get("data_type") == "Edm.DateTime"
                    and field.get("display_format") == "Date"):
                self.date_fields.add(field_key)
                self.bound_date_fields[field_key] = "v2_midnight"
            elif key[1] == "4.0" and field.get("data_type") == "Edm.Date":
                self.bound_date_fields[field_key] = "iso_date"

    def _check_bounds(self, step, fields):
        for bound in self.bounds:
            names = bound["fields"]
            if fields is not None and not set(names).intersection(fields):
                continue
            key = tuple(step.get(k) for k in ("service_name", "odata_version", "entity_set"))
            if fields is None or not any((*key, name) in self.bound_date_fields for name in names):
                reject("runtime_query_scope_rejected", "/entity_set", issues=[{
                    "code": "runtime_query_scope_rejected", "path": "/entity_set", "constraint": "scope",
                    "scope_issue": "authoritative_date_schema_required", "input_field": bound["input_field"],
                    "required_fields": list(names), "schema_request": {"service_name": key[0],
                        "odata_version": key[1], "entity_sets": [key[2]], "max_fields": 5000}}])
            candidates = [(index, f) for index, f in enumerate(step.get("filters") or []) if f["field"] in names]
            proven = False
            for _, item in candidates:
                mode = self.bound_date_fields.get((*key, item["field"]))
                raw = item["value"]
                values = raw if isinstance(raw, list) else [raw]
                days = []
                try:
                    for value in values:
                        if not isinstance(value, str):
                            raise ValueError()
                        if mode == "v2_midnight" and re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00", value):
                            value = value[:10]
                        if mode not in {"v2_midnight", "iso_date"} or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                            raise ValueError()
                        days.append(date.fromisoformat(value))
                except ValueError:
                    continue
                operator = item.get("operator", "eq")
                if not days or (operator == "in") != isinstance(raw, list):
                    continue
                if operator in {"eq", "in"}:
                    proven = all(d <= bound["day"] if bound["operator"] == "le" else d >= bound["day"] for d in days)
                elif len(days) == 1:
                    # Strict comparisons are equivalent at the next/previous
                    # calendar-day boundary only with authoritative date proof.
                    value = days[0]
                    if bound["operator"] == "le":
                        proven = ((operator == "le" and value <= bound["day"]) or
                                  (operator == "lt" and (value <= bound["day"] or (value - bound["day"]).days == 1)))
                    else:
                        proven = ((operator == "ge" and value >= bound["day"]) or
                                  (operator == "gt" and (value >= bound["day"] or (bound["day"] - value).days == 1)))
                if proven:
                    break
            if not proven:
                reject("runtime_query_scope_rejected", "/filters", issues=[{
                    "code": "runtime_query_scope_rejected", "path": "/filters", "constraint": "scope",
                    "scope_issue": "date_bound_not_proven", "input_field": bound["input_field"],
                    "required_fields": list(names), "allowed_operators": [bound["operator"], "lt" if bound["operator"] == "le" else "gt", "eq", "in"]}])

    def check(self, plan):
        candidates = plans(plan)
        self.require_resolved()
        if not self.constraints and not self.bounds:
            return  # Unconstrained questions still use Provider/budget bounds.
        scoped_steps = {}
        for step in candidates:
            fields = self.schemas.get(tuple(step.get(k) for k in ("service_name", "odata_version", "entity_set")))
            if step.get('scope_relation_ref'):
                try:
                    if self.relation_checker is None or self.relation_checker(step) is not True:
                        raise ValueError('runtime_relation_not_owned')
                except ValueError as exc:
                    reject('runtime_query_scope_rejected', '/scope_relation_ref', issues=[{
                        'code': 'runtime_query_scope_rejected', 'path': '/scope_relation_ref',
                        'constraint': 'scope', 'scope_issue': str(exc)}])
                continue
            self._check_bounds(step, fields)
            for expected in self.answer_filters:
                if (fields is None or expected['field'] not in fields or not any(
                        f.get('field') == expected['field'] and f.get('operator', 'eq') == expected['operator']
                        and str(f.get('value')) == str(expected['value']) for f in step.get('filters') or [])):
                    reject('runtime_query_scope_rejected', '/filters', issues=[{
                        'code':'runtime_query_scope_rejected', 'path':'/filters', 'constraint':'scope',
                        'scope_issue':'confirmed_answer_filter_required', 'required_fields':[expected['field']]}])
            if not self.constraints and self.bounds and fields is not None and any(set(b["fields"]).intersection(fields) for b in self.bounds):
                continue
            filters = step.get("filters") or []
            exact = {f["field"]: ([str(v) for v in f["value"]] if isinstance(f["value"], list) else [str(f["value"])])
                     for f in filters if f.get("operator", "eq") in {"eq", "in"}}
            from .runtime_evidence_bindings import scope_fields
            try:
                constraints = [(scope_fields(self.evidence_bindings['scope_mappings'], step, names, fields), values)
                    for names, values in self.constraints]
            except ValueError:
                reject('runtime_query_scope_rejected', '/entity_set', issues=[{
                    'code': 'runtime_query_scope_rejected', 'path': '/entity_set', 'constraint': 'scope',
                    'scope_issue': 'reviewed_mapping_metadata_mismatch',
                    'repair_action': 'Do not guess fields or drop scope. Recheck the reviewed mapping and complete authoritative metadata.'}])
            applicable = [(names, self.allowed_values(names, values, step)) for names, values in constraints
                          if fields is None or set(names).intersection(fields)]
            # Carry only fields actually proven to equal a frozen input, never
            # all incidental filters. Aliases in the same platform-declared
            # input group may propagate that exact value, not arbitrary joins.
            authorized = {name: set.intersection(*[values for names, values in applicable if name in names])
                for name in {name for names, _ in applicable for name in names}}
            proven = {name: set(values) for name, values in exact.items()
                if values and name in authorized and set(values).issubset(authorized[name])}
            complete_dimensions = len(applicable) == len(constraints)
            if applicable and complete_dimensions and all(any(name in proven for name in names) for names, _ in applicable):
                if step.get("step_id"):
                    scoped_steps[step["step_id"]] = proven
                continue
            bindings = step.get("filter_from_previous") or []
            inherited = {}
            for binding in bindings:
                target, source = binding["field"], binding["source_field"]
                values = scoped_steps.get(binding.get("source_step_id"), {}).get(source)
                equivalent = any(target in names and source in names for names, _ in constraints)
                if (values and equivalent and target in authorized and values.issubset(authorized[target])
                        and (target not in exact or target in proven)):
                    inherited[target] = values
            literal_constraints_ok = all(any(name in inherited or name in proven for name in names)
                for names, _ in applicable)
            if applicable and complete_dimensions and inherited and literal_constraints_ok:
                if step.get("step_id"):
                    scoped_steps[step["step_id"]] = {**proven, **inherited}
                continue
            # Do not allow independent field sets to cross-product unrelated rows.
            if not applicable and exact and self.derived_rows and all(
                any(all(str(row.get(k)) == v for k, v in zip(exact, values)) for row in self.derived_rows)
                for values in product(*exact.values())):
                continue
            # Without complete authoritative metadata we cannot assert that a
            # missing input field even exists on this entity. Keep the rejection
            # but request metadata, not invented filters. A plan already proving
            # every frozen constraint above retains its original behavior.
            if fields is None:
                reject("runtime_query_scope_rejected", "/entity_set", issues=[{
                    "code": "runtime_query_scope_rejected", "path": "/entity_set",
                    "constraint": "scope", "scope_issue": "authoritative_schema_required",
                    "message": "Read complete authoritative entity metadata with sap_schema_get before resolving applicable scope filters. Do not add unverified fields or drop confirmed conditions.",
                    "schema_request": {"service_name": step["service_name"],
                        "odata_version": step["odata_version"],
                        "entity_sets": [step["entity_set"]], "max_fields": 5000},
                }])
            issues = []
            if not applicable or not complete_dimensions:
                issues.append({'code': 'runtime_query_scope_rejected', 'path': '/filters', 'constraint': 'scope',
                    'scope_issue': 'service_scope_mapping_or_verified_relation_required',
                    'required_fields': [name for names, _ in self.constraints for name in names],
                    'repair_action': 'This entity cannot prove the frozen business scope. Use a reviewed service-specific mapping or a platform-verified relation; never invent filters or drop confirmed constraints.'})
            for names, values in applicable:
                if any(name in inherited or name in exact and exact[name]
                       and set(exact[name]).issubset(values) for name in names):
                    continue
                # Only platform-owned constraint names and schema paths, no values.
                positions = [index for index, item in enumerate(filters) if item["field"] in names]
                if not positions:
                    issues.append({"code": "runtime_query_scope_rejected", "path": "/filters",
                        "constraint": "scope", "scope_issue": "required_filter_missing",
                        "required_fields": list(names), "allowed_operators": ["eq", "in"]})
                for index in positions:
                    item = filters[index]
                    issue = "exact_operator_required" if item.get("operator", "eq") not in {"eq", "in"} else "value_outside_scope"
                    issues.append({"code": "runtime_query_scope_rejected", "path": f"/filters/{index}/" +
                        ("operator" if issue == "exact_operator_required" else "value"),
                        "constraint": "scope", "scope_issue": issue, "required_fields": list(names),
                        "allowed_operators": ["eq", "in"]})
            reject("runtime_query_scope_rejected", "/filters", issues=issues[:20])

    def remember(self, rows):
        # Only stable linking keys, never descriptive fields such as Currency.
        linking = {"MaterialDocument", "MaterialDocumentYear", "PurchaseOrder", "PurchaseOrderItem",
                   "SalesOrder", "SalesOrderItem", "Material", "Plant"}
        for row in rows[:200]:
            if isinstance(row, dict):
                self.derived_rows.append({k: v for k, v in row.items() if k in linking and v is not None})
        self.derived_rows = self.derived_rows[-500:]

    def check_skill(self, arguments):
        self.require_resolved()
        if self.bounds:
            # No inferred DDIC date aliases or unreviewed Skill input semantics.
            reject("runtime_skill_scope_rejected", "/input", issues=[{
                "code": "runtime_skill_scope_rejected", "path": "/input", "constraint": "scope",
                "scope_issue": "range_scope_not_proven", "input_field": b["input_field"],
                "required_fields": list(b["fields"])} for b in self.bounds])
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
        missing = [names for names, values in self.constraints if not any(
            name in supplied and supplied[name] and set(supplied[name]).issubset(values) for name in names)]
        if missing:
            # No inferred DDIC aliases or cross-source Skill joins. Keep the
            # rejection, but tell the model which confirmed constraints lack proof.
            reject("runtime_skill_scope_rejected", "/input", issues=[{
                "code": "runtime_skill_scope_rejected", "path": "/input", "constraint": "scope",
                "scope_issue": "required_scope_not_proven", "required_fields": list(names)} for names in missing][:20])
