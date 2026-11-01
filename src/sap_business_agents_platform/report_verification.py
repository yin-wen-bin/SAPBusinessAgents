"""Owned, exact value proofs for v2 reports. No SDK or external I/O."""
from decimal import Decimal, InvalidOperation
import re

from .models import LocalizedText, ReportVerification

TOKEN = re.compile(r"\{\{([A-Za-z][A-Za-z0-9_]*)\}\}")
OPEN_TEMPLATE = {"zh": "存在未清项目，数量为 {{count}}。", "en": "Open items exist; count: {{count}}."}


def problem(code, path):
    return {"code": code, "path": path, "constraint": "owned_value_proof"}


def evidence(broker, run_id, ref):
    calls = broker.store.list_harness_tool_calls(run_id)
    call = next((c for c in calls if c.get("evidence_ref") == ref and c.get("status") == "completed"
                 and c.get("tool_name") == "sap_query_execute" and (c.get("output") or {}).get("ok") is True), None)
    if call is None:
        raise ValueError("value_evidence_not_owned")
    raw, meta = broker._read_evidence(run_id, ref)
    if (meta.get("source_type") != "sap_live" or meta.get("source_complete") is not True
            or call["output"].get("source_complete") is not True or raw.get("rows_redacted")
            or raw.get("restricted_artifact_ref")):
        raise ValueError("value_evidence_incomplete_or_restricted")
    from .harness import _extract_rows
    rows = _extract_rows(raw)
    if len(rows) != call["output"].get("row_count"):
        raise ValueError("value_evidence_rows_incomplete")
    return call, rows


