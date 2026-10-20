"""Declared OData sample relationships; provider-independent and fail closed."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def equal(left, right):
    # bool/int and missing/missing are not proven SAP key equivalences.
    return left is not None and right is not None and type(left) is type(right) and left == right


@dataclass
class Node:
    id: str
    step_id: str
    declaration: dict
    parents: dict[str, list[tuple[str, str]]]
    conditional: bool = False


class SampleScopeGraph:
    """A bounded proof graph, not a query engine or a business-rule evaluator."""
    def __init__(self, context):
        from .sample_discovery import _source, _present
        self.context = context
        self.nodes: dict[str, Node] = {}
        self.error = None
        self.active = False
        self.scope = {name for plan in context.plans for _, name, _ in context._bindings(plan)
                      if _present(context.supplied_inputs.get(name))}
        self.context_digest = digest({"manifest": context.manifest, "revision": context.revision,
            "inputs": context.supplied_inputs, "requested": context.missing_fields()})
        groups = []
        outer_nodes = {}

        def visit(value, outer, path, conditional=False):
            if isinstance(value, dict):
                conditional = conditional or ("when" in value and value["when"] not in (None, True))
                if value.get("plan_kind") in {"multi_step", "lookup"} and isinstance(value.get("steps"), list):
                    group = {}
                    for item in value["steps"]:
                        if not isinstance(item, dict) or not item.get("step_id"):
                            continue
                        declaration = {**{k: value[k] for k in ("service_name", "odata_version") if k in value}, **item}
                        if not all(_source(declaration)):
                            continue
                        raw = str(item["step_id"])
                        if raw in group:
                            group = {}
                            break
                        group[raw] = Node(outer + "/" + path + ":" + raw, raw, declaration, {},
                                          conditional or ("when" in item and item["when"] is not True))
                    if group:
                        for node in group.values():
                            for edge in node.declaration.get("filter_from_previous") or []:
                                parent = group.get(str(edge.get("source_step_id") or "")) if isinstance(edge, dict) else None
                                if (not parent or not edge.get("field") or not edge.get("source_field")
                                        or edge.get("operator", "eq") not in {"eq", "in"}):
                                    node.conditional = True  # Unresolvable declaration, never guessed.
                                    continue
                                node.parents.setdefault(parent.id, []).append((str(edge["source_field"]), str(edge["field"])))
                        groups.append(group)
                    return
                if all(_source(value)):
                    raw = str(value.get("step_id") or outer)
                    if raw in outer_nodes:
                        outer_nodes[raw].conditional = True
                    else:
                        outer_nodes[raw] = Node(outer + "/" + path + ":" + raw, raw, copy.deepcopy(value), {}, conditional)
                    return
                for key, child in value.items():
                    visit(child, outer, path + "/" + str(key), conditional)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, outer, path + "/" + str(index), conditional)
        for index, step in enumerate((context.manifest.get("execution") or {}).get("steps") or []):
            if isinstance(step, dict) and step.get("executor") in {"sap_read", "sap"}:
                visit(step, str(step.get("id") or index), "execution", False)
        if outer_nodes:
            for node in outer_nodes.values():
                for edge in node.declaration.get("filter_from_previous") or []:
                    parent = outer_nodes.get(str(edge.get("source_step_id") or "")) if isinstance(edge, dict) else None
                    if (not parent or not edge.get("field") or not edge.get("source_field")
                            or edge.get("operator", "eq") not in {"eq", "in"}):
                        node.conditional = True
                    else:
                        node.parents.setdefault(parent.id, []).append((str(edge["source_field"]), str(edge["field"])))
            groups.append(outer_nodes)
        requested = context.missing_fields()
        declarations = [n.declaration for group in groups for n in group.values()]
        for declaration in declarations:
            if declaration not in context.plans:
                context.plans.append(declaration)
        self.scope = {name for plan in context.plans for _, name, _ in context._bindings(plan)
                      if _present(context.supplied_inputs.get(name))}
        if not requested or any(context.properties[n].get("type") not in {"string", "integer", "number", "boolean"} for n in requested):
            return
        for group in groups:
            if not any(n.parents for n in group.values()):
                continue  # No relationship: retain the existing early rejection.
            all_nodes = {n.id: n for n in group.values()}
            wanted = []
            for name in [*requested, *sorted(self.scope)]:
                owner = next((n for n in group.values() if any(bound == name and op == "eq" and
                    field in n.declaration.get("select_fields", []) for field, bound, op in context._bindings(n.declaration))), None)
                if owner is None:
                    break
                wanted.append(owner.id)
            else:
                required, visiting = set(), set()
                def ancestors(key):
                    if key in visiting:
                        raise ValueError("sample_relationship_cycle")
                    if key in required:
                        return
                    visiting.add(key)
                    for parent in all_nodes[key].parents:
                        ancestors(parent)
                    visiting.remove(key)
                    required.add(key)
                try:
                    for key in wanted:
                        ancestors(key)
                    nodes = {key: node for key, node in all_nodes.items() if key in required}
                    if len(nodes) > context.max_reads:
                        raise ValueError("sample_relationship_read_budget")
                    if any(n.conditional for n in nodes.values()):
                        raise ValueError("sample_relationship_condition_unproven")
                    connected = {wanted[0]}
                    while True:
                        old = len(connected)
                        for n in nodes.values():
                            if n.id in connected:
                                connected.update(n.parents)
                            if any(parent in connected for parent in n.parents):
                                connected.add(n.id)
                        if len(connected) == old:
                            break
                    if connected != set(nodes):
                        raise ValueError("sample_relationship_disconnected")
                    for n in nodes.values():
                        from .sample_discovery import _PRIVATE
                        if n.declaration.get("http_method", "GET") != "GET":
                            raise ValueError("sample_relationship_readonly_required")
                        for parent, fields in n.parents.items():
                            if any(left not in nodes[parent].declaration.get("select_fields", []) or
                                   right not in n.declaration.get("select_fields", []) or _PRIVATE.search(left) or
                                   _PRIVATE.search(right) for left, right in fields):
                                raise ValueError("sample_relationship_projection_missing")
                    self.nodes = nodes
                    # Only use this path when scope really spans nodes or a
                    # requested input depends on a declared upstream binding.
                    self.active = len(nodes) > 1 and any(n.parents for n in nodes.values())
                    self.error = None
                    return
                except ValueError as error:
                    self.error = str(error)

    def resolve(self, plan):
        from .sample_discovery import _source, SampleDiscoveryError
        matches = [n for n in self.nodes.values() if _source(n.declaration) == _source(plan)]
        step = plan.get("step_id")
        if step:
            matches = [n for n in matches if step in {n.id, n.step_id}]
        if len(matches) != 1:
            raise SampleDiscoveryError("sample_relationship_node_unproven")
        return matches[0]

    def local_filters(self, node):
        from .sample_discovery import _INPUT, _present, SampleDiscoveryError
        result = []
        for original in node.declaration.get("filters") or []:
            item = copy.deepcopy(original)
            match = _INPUT.fullmatch(str(item.get("value") or ""))
            if match:
                if not _present(self.context.supplied_inputs.get(match.group(1))):
                    continue
                item["value"] = self.context.supplied_inputs[match.group(1)]
            elif "{{" in str(item.get("value") or ""):
                raise SampleDiscoveryError("sample_relationship_scope_unresolved")
            result.append(item)
        return result

    def types_verified(self, node):
        from .sample_discovery import _source
        for parent, fields in node.parents.items():
            before = self.context.live_types.get(_source(self.nodes[parent].declaration), {})
            after = self.context.live_types.get(_source(node.declaration), {})
            if any(not before.get(left) or before.get(left) != after.get(right) for left, right in fields):
                return False
        return True

    def rows(self, reader):
        """Read hash-validated evidence; conflicting stable keys block the node."""
        from .sample_discovery import SampleDiscoveryError
        result = {key: [] for key in self.nodes}
        seen = {key: {} for key in self.nodes}
        for ref, source in self.context.evidence_sources.items():
            if not source.get("plan"):
                continue
            try:
                node = self.resolve(source["plan"])
            except SampleDiscoveryError:
                continue
            raw, meta = reader(ref)
            if (meta.get("source_type") != "sap_live" or raw.get("ok") is False or raw.get("upstream_incomplete")
                    or raw.get("rows_redacted") or raw.get("restricted_artifact_ref") or not source.get("key_fields")):
                continue
            for index, row in enumerate(raw.get("rows") or []):
                if not all(row.get(k) is not None for k in source["key_fields"]):
                    continue
                identity = digest([row.get(k) for k in source["key_fields"]])
                if identity in seen[node.id] and seen[node.id][identity] != digest(row):
                    raise SampleDiscoveryError("sample_relationship_conflict")
                seen[node.id][identity] = digest(row)
                if not self.context.row_matches(source["plan"], row) or not self.local_row(node, row):
                    continue
                result[node.id].append((ref, index, row, source))
        return result

    def local_row(self, node, row):
        from .sample_discovery import _matches_filter, _source
        types = self.context.live_types.get(_source(node.declaration), {})
        return all(_matches_filter(row, {**item, **({"value_type": "date"} if types.get(item.get("field")) in
                   {"Edm.Date", "Edm.DateTime"} else {})}) for item in self.local_filters(node))

    def check(self, plan, reader):
        from .sample_discovery import SampleDiscoveryError
        node = self.resolve(plan)
        actual = plan.get("filters") or []
        if any(not any(a.get("field") == e.get("field") and a.get("operator", "eq") == e.get("operator", "eq")
                       and type(a.get("value")) is type(e.get("value")) and a.get("value") == e.get("value") for a in actual) for e in self.local_filters(node)):
            raise SampleDiscoveryError("sample_scope_not_preserved")
        if node.parents:
            if not self.types_verified(node):
                raise SampleDiscoveryError("sample_relationship_types_unproven")
            rows = self.rows(reader)
            for parent, pairs in node.parents.items():
                expected = []
                for left, right in pairs:
                    clause = [item for item in actual if item.get("field") == right and item.get("operator", "eq") == "eq"]
                    if len(clause) != 1:
                        raise SampleDiscoveryError("sample_relationship_tuple_unproven")
                    expected.append(clause[0]["value"])
                if not any(all(equal(row.get(left), value) for (left, _), value in zip(pairs, expected)) for _, _, row, _ in rows[parent]):
                    raise SampleDiscoveryError("sample_relationship_tuple_unproven")
        return node.declaration

    def proof(self, selected, reader):
        """Prove one connected cohort, including downstream scope constraints."""
        from .sample_discovery import SampleDiscoveryError
        if self.context.closed or not self.context.remaining():
            return None
        rows = self.rows(reader)
        chosen = {}
        for ref, index in selected.items():
            source = self.context.evidence_sources.get(ref) or {}
            if not source.get("plan"):
                return None
            node = self.resolve(source["plan"])
            row = next((item for item in rows[node.id] if item[0] == ref and item[1] == index), None)
            if row is None or node.id in chosen and chosen[node.id] != row:
                return None
            chosen[node.id] = row
        if not chosen or any(not self.types_verified(n) for n in self.nodes.values()):
            return None
        def consistent(key, candidate, values):
            row = candidate[2]
            for child in self.nodes.values():
                for parent, pairs in child.parents.items():
                    if key == child.id and parent in values:
                        if any(not equal(row.get(right), values[parent][2].get(left)) for left, right in pairs):
                            return False
                    if key == parent and child.id in values:
                        if any(not equal(row.get(left), values[child.id][2].get(right)) for left, right in pairs):
                            return False
            return True
        attempts = 0
        def solve(values):
            nonlocal attempts
            attempts += 1
            if attempts > 10000 or not self.context.remaining():
                return None
            if len(values) == len(self.nodes):
                return values
            for key, node in self.nodes.items():
                if key in values:
                    continue
                adjacent = set(node.parents) | {n.id for n in self.nodes.values() if key in n.parents}
                if not adjacent.intersection(values):
                    continue
                for row in rows[key]:
                    if consistent(key, row, values):
                        solved = solve({**values, key: row})
                        if solved:
                            return solved
                return None
            return None
        if any(not consistent(key, row, chosen) for key, row in chosen.items()):
            return None
        solved = solve(chosen)
        if not solved:
            return None
        proof = {"contract_version": "1.0", "context_digest": self.context_digest,
            "verified_inputs": sorted(self.scope),
            "sources": [{"node_id": key, "evidence_ref": item[0], "row_indices": [item[1]],
                         "row_hash": digest(item[2]), "row_keys": {k: item[2][k] for k in item[3]["key_fields"]}}
                        for key, item in solved.items()],
            "relations": [{"source_node_id": parent, "target_node_id": node.id,
                           "field_pairs": [{"source_field": left, "target_field": right} for left, right in pairs]}
                          for node in self.nodes.values() for parent, pairs in node.parents.items()]}
        proof["proof_digest"] = digest(proof)
        return proof

    def public_rows(self, ref, reader):
        from .sample_discovery import _PRIVATE, SampleDiscoveryError
        source = self.context.evidence_sources[ref]
        raw, _ = reader(ref)
        node = self.resolve(source["plan"])
        keys = set()
        for child in self.nodes.values():
            keys.update(left for left, _ in child.parents.get(node.id, []))
        public = []
        for index, row in enumerate(raw.get("rows") or []):
            proof = self.proof({ref: index}, reader)
            public.append({k: v for k, v in row.items() if (proof or k in keys) and not _PRIVATE.search(k)})
        return public

    def contract(self):
        return {"version": "1.0", "context_digest": self.context_digest,
            "required_scope_inputs": sorted(self.scope),
            "nodes": [{"step_id": n.step_id, "declaration_id": n.id,
                       "source": {k: n.declaration[k] for k in ("service_name", "odata_version", "entity_set")},
                       "bindings": [{"source_step_id": self.nodes[parent].step_id,
                                     "field_pairs": [{"source_field": left, "field": right} for left, right in pairs]}
                                    for parent, pairs in n.parents.items()],
                       "filters": self.local_filters(n)} for n in self.nodes.values()]}
