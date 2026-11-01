"""Shared platform-owned SAP/Skill capability composition.

The application and bounded test tools use the same registry, configuration and
plugin checks. This module never accepts a URL or credentials from a tool call.
"""
from __future__ import annotations

from dataclasses import dataclass

from .plugins import PluginManager, SapReadCapability, SkillCapability, SkillhubPluginProvider, official_plugin_manifests
from .sap_read import EmbeddedODataProvider
from .skills import SkillRegistry


@dataclass
class ReadCapabilities:
    manager: PluginManager
    sap_read: SapReadCapability
    skills: SkillCapability
    skill_registry: SkillRegistry


def build_read_capabilities(settings, *, embedded_provider=None, skill_registry=None):
    registry = skill_registry or SkillRegistry(settings.skillhub_root, settings.repository_root / "config/skills.json")
    provider = embedded_provider or EmbeddedODataProvider(
        base_url=settings.sap_base_url, username=settings.sap_username, password=settings.sap_password,
        client=settings.sap_client, verify_ssl=settings.sap_verify_ssl, auth_type=settings.sap_auth_type,
        timeout_seconds=settings.sap_odata_timeout_seconds, max_results=settings.sap_max_results,
        page_size=settings.sap_page_size, max_concurrent_requests=settings.max_concurrent_sap_gets,
        relationship_catalog_path=settings.repository_root / "config/business-relationships.json",
        service_registry_path=settings.odata_service_registry_path, catalog_seed_path=settings.catalog_seed_path,
        curated_catalog_path=settings.repository_root / "config/catalog-curated-terms.json",
        normalization_catalog_path=settings.repository_root / "config/sap-value-normalization.json")
    manager = PluginManager(settings.plugin_manifest_root, settings.plugin_state_path, official_plugin_manifests(),
                            preferred_plugins={"sap_read.v2": "embedded-sap-odata"},
                            runtime_enabled={"embedded-sap-odata": True})
    manager.bind_provider("embedded-sap-odata", provider)
    manager.bind_provider("sapskillhub", SkillhubPluginProvider(registry))
    return ReadCapabilities(manager, SapReadCapability(manager), SkillCapability(manager), registry)
