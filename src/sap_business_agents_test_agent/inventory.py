"""Source-backed feature registry; discovery performs no model or SAP calls."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .common import TestAgentError, digest, read_json, safe_path


FEATURES = (
    ("free-query", "自由查询", "ask", True,
     ["query", "clarification", "feedback", "report", "cancel", "recovery"],
     ["tests/test_free_query*.py", "tests/test_pending_free_query_draft.py"]),
    ("workflows", "工作流", "workflows", True,
     ["catalog", "compose", "edit", "gaps", "validation", "publish", "lifecycle"],
     ["tests/test_workflow*.py", "tests/test_user_workflow*.py"]),
    ("agent-management", "Agent 管理", "agent-management", True,
     ["catalog", "create", "edit", "sample", "trial", "acceptance", "publish", "lifecycle"],
     ["tests/test_agent_lifecycle.py", "tests/test_agent_authoring*.py", "tests/test_agent_acceptance*.py"]),
    ("fixed-agents", "固定业务 Agent", "", False,
     ["catalog", "inputs", "execute", "business-result", "evidence", "admission"],
     ["tests/test_platform_runtime.py", "tests/test_deterministic*.py"]),
    ("role-matching", "岗位匹配", "", False,
     ["material", "matching", "gaps", "feedback", "report"],
     ["tests/test_role_matching.py", "tests/test_role_consolidation*.py"]),
    ("plugins", "插件与连接", "plugins", False,
     ["catalog", "connection", "binding", "confirmation", "failure"],
     ["tests/test_plugins.py", "tests/test_integrations*.py", "tests/test_workflow_integration*.py"]),
    ("settings", "系统配置与 Runtime/SDK", "settings", False,
     ["sap", "runtime", "model", "authentication", "sdk-maintenance", "month-end-profile"],
     ["tests/test_settings*.py", "tests/test_sdk_manager.py", "tests/test_sdk_update.py", "tests/test_month_end_profile*.py"]),
    ("history", "查询历史与证据", "query-history", False,
     ["history", "restore", "download", "restricted-evidence"],
     ["tests/test_free_query_history.py", "tests/test_restricted_artifacts*.py"]),
)
SOURCE_ROOTS = {"src", "tests", "site", "agents", "workflows", "config", "data", "scripts", "skills", "docs"}
ROOT_FILES = {"pyproject.toml", "README.md", "SECURITY.md", ".gitignore", ".gitattributes"}
EXCLUDED = {".git", ".local-data", ".prototype", ".codex", ".agents", ".aws", "node_modules", ".venv", "dist", ".astro", "__pycache__", ".pytest_cache", "generated"}


def source_allowed(name):
    p = Path(name)
    return (p.parts[0] in SOURCE_ROOTS or name in ROOT_FILES) and not any(
        part in EXCLUDED or part.startswith(".env") or part in {"auth.json", "credentials.json", "__sapba_auto_test_target.json"}
        for part in p.parts
    ) and p.suffix.lower() not in {".pem", ".key", ".sqlite", ".sqlite3", ".db", ".pyc"}


def source_files(root):
    root = Path(root).resolve()
    result = subprocess.run(["git", "-c", "core.longpaths=true", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                            cwd=root, capture_output=True, timeout=30)
    if result.returncode:
        raise TestAgentError("repository_unavailable")
    values = {}
    for name in sorted(set(result.stdout.decode("utf-8").split("\0")) - {""}):
        if not source_allowed(name):
            continue
        path = safe_path(root, name)
        if path.is_file():
            if path.stat().st_size > 64 * 1024 * 1024:
                raise TestAgentError("source_file_too_large", path=name)
            values[name] = digest(path.read_bytes())
    return values


def discover(root):
    root = Path(root).resolve()
    files = source_files(root)
    features = []
    for key, title, page, default, operations, patterns in FEATURES:
        sources = [name for name in files if name == f"site/src/pages/[lang]/{page}.astro"] if page else []
        tests = sorted({p.relative_to(root).as_posix() for pattern in patterns for p in root.glob(pattern) if p.is_file()})
        children, issues = [], []
        catalog = "agents" if key == "fixed-agents" else "workflows" if key == "workflows" else "config/plugins" if key == "plugins" else None
        if catalog:
            pattern = "*/*/agent.json" if key == "fixed-agents" else "*/*/workflow.json" if key == "workflows" else "*.json"
            for path in sorted((root / catalog).glob(pattern)):
                name = path.relative_to(root).as_posix()
                sources.append(name)
                try:
                    value = read_json(path)
                    child_id = str(value.get("id") or value.get("slug") or value.get("plugin_id") or path.parent.name)
                    children.append({"id": child_id, "title": value.get("title") or value.get("name") or child_id,
                                     "version": value.get("version"), "source": name,
                                     "lifecycle": value.get("lifecycle"), "validation": value.get("validation")})
                except (ValueError, OSError, AttributeError):
                    issues.append({"code": "catalog_record_invalid", "source": name})
        if key == "role-matching":
            sources.extend(n for n in files if n.endswith("RoleMatchingAssistant.tsx"))
        features.append({"id": key, "title": title, "default": default, "operations": operations,
                         "sources": sorted(sources), "test_refs": tests, "children": children,
                         "adapter": "native-browser", "offline_adapter": "pytest", "discovery_issues": issues})
    known_pages = {x[2] for x in FEATURES} | {"index", "run"}
    unclassified = [n for n in files if n.startswith("site/src/pages/[lang]/") and n.count("/") == 4
                    and Path(n).stem not in known_pages]
    body = {"schema_version": 1, "repository": str(root), "source_digest": digest(files),
            "features": features, "unclassified_sources": unclassified}
    return {**body, "digest": digest(body)}
