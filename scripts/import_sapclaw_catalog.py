#!/usr/bin/env python3
"""One-time, read-only migration of advisory SAPClaw catalog assets.

This script is deliberately outside the runtime package. It reads a supplied
snapshot, emits a sanitized GET-only Catalog Seed and service registry, and
never copies raw metadata, response rows, URLs, credentials, inferred joins,
lookup paths, vectors, or runtime code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ALLOWED_GUIDANCE_SECTIONS = {
    "purpose",
    "when to use",
    "when not to use",
    "business semantics",
    "common planning patterns",
    "pitfalls",
    "needs verification",
}
FORBIDDEN_TEXT = re.compile(
    r"(?i)(https?://|sap-client|authorization|password|credential|sapclaw|"
    r"\b(?:post|patch|delete|put|create|update|cancel)\b|创建|更新|删除|取消|过账)"
)
PRIVATE_TEXT = re.compile(
    r"(?i)(https?://|sap-client|(?:user(?:name)?|password|credential|authorization)\s*[:=])"
)
SAFE_NAME = re.compile(r"^[A-Za-z0-9_]+(?:;v=[0-9]+)?$")
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def file_hash(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def safe_text(value: Any, *, max_length: int = 2000) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if "\ufffd" in text or PRIVATE_TEXT.search(text):
        return ""
    return text[:max_length]


def read_only_description(value: Any) -> str:
    text = str(value or "").strip()
    parts = re.split(r"(?<=[.!?。！？])\s+", text)
    kept = [safe_text(part) for part in parts if part and not FORBIDDEN_TEXT.search(part)]
    return " ".join(part for part in kept if part)[:2000]


def parse_guidance(path: Path) -> dict[str, list[str]]:
    if not path.is_file():
        return {}
    sections: dict[str, list[str]] = {}
    current = ""
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", raw)
        if heading:
            current = heading.group(1).strip().lower()
            continue
        if current not in ALLOWED_GUIDANCE_SECTIONS:
            continue
        line = raw.strip()
        if not line or FORBIDDEN_TEXT.search(line):
            continue
        line = safe_text(line, max_length=1000)
        if line:
            sections.setdefault(current.replace(" ", "_"), []).append(line)
    return sections


def current_service_refs(repository_root: Path) -> set[str]:
    refs: set[str] = set()
    for path in (repository_root / "agents").rglob("agent.json"):
        collect_service_refs(load_json(path, {}), refs)
    collect_service_refs(load_json(repository_root / "config" / "business-relationships.json", {}), refs)
    return refs


def collect_service_refs(value: Any, refs: set[str]) -> None:
    if isinstance(value, dict):
        service = value.get("service_name")
        if isinstance(service, str) and SAFE_NAME.fullmatch(service):
            refs.add(service)
        for child in value.values():
            collect_service_refs(child, refs)
    elif isinstance(value, list):
        for child in value:
            collect_service_refs(child, refs)


def validate_curated_entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise SystemExit("Curated catalog terms must contain an entries array.")
    entries: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise SystemExit("Each curated catalog entry must be an object.")
        service = str(item.get("service_name") or "")
        version = str(item.get("odata_version") or "")
        entity = str(item.get("entity_set") or "")
        if not SAFE_NAME.fullmatch(service) or version not in {"2.0", "4.0"} or not SAFE_IDENTIFIER.fullmatch(entity):
            raise SystemExit("Curated catalog entries require a safe service, explicit version, and entity.")
        fields = [str(field) for field in item.get("candidate_fields") or []]
        if any(not SAFE_IDENTIFIER.fullmatch(field) for field in fields):
            raise SystemExit("Curated catalog candidate fields must be identifiers.")
        if PRIVATE_TEXT.search(json.dumps(item, ensure_ascii=False)):
            raise SystemExit("Curated catalog entries cannot contain transport or credential data.")
        entries.append(dict(item))
    return entries


def sanitize_service(index_dir: Path, api_skills_root: Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    services_path = index_dir / "services.json"
    services = load_json(services_path, [])
    service = services[0] if isinstance(services, list) and services and isinstance(services[0], dict) else {}
    name = str(service.get("service_name") or index_dir.name).strip()
    if not SAFE_NAME.fullmatch(name):
        return None, {"source": index_dir.name, "reason": "invalid_service_name"}

    raw_entities = load_json(index_dir / "entities.json", [])
    raw_fields = load_json(index_dir / "fields.json", [])
    fields_by_entity: dict[str, list[dict[str, Any]]] = {}
    accepted_fields: set[tuple[str, str]] = set()
    for field in raw_fields if isinstance(raw_fields, list) else []:
        if not isinstance(field, dict) or field.get("runtime_available") is False:
            continue
        entity = str(field.get("entity_set") or "")
        field_name = str(field.get("field_name") or "")
        if not SAFE_IDENTIFIER.fullmatch(entity) or not SAFE_IDENTIFIER.fullmatch(field_name):
            continue
        item = {
            "field_name": field_name,
            "data_type": safe_text(field.get("data_type"), max_length=120),
            "label": safe_text(field.get("label"), max_length=300),
            "business_aliases": sorted(
                {
                    safe_text(alias, max_length=200)
                    for alias in (field.get("business_aliases") or [])
                    if safe_text(alias, max_length=200)
                }
            ),
            "is_key": field.get("is_key") is True,
            "selectable": field.get("selectable") is not False,
            "filterable": field.get("filterable") is not False,
            "sortable": field.get("sortable") is not False,
        }
        fields_by_entity.setdefault(entity, []).append(item)
        accepted_fields.add((entity, field_name))

    entities: list[dict[str, Any]] = []
    accepted_entities: set[str] = set()
    for entity in raw_entities if isinstance(raw_entities, list) else []:
        if not isinstance(entity, dict) or entity.get("runtime_available") is False:
            continue
        methods = {str(item).upper() for item in entity.get("supported_methods") or []}
        if methods and "GET" not in methods:
            continue
        entity_set = str(entity.get("entity_set") or "")
        if not SAFE_IDENTIFIER.fullmatch(entity_set):
            continue
        accepted_entities.add(entity_set)
        entities.append(
            {
                "entity_set": entity_set,
                "entity_type": safe_text(entity.get("entity_type"), max_length=300),
                "description": read_only_description(entity.get("description")),
                "key_fields": [str(item) for item in entity.get("key_fields") or [] if str(item)],
                "business_aliases": sorted(
                    {
                        safe_text(item, max_length=200)
                        for item in entity.get("business_aliases") or []
                        if safe_text(item, max_length=200)
                    }
                ),
                "fields": sorted(fields_by_entity.get(entity_set, []), key=lambda item: item["field_name"]),
                "supported_operations": ["GET"],
            }
        )

    terms: list[dict[str, Any]] = []
    raw_terms = load_json(index_dir / "business_terms.json", [])
    for term in raw_terms if isinstance(raw_terms, list) else []:
        if not isinstance(term, dict):
            continue
        entity = str(term.get("mapped_entity_set") or "")
        mapped_fields = [
            str(field)
            for field in term.get("mapped_fields") or []
            if (entity, str(field)) in accepted_fields
        ]
        if entity and entity not in accepted_entities:
            continue
        text = safe_text(term.get("term"), max_length=300)
        if not text or FORBIDDEN_TEXT.search(text):
            continue
        try:
            confidence = min(1.0, max(0.0, float(term.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        terms.append(
            {
                "term": text,
                "entity_set": entity or None,
                "fields": mapped_fields,
                "synonyms": sorted(
                    {
                        safe_text(item, max_length=300)
                        for item in term.get("synonyms") or []
                        if safe_text(item, max_length=300)
                        and not FORBIDDEN_TEXT.search(str(item))
                    }
                ),
                "confidence": confidence,
                "source": safe_text(term.get("source"), max_length=100),
            }
        )

    base_path = str(service.get("base_path") or "").strip().rstrip("/")
    if base_path.startswith("/sap/opu/odata4/"):
        version = "4.0"
    elif base_path.startswith("/sap/opu/odata/sap/"):
        version = "2.0"
    else:
        version = ""
    record = {
        "service_name": name,
        "odata_version": version or None,
        "artifact_version": safe_text(service.get("service_version"), max_length=80) or None,
        "openapi_version": safe_text(service.get("openapi_version"), max_length=80) or None,
        "description": read_only_description(service.get("description")),
        "business_aliases": sorted(
            {
                safe_text(item, max_length=300)
                for item in service.get("business_aliases") or []
                if safe_text(item, max_length=300)
            }
        ),
        "guidance": parse_guidance(api_skills_root / name / "skill.md"),
        "entities": sorted(entities, key=lambda item: item["entity_set"]),
        "business_terms": sorted(terms, key=lambda item: (item["term"], item.get("entity_set") or "")),
        "supported_operations": ["GET"],
        "schema_authority": "live_metadata_required_before_execution",
        "source_hash": file_hash(services_path) if services_path.is_file() else sha256_bytes(name.encode()),
    }
    quarantine = {
        "service_name": name,
        "relations": count_json_items(index_dir / "relations.json"),
        "lookup_paths": count_json_items(index_dir / "lookup_paths.json"),
        "entity_graph_items": count_json_items(index_dir / "entity_graph.json"),
        "derived_documents": sum(1 for item in ("doc_chunks.jsonl", "vector_documents.jsonl") if (index_dir / item).is_file()),
        "raw_metadata_files": len(list((index_dir / "raw").glob("*"))) if (index_dir / "raw").is_dir() else 0,
    }
    record["_binding_path"] = base_path if version else ""
    return record, quarantine


def count_json_items(path: Path) -> int:
    value = load_json(path, [])
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return 0


def build_outputs(source_root: Path, repository_root: Path, generated_at: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    index_root = source_root / "data" / "index"
    api_skills_root = source_root / "data" / "api_skills"
    if not index_root.is_dir():
        raise SystemExit("The supplied source does not contain data/index.")
    services: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    registry_records: dict[tuple[str, str], dict[str, Any]] = {}
    consumed_files: set[Path] = set()
    for index_dir in sorted(path for path in index_root.iterdir() if path.is_dir()):
        consumed_files.update(
            path
            for path in (
                index_dir / "services.json",
                index_dir / "entities.json",
                index_dir / "fields.json",
                index_dir / "business_terms.json",
                api_skills_root / index_dir.name / "skill.md",
            )
            if path.is_file()
        )
        record, quarantine = sanitize_service(index_dir, api_skills_root)
        if record is None:
            rejected.append(quarantine)
            continue
        binding_path = record.pop("_binding_path")
        services.append(record)
        quarantined.append(quarantine)
        if record.get("odata_version") and binding_path:
            version = str(record["odata_version"])
            registry_records[(record["service_name"], version)] = {
                "service_name": record["service_name"],
                "odata_version": version,
                "service_root_path": binding_path,
                "metadata_path": f"{binding_path}/$metadata",
                "artifact_id": None,
                "artifact_version": record.get("artifact_version"),
                "openapi_version": record.get("openapi_version"),
                "catalog_source": "sapclaw_migration",
                "source_hash": record["source_hash"],
                "status": "seed",
                "enabled": True,
            }

    refs = current_service_refs(repository_root)
    manual_bindings: list[str] = []
    for name in sorted(refs):
        key = (name, "2.0")
        if key in registry_records:
            continue
        root = f"/sap/opu/odata/sap/{name}"
        digest = sha256_bytes(f"existing_embedded_v2_binding:{name}".encode())
        registry_records[key] = {
            "service_name": name,
            "odata_version": "2.0",
            "service_root_path": root,
            "metadata_path": f"{root}/$metadata",
            "artifact_id": None,
            "artifact_version": None,
            "openapi_version": None,
            "catalog_source": "manual",
            "source_hash": digest,
            "status": "seed",
            "enabled": True,
        }
        manual_bindings.append(name)

    curated_path = repository_root / "config" / "catalog-curated-terms.json"
    curated_payload = load_json(curated_path, {"schema_version": "2.0", "entries": []})
    curated_entries = validate_curated_entries(
        curated_payload.get("entries") if isinstance(curated_payload, dict) else []
    )
    catalog = {
        "schema_version": "2.0",
        "generated_at": generated_at,
        "provenance": "sanitized_one_time_snapshot",
        "schema_authority": "advisory_live_metadata_required_before_execution",
        "services": sorted(services, key=lambda item: item["service_name"]),
        "curated_search": curated_entries,
    }
    registry = {
        "schema_version": "2.0",
        "services": [registry_records[key] for key in sorted(registry_records)],
    }
    source_index_dirs = sorted(path for path in index_root.iterdir() if path.is_dir())
    source_inventory = {
        "services": len(source_index_dirs),
        "entities": sum(count_json_items(path / "entities.json") for path in source_index_dirs),
        "fields": sum(count_json_items(path / "fields.json") for path in source_index_dirs),
        "business_terms": sum(
            count_json_items(path / "business_terms.json") for path in source_index_dirs
        ),
    }
    unresolved_versions = sorted(
        item["service_name"] for item in services if item.get("odata_version") is None
    )
    source_files = [
        {
            "path": path.relative_to(source_root).as_posix(),
            "sha256": file_hash(path),
            "source_type": "guidance" if path.name.lower() == "skill.md" else "structured_index",
        }
        for path in sorted(consumed_files)
    ]
    source_replacement_characters = sum(
        path.read_text(encoding="utf-8", errors="replace").count("\ufffd")
        for path in consumed_files
    )
    license_path = source_root / "LICENSE"
    report = {
        "schema_version": "1.0",
        "generated_at": generated_at,
        "source_manifest_hash": sha256_bytes(
            "\n".join(f"{item['path']}:{item['sha256']}" for item in source_files).encode()
        ),
        "source_files": source_files,
        "source_inventory": source_inventory,
        "included": {
            "services": len(services),
            "entities": sum(len(item["entities"]) for item in services),
            "fields": sum(len(entity["fields"]) for item in services for entity in item["entities"]),
            "business_terms": sum(len(item["business_terms"]) for item in services),
            "guidance_services": sum(bool(item["guidance"]) for item in services),
        },
        "transformed": {
            "get_only": True,
            "manual_existing_v2_bindings": manual_bindings,
            "unresolved_odata_version_services": unresolved_versions,
            "curated_search_entries": len(curated_entries),
            "confidence_clamped": True,
            "urls_and_transport_details_removed": True,
        },
        "quarantined": quarantined,
        "rejected": rejected,
        "excluded_classes": [
            "cases_and_feedback",
            "raw_metadata",
            "response_rows",
            "relations",
            "entity_graph",
            "lookup_paths",
            "business_paths",
            "doc_chunks",
            "vector_documents",
            "runtime_frontend_mcp_and_skills",
        ],
        "quality": {
            "service_count": len(services),
            "registry_binding_count": len(registry["services"]),
            "unresolved_odata_version_count": len(unresolved_versions),
            "live_metadata_required": True,
            "source_replacement_character_count": source_replacement_characters,
            "published_replacement_character_count": 0,
        },
        "license": {
            "spdx": "MIT" if license_path.is_file() and "MIT License" in license_path.read_text(encoding="utf-8", errors="replace") else "UNKNOWN",
            "source_file": "LICENSE" if license_path.is_file() else None,
            "sha256": file_hash(license_path) if license_path.is_file() else None,
            "attribution": "SAPClaw contributors" if license_path.is_file() else None,
        },
        "curated_search_source_hash": file_hash(curated_path) if curated_path.is_file() else None,
    }
    leaked = PRIVATE_TEXT.findall(json.dumps(catalog, ensure_ascii=False))
    published_replacement_characters = json.dumps(catalog, ensure_ascii=False).count("\ufffd")
    report["quality"]["published_replacement_character_count"] = published_replacement_characters
    report["privacy_scan"] = {"passed": not leaked, "issue_count": len(leaked)}
    if leaked:
        raise SystemExit("Sanitized Catalog Seed failed the privacy scan.")
    if published_replacement_characters:
        raise SystemExit("Sanitized Catalog Seed still contains replacement-character noise.")
    report["output_hashes"] = {
        "data/catalog-seed/catalog.json": sha256_bytes(json_bytes(catalog)),
        "config/odata-services.json": sha256_bytes(json_bytes(registry)),
    }
    return catalog, registry, report


def write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json_bytes(value)
    path.write_bytes(content)
    return sha256_bytes(content)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--generated-at")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    generated_at = args.generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    catalog, registry, report = build_outputs(
        args.source_root.resolve(), args.repository_root.resolve(), generated_at
    )
    summary = {
        "catalog": report["included"],
        "registry_services": len(registry["services"]),
        "privacy_scan": report["privacy_scan"],
        "dry_run": args.dry_run,
    }
    if not args.dry_run:
        catalog_path = args.repository_root / "data" / "catalog-seed" / "catalog.json"
        report_path = args.repository_root / "data" / "catalog-seed" / "migration-report.json"
        registry_path = args.repository_root / "config" / "odata-services.json"
        hashes = {
            catalog_path.relative_to(args.repository_root).as_posix(): write_json(catalog_path, catalog),
            registry_path.relative_to(args.repository_root).as_posix(): write_json(registry_path, registry),
            report_path.relative_to(args.repository_root).as_posix(): write_json(report_path, report),
        }
        write_json(
            args.repository_root / "data" / "catalog-seed" / "sha256-manifest.json",
            {
                "schema_version": "1.0",
                "generated_at": generated_at,
                "algorithm": "SHA-256",
                "files": hashes,
                "manifest_self_hash": "not_applicable",
            },
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
