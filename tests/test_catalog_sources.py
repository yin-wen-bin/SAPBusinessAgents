from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from scripts.import_sapclaw_catalog import build_outputs, json_bytes, sha256_bytes
from scripts.sync_sap_bah_catalog import detected_odata_version, load_spec, normalize
from sap_business_agents_platform.sap_read.odata_catalog import (
    ODataCatalogError,
    ODataServiceRegistry,
)
from sap_business_agents_platform.sap_read import EmbeddedODataProvider


ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _snapshot_fixture(root: Path) -> None:
    index = root / "data" / "index" / "API_FIXTURE_SRV"
    _write_json(
        index / "services.json",
        [
            {
                "service_name": "API_FIXTURE_SRV",
                "base_path": "/sap/opu/odata/sap/API_FIXTURE_SRV",
                "description": "Read product data. POST creates a record. https://private.invalid",
                "business_aliases": ["库存", "inventory"],
            }
        ],
    )
    _write_json(
        index / "entities.json",
        [
            {
                "entity_set": "A_Product",
                "entity_type": "Product",
                "supported_methods": ["GET", "POST"],
                "description": "Read product stock.",
                "key_fields": ["Product"],
                "business_aliases": ["物料", "material"],
            },
            {
                "entity_set": "WriteOnly",
                "supported_methods": ["POST"],
            },
        ],
    )
    _write_json(
        index / "fields.json",
        [
            {
                "entity_set": "A_Product",
                "field_name": "Product",
                "data_type": "Edm.String",
                "label": "Material",
                "business_aliases": ["物料", "material"],
                "is_key": True,
            }
        ],
    )
    _write_json(
        index / "business_terms.json",
        [
            {
                "term": "库存",
                "mapped_entity_set": "A_Product",
                "mapped_fields": ["Product", "Missing"],
                "synonyms": ["inventory"],
                "confidence": 3,
            }
        ],
    )
    _write_json(index / "relations.json", [{"unsafe": "inferred"}])
    _write_json(index / "lookup_paths.json", [{"unsafe": "inferred"}])
    skill = root / "data" / "api_skills" / "API_FIXTURE_SRV" / "skill.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(
        "# Fixture\n\n## Purpose\n- Read inventory data.\n- POST a new row.\n\n## Pitfalls\n- Live metadata is authoritative.\n",
        encoding="utf-8",
    )
    (root / "LICENSE").write_text("MIT License\nCopyright SAPClaw contributors", encoding="utf-8")


