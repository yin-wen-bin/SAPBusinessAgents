from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


ODATA_VERSIONS = frozenset({"2.0", "4.0"})
_SERVICE = re.compile(r"^[A-Za-z0-9_]+(?:;v=[0-9]+)?$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class ODataCatalogError(ValueError):
    def __init__(self, message: str, *, code: str = "odata_catalog_invalid") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ODataServiceBinding:
    service_name: str
    odata_version: str
    service_root_path: str
    metadata_path: str
    artifact_id: str | None
    artifact_version: str | None
    openapi_version: str | None
    catalog_source: str
    source_hash: str
    status: str
    enabled: bool = True

    @property
    def key(self) -> tuple[str, str]:
        return (self.service_name, self.odata_version)

    def public_dict(self) -> dict[str, Any]:
        return {
            "service_name": self.service_name,
            "odata_version": self.odata_version,
            "artifact_id": self.artifact_id,
            "artifact_version": self.artifact_version,
            "openapi_version": self.openapi_version,
            "catalog_source": self.catalog_source,
            "source_hash": self.source_hash,
            "status": self.status,
            "enabled": self.enabled,
        }


class ODataServiceRegistry:
    """Validated service/version-to-relative-path bindings.

    The registry is the only component allowed to choose an OData service root.
    Query plans can identify a registered service and protocol version, but cannot
    provide URLs, paths, SAP clients, or credentials.
    """

    schema_version = "2.0"

    def __init__(self, bindings: list[ODataServiceBinding]) -> None:
        self._bindings: dict[tuple[str, str], ODataServiceBinding] = {}
        for binding in bindings:
            if binding.key in self._bindings:
                raise ODataCatalogError(
                    f"Duplicate OData service binding: {binding.key}",
                    code="odata_service_duplicate",
                )
            self._bindings[binding.key] = binding

    @classmethod
    def empty(cls) -> "ODataServiceRegistry":
        return cls([])

    @classmethod
    def load(cls, path: Path | None) -> "ODataServiceRegistry":
        if path is None or not path.is_file():
            return cls.empty()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ODataCatalogError(
                "OData service registry could not be read.",
                code="odata_registry_unreadable",
            ) from exc
        return cls.from_payload(payload)

    @classmethod
    def from_payload(cls, payload: Any) -> "ODataServiceRegistry":
        if not isinstance(payload, dict) or payload.get("schema_version") != cls.schema_version:
            raise ODataCatalogError(
                "OData service registry must use schema_version 2.0.",
                code="odata_registry_schema_unsupported",
            )
        raw_services = payload.get("services")
        if not isinstance(raw_services, list):
            raise ODataCatalogError("OData service registry services must be an array.")
        return cls([_parse_binding(item) for item in raw_services])

    def resolve(self, service_name: str, odata_version: str) -> ODataServiceBinding:
        version = normalize_odata_version(odata_version)
        binding = self._bindings.get((service_name, version))
        if binding is None:
            raise ODataCatalogError(
                f"OData service/version is not registered: {service_name} {version}",
                code="odata_service_unregistered",
            )
        if not binding.enabled or binding.status == "blocked":
            raise ODataCatalogError(
                f"OData service/version is disabled: {service_name} {version}",
                code="odata_service_disabled",
            )
        return binding

    def public_services(self) -> list[dict[str, Any]]:
        return [
            binding.public_dict()
            for binding in sorted(self._bindings.values(), key=lambda item: item.key)
        ]


def normalize_odata_version(value: Any) -> str:
    version = str(value or "").strip().lower()
    if version not in ODATA_VERSIONS:
        raise ODataCatalogError(
            "odata_version must be 2.0 or 4.0.",
            code="odata_version_unsupported" if version else "odata_version_required",
        )
    return version


def _parse_binding(value: Any) -> ODataServiceBinding:
    if not isinstance(value, dict):
        raise ODataCatalogError("Each OData service binding must be an object.")
    service_name = str(value.get("service_name") or "").strip()
    if not _SERVICE.fullmatch(service_name):
        raise ODataCatalogError(
            f"Invalid OData service name: {service_name}",
            code="invalid_service_name",
        )
    version = normalize_odata_version(value.get("odata_version"))
    root = _relative_path(value.get("service_root_path"), "service_root_path")
    metadata = _relative_path(value.get("metadata_path"), "metadata_path")
    expected_prefix = "/sap/opu/odata/sap/" if version == "2.0" else "/sap/opu/odata4/"
    if not root.startswith(expected_prefix):
        raise ODataCatalogError(
            f"{version} service_root_path must start with {expected_prefix}",
            code="odata_service_path_version_mismatch",
        )
    normalized_root = root.rstrip("/")
    if metadata != f"{normalized_root}/$metadata":
        raise ODataCatalogError(
            "metadata_path must be the registered service root followed by /$metadata.",
            code="odata_metadata_path_invalid",
        )
    source_hash = str(value.get("source_hash") or "").strip().lower()
    if not _SHA256.fullmatch(source_hash):
        raise ODataCatalogError(
            "source_hash must be a sha256: digest.",
            code="odata_source_hash_invalid",
        )
    source = str(value.get("catalog_source") or "").strip()
    if source not in {"sap_bah", "sapclaw_migration", "manual"}:
        raise ODataCatalogError(
            "catalog_source must be sap_bah, sapclaw_migration, or manual.",
            code="odata_catalog_source_invalid",
        )
    status = str(value.get("status") or "").strip()
    if status not in {"seed", "live_validated", "blocked"}:
        raise ODataCatalogError(
            "status must be seed, live_validated, or blocked.",
            code="odata_catalog_status_invalid",
        )
    return ODataServiceBinding(
        service_name=service_name,
        odata_version=version,
        service_root_path=normalized_root,
        metadata_path=metadata,
        artifact_id=_optional_text(value.get("artifact_id")),
        artifact_version=_optional_text(value.get("artifact_version")),
        openapi_version=_optional_text(value.get("openapi_version")),
        catalog_source=source,
        source_hash=source_hash,
        status=status,
        enabled=value.get("enabled") is not False,
    )


def _relative_path(value: Any, label: str) -> str:
    path = str(value or "").strip()
    parsed = urlsplit(path)
    decoded_path = unquote(path)
    if (
        not path.startswith("/")
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or ".." in path.split("/")
        or "\\" in path
        or decoded_path != path
        or any(segment in {".", ".."} for segment in decoded_path.split("/"))
    ):
        raise ODataCatalogError(
            f"{label} must be a safe relative SAP path.",
            code="odata_service_path_invalid",
        )
    return path


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
