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
    max_free_query_iterations: int = 12
    max_workflow_conversation_turns: int = 12
    max_role_matching_turns: int = 12
    role_matching_max_files: int = 500
    role_matching_max_file_bytes: int = 50 * 1024 * 1024
    role_matching_max_total_bytes: int = 1024 * 1024 * 1024
    role_matching_max_runtime_chars: int = 1_500_000
    local_role_matching_workers: int = 1
    # ``None`` means that run-scoped tool calls are bounded only by the turn and
    # elapsed-time limits.  Environment value ``0`` selects this mode.
    max_tool_calls: int | None = 40
    # Backward-compatible shared limit used by tests and older deployments.
    # New deployments set mode-specific limits below.
    max_run_seconds: int = 600
    max_deterministic_run_seconds: int | None = None
    max_free_query_seconds: int | None = None
    free_query_initial_seconds: int | None = None
    free_query_extension_seconds: int | None = None
    free_query_finalization_seconds: int | None = None
    local_deterministic_workers: int = 1
    local_free_query_workers: int = 1
    local_feedback_workers: int = 1
    max_concurrent_sap_gets: int = 2
    scheduler_lease_seconds: int = 60
    # Always true for the local product runtime. Tests and the in-process live
    # acceptance campaign may construct Settings with this disabled explicitly.
    enforce_agent_acceptance: bool = True

    @property
    def database_path(self) -> Path:
        return self.data_root / "platform.sqlite3"

    @property
    def deterministic_run_seconds(self) -> int:
        return max(1, int(self.max_deterministic_run_seconds or self.max_run_seconds))

    @property
    def free_query_run_seconds(self) -> int:
        return max(1, int(self.max_free_query_seconds or self.max_run_seconds))

    @property
    def free_query_finalization_budget_seconds(self) -> int:
        hard = self.free_query_run_seconds
        configured = self.free_query_finalization_seconds
        return min(max(1, int(configured if configured is not None else max(1, hard // 6))), max(1, hard - 1))

    @property
    def free_query_initial_budget_seconds(self) -> int:
        hard = self.free_query_run_seconds
        finalization = self.free_query_finalization_budget_seconds
        configured = self.free_query_initial_seconds
        default = max(1, hard - finalization)
        return min(max(1, int(configured if configured is not None else default)), max(1, hard - finalization))

    @property
    def free_query_extension_budget_seconds(self) -> int:
        hard = self.free_query_run_seconds
        initial = self.free_query_initial_budget_seconds
        finalization = self.free_query_finalization_budget_seconds
        available = max(0, hard - initial - finalization)
        configured = self.free_query_extension_seconds
        default = max(1, available // 2) if available else 1
        return max(1, min(int(configured if configured is not None else default), max(1, available)))

    @property
    def plugin_manifest_root(self) -> Path:
        return self.repository_root / "config" / "plugins"

    @property
    def plugin_state_path(self) -> Path:
        return self.data_root / "plugins" / "registry.json"

    @property
    def sdk_runtime_state_path(self) -> Path:
        return self.data_root / "sdk-runtimes" / "default.json"

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
            max_free_query_iterations=max(
                1, int(os.getenv("SAPBA_MAX_FREE_QUERY_ITERATIONS", "12"))
            ),
            max_workflow_conversation_turns=max(
                1, int(os.getenv("SAPBA_MAX_WORKFLOW_CONVERSATION_TURNS", "12"))
            ),
            max_role_matching_turns=max(
                1, int(os.getenv("SAPBA_MAX_ROLE_MATCHING_TURNS", "12"))
            ),
            role_matching_max_files=max(
                1, int(os.getenv("SAPBA_ROLE_MATCHING_MAX_FILES", "500"))
            ),
            role_matching_max_file_bytes=max(
                1, int(os.getenv("SAPBA_ROLE_MATCHING_MAX_FILE_MB", "50"))
            ) * 1024 * 1024,
            role_matching_max_total_bytes=max(
                1, int(os.getenv("SAPBA_ROLE_MATCHING_MAX_TOTAL_MB", "1024"))
            ) * 1024 * 1024,
            role_matching_max_runtime_chars=max(
                10_000,
                int(os.getenv("SAPBA_ROLE_MATCHING_MAX_RUNTIME_CHARS", "1500000")),
            ),
            max_tool_calls=_env_optional_limit("SAPBA_MAX_TOOL_CALLS", 40),
            max_run_seconds=max(10, int(os.getenv("SAPBA_MAX_RUN_SECONDS", "600"))),
            max_deterministic_run_seconds=max(
                10,
                int(
                    os.getenv(
                        "SAPBA_MAX_DETERMINISTIC_RUN_SECONDS",
                        os.getenv("SAPBA_MAX_RUN_SECONDS", "600"),
                    )
                ),
            ),
            max_free_query_seconds=max(
                60, int(os.getenv("SAPBA_MAX_FREE_QUERY_SECONDS", "1800"))
            ),
            free_query_initial_seconds=max(
                10, int(os.getenv("SAPBA_FREE_QUERY_INITIAL_SECONDS", "900"))
            ),
            free_query_extension_seconds=max(
                1, int(os.getenv("SAPBA_FREE_QUERY_EXTENSION_SECONDS", "300"))
            ),
            free_query_finalization_seconds=max(
                10, int(os.getenv("SAPBA_FREE_QUERY_FINALIZATION_SECONDS", "300"))
            ),
            local_deterministic_workers=max(
                1, int(os.getenv("SAPBA_LOCAL_DETERMINISTIC_WORKERS", "1"))
            ),
            local_free_query_workers=max(
                1, int(os.getenv("SAPBA_LOCAL_FREE_QUERY_WORKERS", "1"))
            ),
            local_feedback_workers=max(
                1, int(os.getenv("SAPBA_LOCAL_FEEDBACK_WORKERS", "1"))
            ),
            local_role_matching_workers=max(
                1, int(os.getenv("SAPBA_LOCAL_ROLE_MATCHING_WORKERS", "1"))
            ),
            max_concurrent_sap_gets=max(
                1, int(os.getenv("SAPBA_MAX_CONCURRENT_SAP_GETS", "2"))
            ),
            scheduler_lease_seconds=max(
                15, int(os.getenv("SAPBA_SCHEDULER_LEASE_SECONDS", "60"))
            ),
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
