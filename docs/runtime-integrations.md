# 多运行时插件、连接器与工作流集成

## 设计边界

平台把两个职责分开处理：

- Agent Runtime 理解用户需求、选择仓库中的固定 Agent，并提出工作流草稿。
- Integration Backend 发现 Runtime App 或 MCP Server、维护连接状态，并调用一个已经固定的原生工具。

已发布工作流在 `integrationInputs` 和 `outputActions` 中保存
`connectionId`、`integrationBackendId`、原生 server/tool、schema hash 与绑定快照。
修改默认 Agent Runtime 不会改变这些绑定；迁移连接器必须生成并重新发布工作流修订。

Runtime 能力在 `config/sdks.json` 的 schema v3 `integrationRuntime` 中声明。
schema v2 仍可读取，但会按“不支持外部集成”处理。当前状态为：

- Codex：`codex-app-server`，支持 App/MCP 目录、状态、OAuth、配置与精确工具调用。
- WorkBuddy：`workbuddy-mcp`，仅展示已声明的发现与状态能力；直接调用失败关闭。
- DeepSeek Harness、Claude Agent：保留适配位置并返回结构化 blocker。

凭据所有者始终为 Runtime。平台不保存 OAuth token、刷新令牌或邮箱密码。
Codex MCP 继续配置在用户级 `~/.codex/config.toml`，或可信项目的
`.codex/config.toml`；平台只读取 App Server 返回的有效状态并触发认证流程。

## 插件与连接 API

原有 `/api/plugins` 和 `/api/capabilities` 保持不变。统一目录新增：

- `GET /api/plugins/catalog`
- `POST /api/plugins/catalog/refresh`
- `GET /api/plugins/runtime-adapters`
- `GET /api/plugins/connections`
- `POST /api/plugins/catalog/{catalog_id}/connect`
- `POST /api/plugins/connections/{connection_id}/refresh`
- `PUT /api/plugins/connections/{connection_id}/enabled`
- `GET /api/plugins/connections/{connection_id}/bindings`
- `PUT /api/plugins/connections/{connection_id}/bindings/{capability}/{operation}`

目录 ID 包含 Integration Backend、来源类型和原生 ID。相同服务在不同 Runtime
中不会自动合并，防止不同账号或不同工具契约被误认为同一连接。

Codex App 未安装时，连接接口只返回 App Server 提供的 `installUrl`；平台不调用
开发中的安装 RPC。MCP 需要登录时，连接接口返回 App Server OAuth URL，前端打开
该 URL 后通过连接刷新接口轮询状态。

## 邮件能力

首期规范能力为 `mail.v1/search`、`mail.v1/read`、平台本地 `draft` 和
`mail.v1/send`。管理员在“插件与连接”页把每个规范操作绑定到一个精确的 MCP
工具。绑定保存原生输入输出 schema hash；schema 变化会阻止运行。

`search` 和 `read` 只能放入 `integrationInputs`。平台按需查询邮件，把结果收敛为
消息 ID、thread ID、发件人、收件人、主题、时间、最多 500 字摘要和内容 digest，
然后才把数据送入固定 Agent。不会后台轮询收件箱。

`draft` 与 `send` 放在 `outputActions`。两者都先创建本地 `OutboundMailDraft`；
`send` 绑定首次创建时默认关闭，即使启用也必须逐次人工确认。发送审批同时校验：

- action 属于当前运行；
- 页面提交的 draft digest 与持久化草稿一致；
- 固定连接、Backend、原生工具和 schema hash 未变化；
- 绑定仍启用且连接健康；
- 原生工具参数符合发布时 schema。

发送状态通过事务从 `pending_approval` 原子切换为 `sending`，避免并发重复审批。
成功结果按幂等键复用；如果调用已发出但结果不确定，则标记
`send_outcome_unknown` 并阻止自动重试，避免重复发信。

运行详情接口为：

- `GET /api/runs/{run_id}/integration-actions`
- `POST /api/runs/{run_id}/integration-actions/{action_id}/decision`

批准发送还必须携带请求头 `X-SAPBA-Action: mail-send`。

## 工作流缺口与安全隔离

自动编排把缺口分为 `agent_missing`、`plugin_missing`、
`connection_required`、`reauthentication_required`、`permission_required`、
`runtime_adapter_unavailable` 和 `tool_contract_changed`。只有 `agent_missing`
进入“自由查询创建 Agent”；其余缺口跳转“插件与连接”页。插件连接完成后，工作流
重新检查会同时比较 Agent 目录与集成目录，并用保存的提案重新编译。

SAP 严格只读链不继承外部集成。Harness Tool Broker 仍只暴露经过批准的 SAP GET
和只读 Skill；邮件 MCP 只能由独立 Integration Gateway 在固定工作流节点中调用，
不能获得 SAP 凭据、shell 或文件写入权限。

## 当前范围

首期是单机、单操作系统用户、每个 Runtime Backend 下每个原生集成一个有效连接。
不包含共享邮箱、多租户、邮件 webhook、定时收件、等待回复、自动回复和附件上传。
`codex-runtime` 本地插件 ID 暂时保留兼容；页面将其解释为通用 Agent Runtime Router。