def test_one_time_snapshot_import_is_deterministic_get_only_and_quarantines_graphs(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    repository = tmp_path / "repository"
    (repository / "agents").mkdir(parents=True)
    _snapshot_fixture(source)
    first = build_outputs(source, repository, "2026-08-19T00:00:00+00:00")
    second = build_outputs(source, repository, "2026-08-19T00:00:00+00:00")
    assert first == second
    catalog, registry, report = first
    service = catalog["services"][0]
    assert service["odata_version"] == "2.0"
    assert service["supported_operations"] == ["GET"]
    assert [item["entity_set"] for item in service["entities"]] == ["A_Product"]
    assert service["business_terms"][0]["fields"] == ["Product"]
    assert service["business_terms"][0]["confidence"] == 1.0
    encoded = json.dumps(catalog, ensure_ascii=False).lower()
    assert "private.invalid" not in encoded
    assert "post a new row" not in encoded
    assert report["quarantined"][0]["relations"] == 1
    assert report["quarantined"][0]["lookup_paths"] == 1
    assert report["privacy_scan"]["passed"] is True
    assert report["license"]["spdx"] == "MIT"
    assert all(item["sha256"].startswith("sha256:") for item in report["source_files"])
    assert report["output_hashes"]["data/catalog-seed/catalog.json"] == sha256_bytes(
        json_bytes(catalog)
    )
    assert registry["services"][0]["catalog_source"] == "sapclaw_migration"


def test_bah_normalizer_accepts_swagger2_but_publishes_get_only() -> None:
    spec = {
        "swagger": "2.0",
        "x-sap-odata-version": "2.0",
        "paths": {
            "/A_Product": {
                "get": {
                    "operationId": "listProducts",
                    "summary": "Read products. POST creates one. See https://private.invalid",
                },
                "post": {"operationId": "createProduct"},
                "delete": {"operationId": "deleteProduct"},
            }
        },
        "definitions": {
            "Product": {
                "properties": {
                    "Product": {"type": "string", "description": "Material number"}
                }
            }
        },
    }
    candidate = normalize(
        spec,
        service_name="API_PRODUCT_SRV",
        artifact="FIXTURE",
        artifact_version="0002",
        odata_version="2.0",
    )
    assert candidate["openapi_version"] == "2.0"
    assert candidate["supported_operations"] == ["GET"]
    assert [item["method"] for item in candidate["operations"]] == ["GET"]
    assert candidate["rejected_write_operation_count"] == 2
    assert candidate["operations"][0]["summary"] == "Read products."
    assert candidate["source_hash"].startswith("sha256:")
    assert candidate["normalized_hash"].startswith("sha256:")
    assert candidate["schemas"][0]["fields"][0]["field_name"] == "Product"


def test_bah_edmx_detects_v4_without_inferring_from_service_name(tmp_path: Path) -> None:
    spec: dict[str, object] = {"openapi": "3.0.1", "paths": {}}
    (tmp_path / "edmx.xml").write_text(
        '<edmx:Edmx xmlns:edmx="urn:edmx" Version="4.0" />', encoding="utf-8"
    )
    assert detected_odata_version(spec, tmp_path) == "4.0"
    assert detected_odata_version(spec, tmp_path / "missing") is None


def test_bah_yaml_spec_is_loaded_when_json_is_absent(tmp_path: Path) -> None:
    (tmp_path / "yaml.yaml").write_text(
        "openapi: 3.0.1\npaths:\n  /A_Product:\n    get:\n      operationId: listProducts\n",
        encoding="utf-8",
    )
    spec, source_format = load_spec(tmp_path)
    assert source_format == "yaml"
    assert spec["openapi"] == "3.0.1"


def test_service_registry_rejects_urls_path_version_conflicts_and_unknown_versions() -> None:
    base = {
        "service_name": "API_TEST_SRV",
        "odata_version": "2.0",
        "service_root_path": "/sap/opu/odata/sap/API_TEST_SRV",
        "metadata_path": "/sap/opu/odata/sap/API_TEST_SRV/$metadata",
        "catalog_source": "manual",
        "source_hash": f"sha256:{'b' * 64}",
        "status": "seed",
    }
    registry = ODataServiceRegistry.from_payload(
        {"schema_version": "2.0", "services": [base]}
    )
    assert registry.resolve("API_TEST_SRV", "2.0").odata_version == "2.0"
    assert "service_root_path" not in registry.public_services()[0]
    assert "metadata_path" not in registry.public_services()[0]
    with pytest.raises(ODataCatalogError) as missing:
        registry.resolve("API_TEST_SRV", "4.0")
    assert missing.value.code == "odata_service_unregistered"
    for update, code in (
        ({"service_root_path": "https://sap.invalid/root"}, "odata_service_path_invalid"),
        ({"service_root_path": "/sap/opu/odata/sap/API_TEST_SRV/%2e%2e/OTHER"}, "odata_service_path_invalid"),
        ({"odata_version": "4.0"}, "odata_service_path_version_mismatch"),
        ({"odata_version": "3.0"}, "odata_version_unsupported"),
    ):
        with pytest.raises(ODataCatalogError) as caught:
            ODataServiceRegistry.from_payload(
                {"schema_version": "2.0", "services": [{**base, **update}]}
            )
        assert caught.value.code == code


def test_curated_bilingual_inventory_document_and_mrp_search_rank_expected_candidates() -> None:
    provider = EmbeddedODataProvider(
        base_url="",
        username="",
        password="",
        service_registry_path=ROOT / "config" / "odata-services.json",
        catalog_seed_path=ROOT / "data" / "catalog-seed" / "catalog.json",
        curated_catalog_path=ROOT / "config" / "catalog-curated-terms.json",
    )

    async def search(query: str) -> dict[str, object]:
        return await provider.catalog(query=query, limit=3)

    cases = {
        "库存余额": ("API_MATERIAL_STOCK_SRV", "A_MatlStkInAcctMod"),
        "material document": ("API_MATERIAL_DOCUMENT_SRV", "A_MaterialDocumentHeader"),
        "物料需求计划": ("API_MRP_MATERIALS_SRV_01", "A_MRPMaterial"),
        "MRP supply demand": ("API_MRP_MATERIALS_SRV_01", "SupplyDemandItems"),
        "supplier quotation item": ("API_QTN_PROCESS_SRV", "A_SupplierQuotationItem"),
        "planned order": ("API_PLANNED_ORDERS", "A_PlannedOrder"),
    }
    for query, expected in cases.items():
        result = asyncio.run(search(query))
        first = result["data"]["items"][0]  # type: ignore[index]
        assert (first["service_name"], first["entity_set"]) == expected
        assert first["odata_version"] == "2.0"
        assert first["schema_authority"] == "live_metadata_required_before_execution"
    empty = asyncio.run(search("definitely-no-such-sap-term-xyz"))
    assert empty["data"]["items"] == []  # type: ignore[index]


def test_committed_catalog_sha256_manifest_matches_every_generated_output() -> None:
    manifest = json.loads(
        (ROOT / "data" / "catalog-seed" / "sha256-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["algorithm"] == "SHA-256"
    for relative, expected in manifest["files"].items():
        assert sha256_bytes((ROOT / relative).read_bytes()) == expected