def resolve(broker, run_id, binding):
    """Return canonical value, source reference, monetary flag and proof item."""
    state = broker.store.get_harness_state(run_id)
    if binding.kind == "calculation":
        record = (state.get("money_calculations") or {}).get(binding.calculation_ref)
        if not record or record.get("run_id") != run_id:
            raise ValueError("value_calculation_not_owned")
        if broker._calculate_money(run_id, record["evidence_ref"]) != record:
            raise ValueError("money_calculation_changed")
        item = next((m for m in record["metrics"] if m["id"] == binding.calculation_item), None)
        field = binding.calculation_field
        if item is None or item.get(field) is None:
            raise ValueError("value_calculation_item_invalid")
        currency = item.get("currency") if field == "amount" else None
        if binding.currency != currency or binding.evidence_ref not in (None, record["evidence_ref"]):
            raise ValueError("value_currency_or_source_mismatch")
        return str(item[field]), record["evidence_ref"], field == "amount", item
    call, rows = evidence(broker, run_id, binding.evidence_ref)
    from .technical_record_proof import record_profile
    p = record_profile(broker, run_id, call)
    if not p or set(binding.source_key) != set(p["technical_key"]):
        raise ValueError("value_complete_source_key_required")
    keys = [tuple(str(r.get(k)) for k in p["technical_key"]) for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("value_source_key_duplicate")
    key = tuple(str(binding.source_key[k]) for k in p["technical_key"])
    matching = [r for r, k in zip(rows, keys) if k == key]
    if (len(matching) != 1 or binding.source_field not in matching[0]
            or binding.source_field not in call['safe_input']['plan'].get('select_fields', [])):
        raise ValueError("value_source_row_or_field_missing")
    row = matching[0]
    field = binding.source_field
    amount = field in p["amount_pairs"]
    if amount:
        currency_field = p["amount_pairs"][field]
        if binding.currency_field != currency_field or not isinstance(row.get(currency_field), str) or not row[currency_field]:
            raise ValueError("value_amount_currency_pair_required")
        raw = row[field]
        if isinstance(raw, (bool, float)) or not isinstance(raw, (str, int)) or len(str(raw)) > 80:
            raise ValueError("value_amount_invalid")
        try:
            number = Decimal(raw)
            if not number.is_finite() or abs(number.adjusted()) > 40 or number.as_tuple().exponent < -18:
                raise InvalidOperation()
        except InvalidOperation:
            raise ValueError("value_amount_invalid") from None
        value = format(number, "f")
    else:
        if binding.currency_field or isinstance(row[field], (dict, list)):
            raise ValueError("value_source_field_invalid")
        value = "" if row[field] is None else str(row[field])
        metadata = (p.get("fields") or {}).get(field) or {}
        if p.get("technical_only") and (metadata.get("display_format") == "Date" or metadata.get("data_type") == "Edm.Date"):
            from .source_semantics import day
            parsed = day(row[field])
            if parsed is None:
                raise ValueError("value_source_date_invalid")
            value = parsed.isoformat()
    return value, binding.evidence_ref, amount, {"row_key": tuple(row[k] for k in p['technical_key']), "field": field}


def verify(broker, run_id, presentation, *, required):
    """Mutate only the candidate's canonical rendered text; return host proof."""
    result = ReportVerification()
    errors = []
    calculations = broker.store.get_harness_state(run_id).get("money_calculations") or {}
    for ref in {m.calculation_ref for b in presentation.blocks for m in b.metrics if m.calculation_ref}:
        record = calculations.get(ref)
        if record is not None and broker._calculate_money(run_id, record['evidence_ref']) != record:
            errors.append(problem('money_calculation_changed', '/metrics'))
    from .runtime_money import report_issues
    metrics = presentation.model_copy(update={"blocks": [b for b in presentation.blocks if b.type == "metrics"]})
    metric_errors, _ = report_issues(metrics, calculations, required=required)
    errors.extend(metric_errors)
    summary = result.summary_amounts
    for bi, block in enumerate(presentation.blocks):
        path = f"/blocks/{bi}"
        refs = set(block.evidence_refs)
        # Model-supplied labels and missing references cannot exempt content.
        customer = block.type != 'metrics'
        if block.type == "metrics":
            for mi, metric in enumerate(block.metrics):
                loc = f"{path}/metrics/{mi}"
                if metric.calculation_ref and not metric_errors:
                    summary.verified_paths.append(loc)
                    summary.evidence_refs.extend(metric.evidence_refs)
                elif required or metric.currency:
                    summary.issues.append(problem("money_metric_binding_required", loc))
            continue
        if block.type == "table":
            displayed_keys = set()
            for ri, row in enumerate(block.rows):
                row_refs, row_keys = set(), set()
                for unknown in set(row.value_bindings) - {c.key for c in block.columns}:
                    errors.append(problem("value_column_not_declared", f"{path}/rows/{ri}"))
                for ci, col in enumerate(block.columns):
                    loc = f"{path}/rows/{ri}/values/{ci}"
                    binding = row.value_bindings.get(col.key)
                    if binding is None:
                        if customer:
                            result.business_facts.issues.append(problem("business_value_binding_required", loc))
                        if col.format == "currency":
                            result.detail_amounts.issues.append(problem("money_cell_binding_required", loc))
                        continue
                    try:
                        value, ref, amount, proof = resolve(broker, run_id, binding)
                        if col.format == "currency" and not amount:
                            raise ValueError("value_monetary_source_semantics_required")
                        if row.values[ci].zh != value or row.values[ci].en != value:
                            raise ValueError("value_canonical_string_required")
                        if ref not in set(row.evidence_refs) | refs:
                            raise ValueError("value_source_reference_missing")
                        row_refs.add(ref)
                        if "row_key" in proof:
                            row_keys.add((ref, tuple(proof["row_key"])))
                        dimension = result.detail_amounts if amount else result.business_facts
                        dimension.verified_paths.append(loc)
                        dimension.evidence_refs.append(ref)
                    except ValueError as exc:
                        errors.append(problem(str(exc), loc))
                if len(row_keys) > 1:
                    errors.append(problem("value_row_key_conflict", f"{path}/rows/{ri}"))
                if row_keys and row_keys <= displayed_keys:
                    errors.append(problem('value_display_row_duplicate', f'{path}/rows/{ri}'))
                displayed_keys.update(row_keys)
            if block.display_truncated:
                result.coverage.issues.append(problem("report_rows_truncated", path))
            from .source_semantics import profile, digest
            source_refs = {ref for ref, _ in displayed_keys} or refs
            if customer and len(source_refs) == 1:
                ref = next(iter(source_refs))
                try:
                    call, source_rows = evidence(broker, run_id, ref)
                    from .technical_record_proof import record_profile
                    p = record_profile(broker, run_id, call)
                    if p is None:
                        raise ValueError('value_complete_source_key_required')
                    actual = digest(sorted([list(key) for source, key in displayed_keys if source == ref], key=str))
                    expected = digest(sorted([[r[k] for k in p['technical_key']] for r in source_rows], key=str))
                    if block.row_set_binding:
                        if block.row_set_binding.kind != 'calculation':
                            raise ValueError('row_set_calculation_required')
                        _, proof_ref, _, item = resolve(broker, run_id, block.row_set_binding)
                        if proof_ref != ref:
                            raise ValueError('row_set_source_mismatch')
                        expected = item['row_keys_digest']
                    else:
                        # A single declared summary group may define a historical subset.
                        groups = {item['row_keys_digest'] for b in presentation.blocks for m in b.metrics
                                  for record in [calculations.get(m.calculation_ref) or {}]
                                  for item in record.get('metrics', [])
                                  if record.get('evidence_ref') == ref and item['id'] == m.calculation_item}
                        if len(groups) == 1:
                            expected = next(iter(groups))
                        elif len(groups) > 1:
                            result.coverage.issues.append(problem('row_set_binding_required', path))
                    if actual != expected:
                        result.coverage.issues.append(problem('report_row_set_mismatch', path))
                except ValueError as exc:
                    errors.append(problem(str(exc), path))
            elif customer:
                result.coverage.issues.append(problem('report_row_set_unproven', path))
        if block.text_template is not None:
            if block.type not in {"text", "notice"}:
                errors.append(problem("value_template_block_type_invalid", path))
                continue
            templates = block.text_template.model_dump()
            tokens = {m for text in templates.values() for m in TOKEN.findall(text)}
            if tokens != set(block.value_bindings) or any(set(TOKEN.findall(t)) != tokens for t in templates.values()):
                errors.append(problem("value_template_binding_mismatch", path))
                continue
            resolved, items = {}, {}
            for name, binding in block.value_bindings.items():
                try:
                    value, ref, amount, item = resolve(broker, run_id, binding)
                    if ref not in refs:
                        raise ValueError("value_source_reference_missing")
                    resolved[name], items[name] = value, item
                    dimension = result.detail_amounts if amount else result.business_facts
                    dimension.verified_paths.append(f"{path}/value_bindings/{name}")
                    dimension.evidence_refs.append(ref)
                except ValueError as exc:
                    errors.append(problem(str(exc), f"{path}/value_bindings/{name}"))
            rendered = {lang: TOKEN.sub(lambda m: resolved.get(m[1], m[0]), text) for lang, text in templates.items()}
            if block.text and block.text.model_dump() != templates and block.text.model_dump() != rendered:
                errors.append(problem("value_template_render_mismatch", path))
            block.text = LocalizedText(**rendered)
            if block.fact_rule == "open_items_exist":
                count = items.get("count") or {}
                if (templates != OPEN_TEMPLATE or count.get("historical_status") != "open_at_cutoff"
                        or not str(count.get("count", "")).isdigit() or int(count["count"]) <= 0
                        or resolved.get('count') != count.get('count')
                        or block.value_bindings.get('count').calculation_field != 'count'):
                    errors.append(problem("business_fact_rule_not_proven", path))
                else:
                    result.business_facts.verified_paths.append(path)
            elif customer or required:
                result.business_facts.issues.append(problem("narrative_business_claim_unproven", path))
        elif block.type != "table" and (customer or required):
            result.business_facts.issues.append(problem("business_claim_binding_required", path))
        if block.value_bindings and block.text_template is None:
            errors.append(problem("value_template_required", path))
        if block.fact_rule and block.text_template is None:
            errors.append(problem("business_fact_template_required", path))
    if metric_errors:
        summary.status = "failed"
    for dimension in (summary, result.detail_amounts, result.business_facts):
        if dimension.status != "failed":
            dimension.status = "needs_evidence" if dimension.issues else "verified" if dimension.verified_paths else "not_requested"
        dimension.evidence_refs = sorted(set(dimension.evidence_refs))
    if required and summary.status == result.detail_amounts.status == "not_requested":
        summary.status = "needs_evidence"
        summary.issues.append(problem("money_proof_required", "/"))
    from .report_scope import assess_coverage
    result.coverage, result.scope_receipt = assess_coverage(broker, run_id, presentation, result.coverage.issues)
    monetary = (summary, result.detail_amounts)
    legacy = ("unverified" if any(d.status in {"needs_evidence", "failed"} for d in monetary) else
              "verified_metrics_only" if summary.status == "verified" else "not_requested")
    return errors, legacy, result
