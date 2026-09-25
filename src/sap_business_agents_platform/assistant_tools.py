"""Task-scoped discovery and admission for platform-owned assistant tools.

This is a projection of existing tool contracts, not a second execution registry.
The caller's context is constructed by the platform; model arguments never select
an assistant kind, target, revision, or permission profile.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


CATALOG_VERSION = "1.0"

_SAP_READ = frozenset({
    "list_all_approved_skills", "sap_catalog_search", "sap_schema_get",
    "sap_query_validate", "sap_query_execute", "sap_evidence_read",
    "sap_evidence_assess", "sap_skill_execute", "sap_inventory_fifo_assess",
    "sap_month_end_status_assess", "sap_final_report_validate",
})
_CATALOG = frozenset({"tool_catalog_search", "tool_catalog_inspect"})
_PURPOSE: dict[str, tuple[str, str]] = {
    "sap_catalog_search": ("查找 SAP OData 服务", "Find SAP OData services"),
    "sap_schema_get": ("读取实时 OData 元数据", "Read live OData metadata"),
    "sap_query_validate": ("验证只读查询计划", "Validate a read-only query plan"),
    "sap_query_execute": ("执行有界的 SAP GET 查询", "Execute a bounded SAP GET query"),
    "sap_evidence_read": ("读取允许展示的证据页面", "Read a public evidence page"),
    "sap_evidence_assess": ("核对证据缺口和 Skill 资格", "Assess evidence gaps and Skill eligibility"),
    "sap_inventory_fifo_assess": ("核对已取证的库存 FIFO 评估", "Assess evidenced inventory FIFO"),
    "sap_month_end_status_assess": ("核对已取证的月结状态", "Assess evidenced month-end status"),
    "sap_skill_execute": ("执行获批只读 Skill", "Run an approved read-only Skill"),
    "list_all_approved_skills": ("查看获批 Skill 契约", "Inspect approved Skill contracts"),
    "sap_final_report_validate": ("校验业务报告证据", "Validate report evidence"),
    "sap_ddic_field_labels_get": ("核对 DDIC 字段中英文标签", "Verify bilingual DDIC field labels"),
    "tool_catalog_search": ("查找当前任务的工具", "Find task-relevant tools"),
    "tool_catalog_inspect": ("查看工具资格和契约", "Inspect tool eligibility and contract"),
    "safe_compute": ("对已获证据执行受限计算", "Perform bounded calculations over evidence"),
    "tool_discovery_search": ("发现受控外部只读工具", "Discover admitted external read tools"),
    "tool_discovery_inspect": ("查看外部工具的安全契约", "Inspect an external tool safety contract"),
    "tool_discovery_activate": ("激活本轮已审核的外部工具", "Activate an admitted external tool for this run"),
    "external_tool_execute": ("执行已激活的外部只读工具", "Execute an activated external read tool"),
    "mail.v1/search": ("搜索已绑定邮箱", "Search a bound mailbox"),
    "mail.v1/read": ("读取已绑定邮箱邮件", "Read a bound mailbox message"),
    "mail.v1/draft": ("准备本地邮件草稿", "Prepare a local mail draft"),
    "mail.v1/send": ("发送需逐封确认的邮件", "Send mail after per-message confirmation"),
    "agent.publish": ("准备 Agent 发布", "Prepare Agent publication"),
    "agent.activate": ("准备 Agent 启用", "Prepare Agent activation"),
}
_HUMAN_ONLY = frozenset({"mail.v1/send", "agent.publish", "agent.activate"})
_ASSISTANT_ALLOWED: dict[str, frozenset[str]] = {
    "free_query": _SAP_READ | _CATALOG | {"safe_compute", "tool_discovery_search", "tool_discovery_inspect", "tool_discovery_activate", "external_tool_execute"},
    "acceptance_free_query": _SAP_READ | _CATALOG | {"safe_compute", "tool_discovery_search", "tool_discovery_inspect", "tool_discovery_activate", "external_tool_execute"},
    "acceptance_baseline": _SAP_READ | _CATALOG,
    # The sample-discovery scope is further narrowed by its persisted draft
    # contract. Discovery never overrides that per-plan gate.
    "sample_discovery": _SAP_READ | _CATALOG,
    "agent_authoring": _CATALOG | {"sap_ddic_field_labels_get"},
    "workflow_authoring": _CATALOG,
    "role_matching": _CATALOG,
}


@dataclass(frozen=True, slots=True)
class AssistantToolContext:
    kind: str
    request_digest: str
    target_id: str
    revision: int | None
    expires_at: datetime
    allowed_ddic_fields: tuple[tuple[str, str], ...] = ()

    def valid(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        return bool(self.request_digest and self.target_id) and current < self.expires_at


def permitted(context: AssistantToolContext, tool_id: str, *, now: datetime | None = None) -> tuple[bool, str | None]:
    if not context.valid(now):
        return False, "tool_session_expired"
    if tool_id in _HUMAN_ONLY:
        return False, "user_confirmation_required"
    if tool_id == "sap_ddic_field_labels_get" and not context.allowed_ddic_fields:
        return False, "ddic_field_scope_required"
    if tool_id not in _ASSISTANT_ALLOWED.get(context.kind, frozenset()):
        return False, "tool_not_enabled_for_task"
    return True, None


def inspect(context: AssistantToolContext, tool_id: str, **kwargs: Any) -> dict[str, Any] | None:
    return next((item for item in catalog(context, limit=50, **kwargs) if item["tool_id"] == tool_id), None)


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def contract_digest(tool_id: str) -> str | None:
    from .mcp_server import _SAP_TOOLS, _TOOL_TOOLS, _AUTHORING_TOOLS
    for item in (*_SAP_TOOLS, *_TOOL_TOOLS, *_AUTHORING_TOOLS):
        if item["name"] == tool_id:
            return _digest({"catalog_version": CATALOG_VERSION, "input_schema": item["inputSchema"]})
    return None


def contract_snapshot(kind: str) -> dict[str, str]:
    return {tool_id: digest for tool_id in _ASSISTANT_ALLOWED.get(kind, ())
            if (digest := contract_digest(tool_id)) is not None}


def catalog(
    context: AssistantToolContext,
    *,
    approved_skills: list[dict[str, Any]] | None = None,
    mail_bindings: list[dict[str, Any]] | None = None,
    query: str = "",
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Return relevant public descriptors; this performs no external reads."""
    from .mcp_server import _SAP_TOOLS, _TOOL_TOOLS, _AUTHORING_TOOLS

    bindings = [item for item in mail_bindings or [] if isinstance(item, dict)]
    approved_ids = {str(item.get("skill_id")) for item in approved_skills or []}
    entries: dict[str, dict[str, Any]] = {}
    for item in (*_SAP_TOOLS, *_TOOL_TOOLS, *_AUTHORING_TOOLS):
        tool_id = str(item["name"])
        schema = item["inputSchema"]
        effect = ("sap_read" if tool_id.startswith("sap_") else
                  "external_read" if tool_id == "external_tool_execute" else
                  "session_write" if tool_id == "tool_discovery_activate" else
                  "local_compute" if tool_id == "safe_compute" else "local_read")
        entries[tool_id] = {
            "tool_id": tool_id,
            "purpose": dict(zip(("zh", "en"), _PURPOSE.get(tool_id, (item["description"], item["description"])))),
            "effect": effect,
            "contract_digest": _digest({"catalog_version": CATALOG_VERSION, "input_schema": schema}),
            "source": "platform_broker",
            # Catalog lookup is offline. A permitted read still has to pass
            # Provider/connection validation at execution time.
            "connection_status": "not_checked" if tool_id.startswith("sap_") else "not_required",
        }
    for skill in approved_skills or []:
        skill_id = str(skill.get("skill_id") or "")
        if not skill_id:
            continue
        description = skill.get("description") or {}
        entries[f"skill:{skill_id}"] = {
            "tool_id": f"skill:{skill_id}",
            "purpose": {"zh": str(description.get("zh") or skill_id), "en": str(description.get("en") or skill_id)},
            "effect": "sap_read",
            "contract_digest": str(skill.get("input_schema_hash") or skill.get("schema_hash") or _digest(skill.get("input_schema") or {})),
            "source": "approved_skill_catalog",
        }
    for operation in ("search", "read", "draft", "send"):
        tool_id = f"mail.v1/{operation}"
        matches = [item for item in bindings if item.get("capability") == "mail.v1" and item.get("operation") == operation]
        entries[tool_id] = {
            "tool_id": tool_id,
            "purpose": dict(zip(("zh", "en"), _PURPOSE[tool_id])),
            "effect": "external_write" if operation == "send" else "mail_read" if operation in {"search", "read"} else "local_write",
            "contract_digest": _digest([item.get("schema_hash") for item in matches]),
            "source": "mail_binding_catalog",
            "binding_ready": any(item.get("enabled") and item.get("connection_enabled") and item.get("connection_status") == "ready" for item in matches),
        }
    for tool_id in ("agent.publish", "agent.activate"):
        entries[tool_id] = {
            "tool_id": tool_id, "purpose": dict(zip(("zh", "en"), _PURPOSE[tool_id])),
            "effect": "publication", "contract_digest": _digest(tool_id), "source": "agent_lifecycle",
        }
    terms = [part.casefold() for part in query.split() if part]
    result: list[dict[str, Any]] = []
    for item in entries.values():
        tool_id = item["tool_id"]
        # The independent baseline has no reason to learn about non-SAP tools.
        if context.kind == "acceptance_baseline" and tool_id not in _ASSISTANT_ALLOWED["acceptance_baseline"]:
            continue
        if terms and not all(term in (tool_id + " " + item["purpose"]["zh"] + " " + item["purpose"]["en"]).casefold() for term in terms):
            continue
        allowed, reason = permitted(context, tool_id)
        if tool_id.startswith("skill:"):
            allowed, reason = False, "skill_requires_broker_gate"
        if tool_id == "sap_skill_execute" and allowed:
            allowed, reason = False, "skill_requires_evidence_gap_token"
        if tool_id == "sap_ddic_field_labels_get" and "sap-adt-table-export" not in approved_ids:
            allowed, reason = False, "ddic_skill_unavailable"
        if tool_id.startswith("mail.v1/"):
            ready = item.pop("binding_ready")
            allowed, reason = False, "user_confirmation_required" if tool_id == "mail.v1/send" else "mail_workflow_binding_required" if not ready else "mail_workflow_only"
        result.append({**item, "available": allowed, "unavailable_reason": reason})
        if len(result) >= max(1, min(limit, 50)):
            break
    return result
