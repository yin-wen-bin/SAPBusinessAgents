#!/usr/bin/env python3
"""Normalize a fetched SAP BAH artifact into a reviewable GET-only candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PRIVATE = re.compile(r"(?i)(https?://|sap-client|authorization|password|credential)")
PRIVATE_OR_WRITE_SENTENCE = re.compile(
    r"(?i)(https?://|sap-client|authorization|password|credential|\b(?:post|put|patch|delete)\b)"
)
WRITE_METHODS = {"post", "put", "patch", "delete"}
SAFE_ID = re.compile(r"^[A-Za-z0-9_]+$")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def safe_description(value: Any, *, limit: int) -> str:
    parts = re.split(r"(?<=[.!?。！？])\s+|[\r\n]+", str(value or ""))
    kept = [re.sub(r"\s+", " ", item).strip() for item in parts]
    return " ".join(
        item for item in kept if item and not PRIVATE_OR_WRITE_SENTENCE.search(item)
    )[:limit]


def load_spec(raw_root: Path) -> tuple[dict[str, Any], str]:
    json_path = raw_root / "json.json"
    if json_path.is_file():
        value = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise SystemExit("SAP BAH JSON specification must be an object.")
        return value, "json"
    yaml_path = raw_root / "yaml.yaml"
    if yaml_path.is_file():
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise SystemExit("Install PyYAML to normalize a YAML-only SAP BAH artifact.") from exc
        value = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise SystemExit("SAP BAH YAML specification must be an object.")
        return value, "yaml"
    raise SystemExit("No JSON or YAML SAP BAH specification was found.")


def detected_odata_version(spec: dict[str, Any], raw_root: Path) -> str | None:
    candidates = [
        spec.get("x-sap-odata-version"),
        spec.get("x-odata-version"),
        (spec.get("info") or {}).get("x-odata-version") if isinstance(spec.get("info"), dict) else None,
    ]
    for candidate in candidates:
        text = str(candidate or "").strip().lower()
        if text in {"2", "2.0", "v2"}:
            return "2.0"
        if text in {"4", "4.0", "v4"}:
            return "4.0"
    edmx = raw_root / "edmx.xml"
    if edmx.is_file():
        try:
            root = ET.fromstring(edmx.read_text(encoding="utf-8"))
        except ET.ParseError:
            return None
        version = str(root.attrib.get("Version") or "")
        if version.startswith("4"):
            return "4.0"
        if version.startswith("1"):
            return "2.0"
    return None


def normalize(
    spec: dict[str, Any],
    *,
    service_name: str,
    artifact: str,
    artifact_version: str | None,
    odata_version: str,
    source_hash: str | None = None,
    source_format: str | None = None,
    fetched_at: str | None = None,
    source_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not SAFE_ID.fullmatch(service_name) or not SAFE_ID.fullmatch(artifact):
        raise SystemExit("Artifact and service names must be safe technical identifiers.")
    if odata_version not in {"2.0", "4.0"}:
        raise SystemExit("OData version must explicitly be 2.0 or 4.0.")
    operations: list[dict[str, Any]] = []
    rejected_writes = 0
    for path_name, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            normalized_method = str(method).lower()
            if normalized_method in WRITE_METHODS:
                rejected_writes += 1
                continue
            if normalized_method != "get" or not isinstance(operation, dict):
                continue
            operations.append(
                {
                    "method": "GET",
                    "path_template": str(path_name),
                    "summary": safe_description(operation.get("summary"), limit=1000),
                    "description": safe_description(operation.get("description"), limit=2000),
                    "operation_id": str(operation.get("operationId") or "")[:300],
                    "deprecated": operation.get("deprecated") is True,
                }
            )

    schemas = ((spec.get("components") or {}).get("schemas") or {})
    if not schemas:
        schemas = spec.get("definitions") or {}
    schema_rows: list[dict[str, Any]] = []
    for schema_name, schema in schemas.items():
        if not isinstance(schema, dict):
            continue
        properties = schema.get("properties") or {}
        schema_rows.append(
            {
                "schema_name": str(schema_name),
                "description": safe_description(schema.get("description"), limit=2000),
                "fields": [
                    {
                        "field_name": str(field_name),
                        "type": str(field.get("type") or field.get("format") or "") if isinstance(field, dict) else "",
                        "description": safe_description(field.get("description"), limit=1000) if isinstance(field, dict) else "",
                    }
                    for field_name, field in properties.items()
                ],
            }
        )
    openapi_version = str(spec.get("openapi") or spec.get("swagger") or "") or None
    candidate = {
        "schema_version": "2.0",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "service_name": service_name,
        "odata_version": odata_version,
        "artifact_id": artifact,
        "artifact_version": artifact_version,
        "openapi_version": openapi_version,
        "catalog_source": "sap_bah",
        "status": "seed",
        "supported_operations": ["GET"],
        "schema_authority": "advisory_live_metadata_required_before_execution",
        "source_format": source_format,
        "fetched_at": fetched_at,
        "source_evidence": source_evidence or {},
        "operations": operations,
        "schemas": schema_rows,
        "rejected_write_operation_count": rejected_writes,
    }
    candidate["source_hash"] = source_hash or sha256_bytes(
        json.dumps(spec, ensure_ascii=False, sort_keys=True).encode()
    )
    candidate["normalized_hash"] = sha256_bytes(
        json.dumps(candidate, ensure_ascii=False, sort_keys=True).encode()
    )
    if PRIVATE.search(json.dumps(candidate, ensure_ascii=False)):
        raise SystemExit("Normalized SAP BAH candidate contains transport or credential data.")
    return candidate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--service-name", required=True)
    parser.add_argument("--odata-version", choices=["2.0", "4.0"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--publish", action="store_true", help="Write the sanitized candidate under data/catalog-seed/sap-bah")
    args = parser.parse_args()

    if not SAFE_ID.fullmatch(args.artifact) or not SAFE_ID.fullmatch(args.service_name):
        raise SystemExit("Artifact and service names must be safe technical identifiers.")

    raw_root = args.repository_root / ".artifacts" / "sap-bah" / args.artifact / "raw"
    manifest_path = raw_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    spec, source_format = load_spec(raw_root)
    detected = detected_odata_version(spec, raw_root)
    requested = args.odata_version
    if detected and requested and detected != requested:
        raise SystemExit(f"Declared OData version {requested} conflicts with artifact evidence {detected}.")
    version = detected or requested
    if version not in {"2.0", "4.0"}:
        raise SystemExit("OData version is unresolved; supply --odata-version after reviewing the artifact.")
    spec_path = raw_root / ("json.json" if source_format == "json" else "yaml.yaml")
    manifest_files = {
        str(item.get("name") or ""): item
        for item in manifest.get("files") or []
        if isinstance(item, dict)
    }
    source_manifest = manifest_files.get(source_format) or {}
    edmx_manifest = manifest_files.get("edmx") or {}
    candidate = normalize(
        spec,
        service_name=args.service_name,
        artifact=args.artifact,
        artifact_version=str(manifest.get("artifact_version") or "") or None,
        odata_version=version,
        source_hash=sha256_bytes(spec_path.read_bytes()),
        source_format=source_format,
        fetched_at=str(manifest.get("fetched_at") or "") or None,
        source_evidence={
            "spec_etag": source_manifest.get("etag"),
            "spec_last_modified": source_manifest.get("last_modified"),
            "edmx_sha256": edmx_manifest.get("sha256"),
        },
    )
    output_root = (
        args.repository_root / "data" / "catalog-seed" / "sap-bah"
        if args.publish
        else args.repository_root / ".artifacts" / "sap-bah" / args.artifact / "normalized"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / f"{args.service_name}.json"
    output_path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    registry_path = args.repository_root / "config" / "odata-services.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.is_file() else {"services": []}
    current = next(
        (
            item
            for item in registry.get("services") or []
            if item.get("service_name") == args.service_name and item.get("odata_version") == version
        ),
        None,
    )
    result = {
        "status": "review_required",
        "diff_status": (
            "new_service_version"
            if current is None
            else "unchanged_source"
            if current.get("source_hash") == candidate.get("source_hash")
            else "source_changed_manual_breaking_review_required"
        ),
        "candidate_path": str(output_path.relative_to(args.repository_root)),
        "service_name": args.service_name,
        "odata_version": version,
        "current_registry_record": current,
        "candidate_registry_fields": {
            key: candidate.get(key)
            for key in ("service_name", "odata_version", "artifact_id", "artifact_version", "openapi_version", "catalog_source", "source_hash", "status")
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
