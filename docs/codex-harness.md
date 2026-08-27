# Codex Harness 自由查询原型

“直接询问 SAP”默认由持久 Codex App Server thread 执行；固定 Agent 和已发布工作流仍走现有确定性执行链。Embedded SAP Read Provider是唯一OData执行通道；扩展证据仅使用批准的只读Skill。

```text
UI / POST /api/runs
        -> CodexHarnessController
        -> Codex App Server (--search)
             -> SAP Tool Broker MCP
             -> Tool Discovery & Admission MCP
             -> Native Web Search
```

## Harness 能力

- 每个自由查询 run 保存 Codex `thread_id`、turn、搜索记录、工具候选、准入结果、工具调用和证据引用；进程重启后恢复同一 thread 及已准入的 run-scoped 候选。
- 活动 turn 的 `/input` 使用 steer；等待输入后继续原 thread；`/cancel` 调用 interrupt。
- Native Web Search 可研究公开产品文档、业务语义、Schema 漂移和候选工具。网络内容始终是不受信任数据，不能证明客户 SAP 业务事实。
- SAP Broker 提供 Catalog、实时 `$metadata`、计划验证、GET-only 查询、证据读取/评估、受控 Skill 和最终报告验证。
- 查询结果可被观察并修订；相同工具与请求摘要会幂等复用，不重复访问 SAP。
- 默认限制为 12 turn、40 次平台工具调用和 600 秒。超限输出 `INCONCLUSIVE`，不会自动回退到旧 Planner。

## 能力隔离

App Server 子进程只直接加载仓库内的两个 MCP Server。启动参数按通用Allowlist禁用全部继承的MCP，再启用run-scoped SAP Broker与工具准入Gateway。子进程不直接获得Shell、文件修改、Computer Use、任意浏览器或宿主工作区写权限。

子进程环境会清除 SAP URL、Client、用户名、密码、证书路径和 `SAP_ADT_*`。SAP 查询只能提交注册的 `service_name`、显式 `odata_version`、实体和 GET-only 计划；原始连接信息留在 Embedded Provider 内。

动态工具只有在来源、版本、SHA-256、输入输出 Schema 和只读行为可验证，且无需新凭据时才可临时启用。外部 OpenAPI 工具只允许公共 HTTPS 443 的 GET/HEAD、禁止重定向、内网/RFC1918、本机和未声明端点。候选不会写入 Codex 全局配置，完整的不受信任 OpenAPI 文档也不会持久化。纯计算使用 AST allowlist 的 `safe_compute(language="python", code, inputs)`。

## 证据边界

只有 `sap_live` 和满足完整性契约的 `sap_skill` 可以支持 `customer_business_fact`。`web_reference` 与 `external_tool` 只能支持产品文档、业务语义或诊断。

API 不足时，`sap_evidence_assess` 只有在 Catalog、实时 Schema 和计划验证均已执行后，才会为 `config/skills.json` 中已注册、可用、`read_only=true`、`validated=true` 的 Skill 签发绑定 run、Skill 和精确输入哈希的单次 `gap_token`。非 ADT Skill 必须在签发前提交完整 `skill_input` 并通过其可信 JSON Schema；跨 run、跨 Skill、输入变化、过期或重复使用都会被拒绝。SAPBusinessAgents 不选择或传递 SAPSkillhub connection profile。`sap-adt-table-export` 继续使用固定的声明式 Schema：`schema_version=1`、`source_type`、对象、字段、有界过滤、可选且经实时 DDIC 证明的升序稳定键，以及 `max_rows<=30000`。

原始 SAP 结果只写入忽略目录 `.local-data/harness/<run>/evidence/`。模型只获得至多 200 行的标准化页面和 `evidence_ref`；OData `__metadata` 与内部 URL 不进入模型、事件或公开结果。

## API 与事件

沿用 `POST /api/runs`、`GET /api/runs/{id}`、SSE、`POST /input` 和 `POST /cancel`。`RunResult.harness` 返回 runtime、protocol、thread、turn/tool/Web/工具发现计数及 stop reason。

公开事件包括 `harness_started`、`codex_turn_*`、`web_search_*`、`tool_discovery_*`、`tool_admission_*`、`external_tool_*`、`tool_requested/completed/failed`、`query_revised`、`evidence_gap_assessed`、`waiting_input`、`harness_completed/interrupted`。事件不保存隐藏推理、原始 SAP 行、凭据、内部 URL或宿主路径。

## 配置

```dotenv
SAPBA_FREE_QUERY_RUNTIME=harness
SAPBA_INTERNAL_API_URL=http://127.0.0.1:8765
SAPBA_MAX_HARNESS_TURNS=12
SAPBA_MAX_TOOL_CALLS=40
SAPBA_MAX_RUN_SECONDS=600
# SAPBA_CODEX_MODEL=
```

`SAPBA_MAX_TOOL_CALLS` 接受正整数形式的 run 级调用预算。设置为 `0` 时只取消
工具调用次数上限，轮次和运行时间限制仍然生效；负数和非整数会在启动时被拒绝。

只有人工设置 `SAPBA_FREE_QUERY_RUNTIME=planner_legacy` 才使用旧 Planner；Harness 初始化或隔离失败时不自动降级。第一版不实现登录、RBAC、租户隔离、配额或审批界面，复用当前机器的 Codex 登录。
