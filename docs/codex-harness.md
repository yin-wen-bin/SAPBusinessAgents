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

## 多轮纠错会话

每个新自由查询同时建立一个 `free_query_session`。首轮、反馈轮和展示修订轮分别保存为不可变的运行记录，页面使用 `?session=<session_id>` 展示完整时间线。会话固定创建时的 Runtime、SDK版本、配置摘要和 Codex thread；切换全局Runtime不会迁移已有会话。

反馈先由Runtime产生结构化分类，再由平台执行确定性复核：筛选、字段、实体、业务键、关系、时间范围或证据要求发生变化时必须重新执行GET-only查询；只有排序、语言、表格或文案变化时才能引用同一会话内已验证的证据并生成新展示，此路径不会访问SAP。无法安全判断时默认重新查询；意图不清时进入`waiting_input`，不同业务问题要求创建新会话。

用户期望只作为候选断言与SAP证据比较，状态为`confirmed`、`mismatch`或`not_verifiable`，不得覆盖SAP事实、确定性规则或完整性。用户确认满意只记录接受的迭代和结果Digest；`source_complete`和`business_complete`保持原值。

## 能力隔离

App Server 子进程只直接加载仓库内的两个 MCP Server。启动参数按通用Allowlist禁用全部继承的MCP，再启用run-scoped SAP Broker与工具准入Gateway。子进程不直接获得Shell、文件修改、Computer Use、任意浏览器或宿主工作区写权限。

子进程环境会清除 SAP URL、Client、用户名、密码、证书路径和 `SAP_ADT_*`。SAP 查询只能提交注册的 `service_name`、显式 `odata_version`、实体和 GET-only 计划；原始连接信息留在 Embedded Provider 内。

动态工具只有在来源、版本、SHA-256、输入输出 Schema 和只读行为可验证，且无需新凭据时才可临时启用。外部 OpenAPI 工具只允许公共 HTTPS 443 的 GET/HEAD、禁止重定向、内网/RFC1918、本机和未声明端点。候选不会写入 Codex 全局配置，完整的不受信任 OpenAPI 文档也不会持久化。纯计算使用 AST allowlist 的 `safe_compute(language="python", code, inputs)`。

## 证据边界

只有 `sap_live` 和满足完整性契约的 `sap_skill` 可以支持 `customer_business_fact`。`web_reference` 与 `external_tool` 只能支持产品文档、业务语义或诊断。

API 不足时，`sap_evidence_assess` 只有在 Catalog、实时 Schema 和计划验证均已执行后，才会为 `config/skills.json` 中已注册、可用、`read_only=true`、`validated=true` 的 Skill 签发绑定 run、Skill 和精确输入哈希的单次 `gap_token`。非 ADT Skill 必须在签发前提交完整 `skill_input` 并通过其可信 JSON Schema；跨 run、跨 Skill、输入变化、过期或重复使用都会被拒绝。SAPBusinessAgents 不选择或传递 SAPSkillhub connection profile。`sap-adt-table-export` 继续使用固定的声明式 Schema：`schema_version=1`、`source_type`、对象、字段、有界过滤、可选且经实时 DDIC 证明的升序稳定键，以及 `max_rows<=30000`。

原始 SAP 结果只写入忽略目录 `.local-data/harness/<run>/evidence/`。模型只获得至多 200 行的标准化页面和 `evidence_ref`；OData `__metadata` 与内部 URL 不进入模型、事件或公开结果。

## API 与事件

沿用 `POST /api/runs`、`GET /api/runs/{id}`、SSE、`POST /input` 和 `POST /cancel`。自由查询创建响应增加`session_id`和`iteration`。多轮接口包括：

