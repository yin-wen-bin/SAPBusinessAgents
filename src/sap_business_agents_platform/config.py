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
    sapclaw_url: str = "http://127.0.0.1:8000"
    sapclaw_api_key: str = ""
    sap_read_provider: str = "embedded"
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
    max_tool_calls: int = 20
    max_run_seconds: int = 300

    @property
    def database_path(self) -> Path:
        return self.data_root / "platform.sqlite3"

    @property
    def plugin_manifest_root(self) -> Path:
        return self.repository_root / "config" / "plugins"

    @property
    def plugin_state_path(self) -> Path:
        return self.data_root / "plugins" / "registry.json"

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
        sap_read_provider = os.getenv("SAP_READ_PROVIDER", "embedded").strip().lower()
        if sap_read_provider not in {"embedded", "sapclaw"}:
            raise ValueError("SAP_READ_PROVIDER must be 'embedded' or 'sapclaw'")
        timeout_ms = max(1000, int(os.getenv("SAP_ODATA_TIMEOUT_MS", "60000")))
        return cls(
            repository_root=root,
            data_root=data_root,
            draft_root=draft_root,
            sapclaw_url=os.getenv("SAPCLAW_RUNTIME_URL", "http://127.0.0.1:8000").rstrip("/"),
            sapclaw_api_key=os.getenv("SAPCLAW_API_KEY", ""),
            sap_read_provider=sap_read_provider,
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
            max_tool_calls=max(1, int(os.getenv("SAPBA_MAX_TOOL_CALLS", "20"))),
            max_run_seconds=max(10, int(os.getenv("SAPBA_MAX_RUN_SECONDS", "300"))),
        )


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
