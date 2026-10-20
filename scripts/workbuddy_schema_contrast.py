"""Offline/explicit probe candidate only: equivalent JSON Schema type encoding.

Not imported by a production driver/worker. No value decoder or field repair.
Changing representation does not relax the shared business contract.
"""
from __future__ import annotations

import copy

from jsonschema import Draft202012Validator


def equivalent_union_schema(schema):
    Draft202012Validator.check_schema(schema)
    result = copy.deepcopy(schema)
    def walk(node):
        if not isinstance(node, dict):
            return
        types = node.get("type")
        if isinstance(types, list):
            node.pop("type")
            alternatives = [{"type": name} for name in types]
            if "anyOf" in node:
                node.setdefault("allOf", []).append({"anyOf": alternatives})
            else:
                node["anyOf"] = alternatives
        for name in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"):
            for child in (node.get(name) or {}).values():
                walk(child)
        for name in ("items", "additionalProperties", "unevaluatedProperties", "propertyNames", "contains", "not", "if", "then", "else"):
            child = node.get(name)
            if isinstance(child, list):
                for item in child:
                    walk(item)
            else:
                walk(child)
        for name in ("anyOf", "oneOf", "allOf", "prefixItems"):
            for child in node.get(name) or []:
                walk(child)
    walk(result)
    Draft202012Validator.check_schema(result)
    return result