```text
POST /api/free-query-sessions
GET  /api/free-query-sessions/{session_id}
GET  /api/free-query-sessions/{session_id}/iterations/{iteration}
POST /api/free-query-sessions/{session_id}/feedback
POST /api/free-query-sessions/{session_id}/feedback-input
GET  /api/free-query-sessions/{session_id}/feedback-requests/{request_id}
GET  /api/free-query-sessions/{session_id}/feedback-requests/{request_id}/events
POST /api/free-query-sessions/{session_id}/feedback-requests/{request_id}/cancel
POST /api/free-query-sessions/{session_id}/accept
POST /api/free-query-sessions/{session_id}/reopen
POST /api/free-query-sessions/{session_id}/agent-draft
```

`RunResult.harness` 返回 runtime、protocol、thread、turn/tool/Web/工具发现计数及 stop reason。历史单轮运行仍可通过`POST /api/free-query-sessions`只读关联为新会话第一轮，不改写原运行。

反馈提交会立即返回持久化的`feedback_request_id`；Runtime分类在独立反馈Worker中执行，页面通过反馈事件流展示`feedback_received`、`feedback_review_started`、`feedback_decision_created`和`feedback_iteration_queued`等阶段。固定Agent/组合工作流、自由查询、反馈预审使用三个独立本机执行通道，因此长查询不会阻塞确定性任务。

公开运行事件包括 `harness_started`、`codex_turn_*`、`web_search_*`、`tool_discovery_*`、`tool_admission_*`、`external_tool_*`、平台Broker的`tool_requested/completed/failed`、App Server观察事件`agent_runtime_tool_*`、`query_revised`、`evidence_gap_assessed`、`harness_time_extended`、`harness_finalization_started`、`waiting_input`、`harness_completed/interrupted`。进度和工具调用计数只使用平台Broker事件，避免同一调用重复计数。事件不保存隐藏推理、原始 SAP 行、凭据、内部 URL或宿主路径。

## 配置

```dotenv
SAPBA_FREE_QUERY_RUNTIME=harness
SAPBA_INTERNAL_API_URL=http://127.0.0.1:8765
SAPBA_MAX_HARNESS_TURNS=12
SAPBA_MAX_FREE_QUERY_ITERATIONS=12
SAPBA_MAX_TOOL_CALLS=40
SAPBA_MAX_RUN_SECONDS=600
SAPBA_MAX_DETERMINISTIC_RUN_SECONDS=600
SAPBA_MAX_FREE_QUERY_SECONDS=1800
SAPBA_FREE_QUERY_INITIAL_SECONDS=900
SAPBA_FREE_QUERY_EXTENSION_SECONDS=300
SAPBA_FREE_QUERY_FINALIZATION_SECONDS=300
SAPBA_LOCAL_DETERMINISTIC_WORKERS=1
SAPBA_LOCAL_FREE_QUERY_WORKERS=1
SAPBA_LOCAL_FEEDBACK_WORKERS=1
SAPBA_MAX_CONCURRENT_SAP_GETS=2
SAPBA_SCHEDULER_LEASE_SECONDS=60
# SAPBA_CODEX_MODEL=
```

`SAPBA_MAX_TOOL_CALLS` 接受正整数形式的 run 级调用预算。设置为 `0` 时只取消
工具调用次数上限，轮次和运行时间限制仍然生效；负数和非整数会在启动时被拒绝。

自由查询使用30分钟绝对预算：前15分钟正常查询；第15和20分钟只有在新增已验证SAP证据、通过Schema校验的新计划或解决明确证据缺口时才各延长5分钟；最晚第25分钟关闭外部读取，保留最后5分钟整理报告。最终整理阶段拒绝Catalog、Schema、SAP GET及Skill请求。固定Agent和组合工作流仍使用10分钟确定性预算。SQLite调度器实现`enqueue/claim/heartbeat/complete/fail/cancel/recover`契约，未来可以替换为PostgreSQL或Redis队列而不改变运行API。

只有人工设置 `SAPBA_FREE_QUERY_RUNTIME=planner_legacy` 才使用旧 Planner；Harness 初始化或隔离失败时不自动降级。第一版不实现登录、RBAC、租户隔离、配额或审批界面，复用当前机器的 Codex 登录。
