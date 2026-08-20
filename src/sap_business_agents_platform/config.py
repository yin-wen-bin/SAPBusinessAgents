from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class Settings:
    repository_root: Path = REPOSITORY_ROOT
    data_root: Path = REPOSITORY_ROOT / ".local-data"
    draft_root: Path = REPOSITORY_ROOT / ".prototype" / "authoring"
    sap_base_url: str = ""
    sap_username: str = ""
    sap_password: str = ""
    sap_client: str = ""
    sap_verify_ssl: bool = True
    sap_auth_type: str = "basic"
    sap_odata_timeout_seconds: float = 60.0
    sap_max_results: int = 5000
    sap_page_size: int = 1000
    sap_env_file: Path | None = None
    skillhub_root: Path = Path(r"C:\Users\wenbi\Documents\SAPSkillhub")
    codex_model: str | None = None
    free_query_runtime: str = "harness"
    internal_api_url: str = "http://127.0.0.1:8765"
    max_harness_turns: int = 12
    # ``None`` means that run-scoped tool calls are bounded only by the turn and
    # elapsed-time limits.  Environment value ``0`` selects this mode.
    max_tool_calls: int | None = 40
    max_run_seconds: int = 600
    # Always true for the local product runtime. Tests and the in-process live
    # acceptance campaign may construct Settings with this disabled explicitly.
    enforce_agent_acceptance: bool = True

    @property
    def database_path(self) -> Path:
        return self.data_root / "platform.sqlite3"

    @property
    def plugin_manifest_root(self) -> Path:
        return self.repository_root / "config" / "plugins"

    @property
    def plugin_state_path(self) -> Path:
        return self.data_root / "plugins" / "registry.json"

    @property
    def odata_service_registry_path(self) -> Path:
        return self.repository_root / "config" / "odata-services.json"

    @property
    def catalog_seed_path(self) -> Path:
        return self.repository_root / "data" / "catalog-seed" / "catalog.json"

    @classmethod
    def from_env(cls, repository_root: Path | None = None) -> "Settings":
        root = (repository_root or REPOSITORY_ROOT).resolve()
        load_dotenv(root / ".env", override=False)
        configured_env_file = os.getenv("SAPBA_SAP_ENV_FILE", "").strip()
        if configured_env_file:
            candidate = Path(configured_env_file)
            sap_env_file = (candidate if candidate.is_absolute() else root / candidate).resolve()
        else:
            sap_env_file = None
        if sap_env_file and sap_env_file.is_file():
            # Transitional credential reuse only: values are loaded in process and never copied.
            load_dotenv(sap_env_file, override=False)
        data_root = Path(os.getenv("SAPBA_DATA_ROOT", str(root / ".local-data"))).resolve()
        draft_root = Path(os.getenv("SAPBA_DRAFT_ROOT", str(root / ".prototype" / "authoring"))).resolve()
        timeout_ms = max(1000, int(os.getenv("SAP_ODATA_TIMEOUT_MS", "60000")))
        return cls(
            repository_root=root,
            data_root=data_root,
            draft_root=draft_root,
            sap_base_url=(os.getenv("SAP_ODATA_BASE_URL") or os.getenv("SAP_BASE_URL", "")).rstrip("/"),
            sap_username=os.getenv("SAP_USERNAME", ""),
            sap_password=os.getenv("SAP_PASSWORD", ""),
            sap_client=os.getenv("SAP_CLIENT", ""),
            sap_verify_ssl=_env_bool("SAP_VERIFY_SSL", True),
            sap_auth_type=os.getenv("SAP_AUTH_TYPE", "basic"),
            sap_odata_timeout_seconds=timeout_ms / 1000,
            sap_max_results=max(1, int(os.getenv("SAPBA_SAP_MAX_RESULTS", "5000"))),
            sap_page_size=max(1, int(os.getenv("SAPBA_SAP_PAGE_SIZE", "1000"))),
            sap_env_file=sap_env_file,
            skillhub_root=Path(
                os.getenv("SAPSKILLHUB_ROOT", r"C:\Users\wenbi\Documents\SAPSkillhub")
            ).resolve(),
            codex_model=os.getenv("SAPBA_CODEX_MODEL") or None,
            free_query_runtime=_env_choice(
                "SAPBA_FREE_QUERY_RUNTIME", "harness", {"harness", "planner_legacy"}
            ),
            internal_api_url=os.getenv(
                "SAPBA_INTERNAL_API_URL", "http://127.0.0.1:8765"
            ).rstrip("/"),
            max_harness_turns=max(1, int(os.getenv("SAPBA_MAX_HARNESS_TURNS", "12"))),
            max_tool_calls=_env_optional_limit("SAPBA_MAX_TOOL_CALLS", 40),
            max_run_seconds=max(10, int(os.getenv("SAPBA_MAX_RUN_SECONDS", "600"))),
        )


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_choice(name: str, default: str, allowed: set[str]) -> str:
    value = os.getenv(name, default).strip().lower()
    if value not in allowed:
        raise ValueError(f"{name} must be one of: {', '.join(sorted(allowed))}")
    return value


def _env_optional_limit(name: str, default: int) -> int | None:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return None if value == 0 else value
