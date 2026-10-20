# Workflow authoring v2 / 工作流全流程助手

工作流创建／编辑与 Agent 草稿共用 AI 助手页面标注交互，引用仍由工作流服务校验。详见[通用标注说明 / Shared annotations](ai-assistant-annotations.md)。

## Implemented / 已实现

The independent `workflow_authoring.v2` operation uses an isolated, secret-free
source snapshot and canonical workflow JSON. Legacy workflow APIs remain supported;
Agent authoring and free-query Codex configuration are unchanged.

工作流各步骤共用助手：解释／修改、已保存对象引用、安全 Markdown、按轮 Diff、
持久回合与阶段事件、SSE 和有界轮询、最多三条等待消息、撤回与取消、澄清绑定。
关闭面板不取消任务。解释不改变修订或资格；修改前显式保存未保存编辑。
每轮结果通过修订及摘要 CAS，在同一数据库事务中保存修订、Diff 和终态。
无变更不失效资格，固定 Agent 和邮件连接绑定不经过编译器自动升级。

Queued messages do not reserve a workflow operation lock. Stale messages pause
for explicit review and a new request ID. Restart never automatically replays them.
Published drafts are explanation-only; create a new version before revision.
After an unclean restart, an old running round retains `cleanup_pending` until
its process ownership can be reconciled; it is not assumed safely terminated.

## Execution modes / 执行模式

- `restricted` is the default. The real Windows permission profile is probed
  before model work. A failed probe blocks that round, with no automatic fallback.
  Source is read-only, candidate and temporary tests are writable. Network access
  is not authorized in this mode.
  The v2 probe additionally requires a readable source canary whose writes are
  denied; a missing file or a timeout never counts as a read-only boundary PASS.
- `full_access` requires per-request explicit trusted-local confirmation. It is
  **not an OS sandbox**. Native commands operate with the local account's rights;
  the platform cannot guarantee preventing bypasses. Model work still runs from
  a source copy; production files are not accepted as output artifacts.
- SAP/mail credentials and personal MCP servers are not supplied. No SAP calls,
  mail sends, live validation, publication or activation are performed by v2.
- Each new dispatched round has a 3600-second default budget (API maximum 3600),
  followed by at most ten seconds of owned-process cleanup. Unconfirmed cleanup
  retains an operation blocker. Waiting/queue time is outside that budget.
  Explicit shorter API budgets and already-saved rounds retain their original values;
  pending requests are retransmitted unchanged. The one-hour default applies to
  both Codex and WorkBuddy workflow authoring, without changing SDK/model options.

新工作流助手回合默认执行预算为 3600 秒（1 小时），前台与 API 默认值一致。
历史回合、等待重传的请求及显式指定的较短预算不改写；排队时间不计入执行预算，
清理窗口仍最多 10 秒。此项按用户要求同时适用于 Codex 和 WorkBuddy 工作流编写，
不修改 SDK、模型、推理或权限配置。

## 候选定义与可选修改范围 / Candidate definition and optional scope

工作流定义、历史对话和控件引用分别传入共用编排；引用不是工作流根字段，也不构成修改授权。候选先通过版本化根字段及现有固定绑定、编译检查，才能应用为修订。AI 新增会话字段或修改平台维护字段会被拒绝，不静默删除后应用；来源已有的未知扩展只能原样保留。历史正式工作流的读取与执行不改变。

存在当前修订的字段引用时，输入框附近提供默认不勾选的“仅修改已选字段”。勾选后的修改请求携带 `allowedChangePaths`（最多 20 个有效 JSON Pointer），服务端按结构及数组位置比较全部差异；范围外变更返回具体路径，原定义、修订及资格保持不变。该约束仅控制候选应用，不移除 SDK 调查、文件编辑和本地测试能力。需要关联修改时取消限制，以新请求提交。未保存内容须先显式保存并重新核对引用；修订或字段变化取消勾选。响应不确定时重传原请求及原范围，不改用另一意图。解释请求不携带修改范围，也不得产生修订。

Definition, conversation history and control references are distinct shared inputs. References are neither definition fields nor authorization. New conversation metadata or altered platform-owned fields fails candidate validation; source-only unknown extensions must remain unchanged. Existing published-workflow reading and execution remain compatible.

An optional, initially unchecked selected-field scope sends at most twenty verified JSON Pointers in `allowedChangePaths`. Structural comparison, including array positions, rejects any out-of-scope change without modifying the definition, revision or validation eligibility. This is an application gate, not removal of SDK investigation, local editing or tests. Related changes require a new unscoped request. Dirty content must first be explicitly saved and references reconfirmed; field/revision changes clear the opt-in. Uncertain delivery retries the identical intent/body/scope. Explanation never applies revisions. Omitting scope preserves legacy request fingerprints.
仅凭配置不能证明 Windows 隔离成立。离线检查实际命令边界（不调用模型或 SAP）：

```powershell
.\.venv\Scripts\python.exe scripts\check-workflow-authoring-runtime.py --mode restricted
.\.venv\Scripts\python.exe scripts\check-workflow-authoring-runtime.py --mode full_access --confirm-trusted-local
```

## Capability gaps / 未交付能力

The present adapter reconstructs platform history, not native SDK thread resume.
Runtime steering is queue-only. SSE reports platform phases and safe SDK tool
kind/status events, not raw command output or token streaming.
Native subagents, screenshot input and task-scoped workflow MCP execution are not
verified/connected; the UI/adapter must not advertise them as available. Additional
filesystem/network approval interactions remain unavailable (use a new, explicitly
confirmed trusted-local request only if that scope is acceptable).

当前实现不是“完整 Harness 所有能力已经验活”的证明。WorkBuddy/DeepSeek 不注册
为 v2 可执行 Provider。SDK 原生续接、steer、子 Agent 需分别验证，再
通过独立适配器启用，不改动现有 Agent／自由查询路径。未知诊断引用明确拒绝；
首期引用支持规范工作流 JSON 字段。DPAPI 必须在部署服务的实际 Windows 账户验证，
不可通过明文存储或跳过加密检查来使测试通过。

Official integration boundary: [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk).

## Local verification / 本轮本机验证

- Workflow/factory/Runtime/plugin/workspace regressions: 104 passed.
- Latest v2-specific regression after the source-boundary probe change: 17 passed.
- Frontend suite: 100 passed; production build completed; Astro reported zero
  errors, warnings or hints.
- Three representative DPAPI tests passed under the normal Windows account.
- The normal-account full-access fixed-command probe passed without SAP/model
  calls. The restricted probe timed out while initializing the Windows permission
  profile. It is not an isolation PASS and did not trigger a fallback.
  The existing user sandbox log has no entry for this probe, so the cause has
  not been attributed to a particular Windows setup failure. Do not treat its
  older firewall setup messages as evidence for the current timeout. Native
  sandbox initialization/repair must be explicitly authorized; this change does
  not create Windows users or alter firewall rules automatically.
- No production services were restarted, no real model workflow round was run,
  and no SAP/mail/publication operation was performed. Browser interaction and
  additional native capability smoke tests remain outstanding.
