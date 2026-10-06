# WorkBuddy 独立 Runtime / Isolated WorkBuddy Runtime

实施检查结果与未通过门禁见[验证记录 / Validation record](workbuddy-validation.md)。

## 状态与边界 / Status and boundaries

Codex 仍为默认，WorkBuddy 保持禁用。本轮共享编排重构仅进行离线验证；18 项首期操作的新编排摘要都需要各自真实验证后才能开放选择，不沿用旧代码的成功资格。历史通过、失败、环境和任务绑定保留原样，详见验证记录。锁定 SDK `codebuddy-agent-sdk 0.3.247`、内置 CLI `2.141.0`、已有独立安装、认证和模型状态没有在本轮升级或切换。模型兼容性通过不等于业务操作通过；已有任务不能自动更换 Provider、模型或环境。

Codex remains the default and WorkBuddy stays disabled. This shared-orchestration migration is validated offline only; all eighteen operations need live checks against their new orchestration digests before selection can be enabled. Historical results and task/environment bindings remain unchanged; consult the validation record. The pinned SDK `codebuddy-agent-sdk 0.3.247`, bundled CLI `2.141.0`, independent installation, authentication and model state are neither upgraded nor switched here. Model compatibility does not certify business operations, and existing tasks cannot silently change Provider, model or environment.

## 2026-10-06 共享编排迁移 / Shared-orchestration migration

平台增加 SDK 无关的 `SharedPlanner`、`RuntimeRequest / RuntimeResult / RuntimeEvent`、集中预算策略和两种执行驱动。计划、计划补全、总结、查询反馈与重新解释、工作流提案/检查/修复/反馈、岗位分页分析/反馈及 Agent 创建的业务逻辑只有一个实现。Codex 的旧入口委托共用层；WorkBuddy 的相应重复实现已移除。工作流 v2 的候选准备和检查、选样生命周期、草稿候选检查也已共用。

The SDK-independent layer owns shared Planner algorithms, typed execution contracts and budget policy. Planning, grounding, summarization, query feedback/reinterpretation, workflow proposal/review/repair/feedback, paginated role matching/feedback and Agent creation have one business implementation. The old Codex entry points delegate to it, and the corresponding WorkBuddy duplicates have been removed. Workflow-v2 candidate preparation/checks, sample discovery lifecycle and draft candidate checks also share an authority.

18 项操作已接入共享业务入口。Harness 自由查询、独立基线和验收自由查询的预检、预算、隔离上下文、超时/中断恢复、证据保留及结果写回由 `runtime_harness` 维护；控制器只保留委托入口，原生会话/事件及 worker 协议转换在各驱动中。查询范围在公共 Broker 重新核对，包括 Provider 规范化后的计划和 Skill 参数；未知输入须澄清，不能整表读取替代。12 项 Planner 操作及其余六项均有迁移前 Codex 离线调用轨迹，核对提示、Schema、阶段、权限参数和结果。轨迹为模拟 SDK 的代表性路径，不代替真实模型、SAP 或全部原生会话能力验证。

All 18 operations enter shared business orchestration. `runtime_harness` owns admission, budgets, isolated context, timeout/interruption recovery, evidence retention and result application for free query and acceptance phases. Controllers delegate; drivers translate native sessions/events and owned-worker protocols. The common Broker rechecks normalized query scope and Skill parameters; unresolved inputs require clarification rather than unscoped reads. Before-source Codex traces cover the 12 Planner operations and six remaining operations, comparing prompts, Schema, stages, permission parameters and results. These representative mocked-SDK paths do not replace live model/SAP or complete native-session capability validation.

WorkBuddy 资格额外绑定共享编排版本及操作摘要。旧真实验证记录不改写，但摘要变化后不能为新代码背书，需要重新验证。锁定 worker 协议及 SDK/CLI 安装不变；本轮没有访问模型或 SAP，也没有启用、重启、发布、提交或推送。可信本地模式仍不是操作系统级隔离。

WorkBuddy qualification additionally binds the orchestration version and operation digest. Historical live records remain unchanged, but changed code needs new validation. The pinned worker protocol and SDK/CLI installations are unchanged. This migration has not contacted a model or SAP, enabled a Runtime, restarted services, published, committed or pushed. Trusted local mode remains outside OS-level isolation.

## 独立安装 / Independent installation

在 Windows 使用平台 `.venv` 之外的 Python，以及来源和 SHA-256 已独立核对的 **0.3.247 wheel**：

On Windows, use a Python installation outside the platform `.venv` and an independently verified **0.3.247 wheel**. As checked on 2026-10-02, the version is absent from the current PyPI index, but its [historical official Windows wheel](https://files.pythonhosted.org/packages/1c/86/82bdd8ce575b83d9648dd47ce670aa6c7568c90928769445b196417a124f/codebuddy_agent_sdk-0.3.247-py3-none-win_amd64.whl) remains downloadable. The publisher's reason for removing the index entry is unknown. Download this file into a local ignored or external directory; do not repack an installed SDK.

截至 2026-10-02，当前 PyPI 索引没有这个版本，但上述历史官方 Windows wheel 仍可下载。索引移除原因未知。将文件下载至本地忽略目录或仓库外目录，不重新打包已有 SDK。核对 / Verify:

```powershell
(Get-FileHash -Algorithm SHA256 "C:\VerifiedPackages\codebuddy_agent_sdk-0.3.247-py3-none-win_amd64.whl").Hash
```

预期摘要 / Expected SHA-256: `1189d1f06adc3358e62e134bcd704c8ea2089cb271addd90f8f924d1c25fd398`

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-workbuddy-runtime.ps1 `
  -Python "C:\RuntimePython\python.exe" `
  -SdkWheel "C:\VerifiedPackages\codebuddy_agent_sdk-0.3.247-py3-none-win_amd64.whl" `
  -WheelSha256 "1189d1f06adc3358e62e134bcd704c8ea2089cb271addd90f8f924d1c25fd398"
```

示例路径必须替换。`config/workbuddy-requirements.lock` 固定并校验独立依赖。安装不修改平台 Python、Codex SDK/CLI 或个人 Codex 设置；失败保留诊断副本，当前环境不变。SDK wheel 摘要、依赖锁、解释器、CLI、worker 和安装文件清单绑定新环境摘要。已有平台安装不自动卸载。

Replace the example paths. `config/workbuddy-requirements.lock` pins independent dependencies with hashes. Installation does not mutate platform Python, Codex SDK/CLI or personal Codex settings. Failed preparation is retained for diagnostics without switching the current environment. The wheel hash, dependency lock, interpreter, CLI, worker and installed file inventory bind the release digest. An existing platform installation is not uninstalled automatically.

环境位于 `.local-data/runtimes/workbuddy/releases/<environmentDigest>/`。更新创建新目录而非原地覆盖；已有会话、排队消息及验收继续使用原环境，首期不自动删除。维护与派发共享跨进程锁，已有作业不中断，API 与 Codex 不重启。

Releases live under `.local-data/runtimes/workbuddy/releases/<environmentDigest>/`. Updates create new releases; existing sessions, queued messages and campaigns retain their original bindings. Old releases are not deleted automatically. Maintenance and dispatch share a cross-process lock without restarting the API or Codex.

## 本机登录 / Local sign-in

平台的“检查状态”只检查锁定 CLI 的已有登录态，不自动开启 OAuth、不接收密码或 Token。WorkBuddy App 登录不能未经核对就视为 CLI 已登录。按 [CodeBuddy 官方登录说明](https://www.codebuddy.ai/docs/cli/quickstart)在终端完成浏览器认证；选择与自己的账号匹配的站点或企业入口。本项目使用已安装的锁定 CLI，不另装全局 CLI。

The platform's status check only checks an existing login for the pinned CLI; it does not start OAuth or accept passwords/tokens. Do not assume that WorkBuddy App sign-in also signs in the CLI. Follow the [official CodeBuddy sign-in guide](https://www.codebuddy.ai/docs/cli/quickstart) in your terminal and choose the site or enterprise endpoint matching your account. Use the installed pinned CLI rather than installing a global CLI.

执行以下登录脚本，先校验完整安装清单，再使用**独立 WorkBuddy Python**在临时空目录通过 SDK 认证控制协议取得登录 URL，并打开默认浏览器。内置 `headless` CLI 不是交互式登录界面，不能仅直接启动它。此处平台 Python **仅读取 SDK-free 注册信息**，不安装或运行 WorkBuddy SDK。脚本不发送模型任务，不加载个人配置或 MCP，登录后用新的 CLI 进程再次确认可复用凭据；只有显示 `WorkBuddy login confirmed` 才表示这项检查成功。脚本不会自动启用或更换默认 Runtime。

Run the script below. It verifies the release inventory, then uses **independent WorkBuddy Python** and the SDK authentication control protocol in an empty temporary directory to obtain a login URL and open the default browser. The bundled headless CLI is not an interactive sign-in UI; launching it alone is insufficient. Platform Python **only reads the SDK-free registry**; it never runs the SDK. No model task, personal settings or MCP are loaded. A fresh CLI process checks reusable credentials after the callback. Only `WorkBuddy login confirmed` confirms this check. The script does not enable WorkBuddy or change the default Runtime.

```powershell
powershell -ExecutionPolicy Bypass -File "D:\SAPBusinessAgents\scripts\login-workbuddy-runtime.ps1"
```

仅核对安装、不启动登录 / Verify installation without starting sign-in:

```powershell
powershell -ExecutionPolicy Bypass -File "D:\SAPBusinessAgents\scripts\login-workbuddy-runtime.ps1" -VerifyOnly
```

安装在其他目录时替换上述绝对路径。默认使用锁定 SDK 的 `external` 认证方式；需要其他已支持方式时显式传入 `-MethodId`，拒绝不支持的方式不会自动换入口。默认浏览器未打开时会在**本机终端**显示临时登录链接，也可使用 `-NoBrowser` 手动打开。不要把链接、密码或 Token 粘贴到聊天或日志。登录有 300 秒等待时限；失败、超时、取消或未持久化均返回非零状态，不把 CLI 正常退出当作成功。

Replace the absolute path for other installation directories. The default is the pinned SDK's `external` authentication method; use `-MethodId` for another supported method. Rejection never silently switches endpoints. If browser launch fails, a temporary URL is shown in the **local terminal**; `-NoBrowser` also permits manual opening. Do not paste the URL, passwords or tokens into chat or logs. Login waits up to 300 seconds. Failure, timeout, cancellation or missing persisted credentials returns a nonzero status; a clean CLI exit alone is not success.

只检查已有登录、不开启认证 / Check existing login without initiating authentication:

```powershell
powershell -ExecutionPolicy Bypass -File "D:\SAPBusinessAgents\scripts\login-workbuddy-runtime.ps1" -CheckLogin
```

登录完成后显式检查 WorkBuddy 状态，再检查所选模型。官方 SDK [说明](https://www.codebuddy.ai/docs/cli/sdk)支持复用终端 CLI 登录，但实际可用性仍由本项目的锁定环境检查确认。无需将任何连接凭据填写到 SAPBusinessAgents 或发送到聊天中。

After signing in, explicitly check WorkBuddy status and then the chosen model. The SDK [documents](https://www.codebuddy.ai/docs/cli/sdk) reuse of terminal CLI authentication; the pinned-environment check still confirms availability. Do not enter connection credentials into SAPBusinessAgents or send them in chat.

`workbuddy_environment_access_denied`表示当前 Windows 账户不能读取安装，不等于SDK未安装或CLI未登录。请在运行本地服务的同一 Windows 账户下核对权限；不要因此重置 Codex 或删除旧环境。合法 JSON 中的损坏清单与管理状态也会明确阻断 WorkBuddy，不能破坏 Codex 的 Runtime 列表。

`workbuddy_environment_access_denied` means the current Windows account cannot read the installation; it does not mean that the SDK is missing or the CLI is signed out. Check permissions under the account running the local service. Do not reset Codex or delete old releases. Structurally damaged manifests or management state also block WorkBuddy without breaking Codex Runtime discovery.

## 管理及能力 / Management and capabilities

- 独立状态保存在 WorkBuddy 的 `state.json`；禁用时列表仅读取状态，不探测登录或模型。显式检查可以核对认证及残留进程。
- 显式“刷新模型”从锁定 CLI 的帮助读取声明的模型 ID。它们仅为候选，不代表账号可以使用，目录仍标记不完整；列表加载不自动启动探针。用户可填写或选择候选 ID，在当前环境登录检查通过后执行真实结构化输出检查。
- 推理强度始终为 `null / sdk_default`。模型身份未明确报告时保持未知；路由别名不能用于正式验收。每个验收 worker 必须报告与冻结检查相同的实际模型。
- 按环境、操作、契约版本分别保存验证记录。自由查询/反馈、Agent 编写/选样、岗位匹配、旧工作流接口、`workflow_authoring.v2` 和验收阶段分别验证。内部验证入口 `workbuddy_verification.validate_operation` 要求实际业务实现及平台完整契约检查，不提供用户通用执行或能力登记 API。
- 独立基线使用 `workbuddy-baseline/1` 制品；原 Codex 制品与历史记录不重写。

- Independent state is stored in WorkBuddy `state.json`. Disabled listing is read-only; explicit checks may verify authentication and orphan-process cleanup.
- Explicit model refresh reads IDs declared by the pinned CLI help. These are candidates, not verified account access; the catalog remains incomplete and listing never starts a probe. Enter an ID or pick a candidate, then run a real structured-output check after the current environment passes authentication checking.
- Reasoning is always `null / sdk_default`. Unknown actual identity stays unknown; routing aliases cannot qualify for formal acceptance. Each acceptance worker must report the actual model bound by its check.
- Validation is per environment, operation and contract version. Query/feedback, Agent authoring/sampling, matching, legacy workflows, `workflow_authoring.v2` and acceptance stages are validated independently. The internal `workbuddy_verification.validate_operation` requires the real business implementation and a complete platform contract check; it is not a public execution or registration API.
- Independent baselines use `workbuddy-baseline/1`; existing Codex artifacts and history are unchanged.

模型响应兼容与具体身份是两个独立结论。只有 SDK 的 `AssistantMessage.model` 明确报告具体身份才可用于正式验收；初始化消息中回显的模型选择、模型生成的自我介绍以及 `default-model`、`fast-model`、`balanced-model`、`primary-model`、`deep-model` 等路由别名不构成身份依据。同轮报告不同模型时身份保持未知。旧检查缺少身份来源时要求重新检查，不重写历史记录；普通编写仍可使用兼容性检查通过的路由，并如实显示身份未知。

Response compatibility and concrete identity are separate conclusions. Formal acceptance requires an explicit concrete `AssistantMessage.model` from the SDK. Initialization metadata, model-generated self-descriptions and routing aliases are not identity evidence. Conflicting reported models leave identity unknown. Old checks without an identity source require a new check, without rewriting history. Ordinary authoring may use a compatible route with its identity honestly shown as unknown.

模型兼容性探针使用独立的 90 秒执行预算，排队时间不计入，进程清理仍最多 10 秒；不改变 Codex 或任何业务/SAP 运行时限。探针只执行一次，不自动换模型或重试。临时工作目录被 Windows 占用时，保存安全的 `workbuddy_model_workspace_cleanup_failed` 警告及真实检查结果，不用目录清理异常覆盖超时或失败。进程树清理仍由监督器单独核对；无法确认时继续保持 `cleanup_pending` 门禁，不能将其标为成功。

Model compatibility probes have an independent 90-second execution budget, excluding queueing, with up to 10 seconds for process cleanup. Codex and business/SAP deadlines are unchanged. Each probe runs once without automatic retry or fallback. A Windows-locked temporary directory produces a safe `workbuddy_model_workspace_cleanup_failed` warning alongside the actual probe result rather than masking a timeout or failure. The supervisor still independently verifies process-tree cleanup; uncertain cleanup retains the `cleanup_pending` gate and cannot be certified as success.

## 权限、预算和恢复 / Permissions, budgets and recovery

每个作业使用独立 worker 和 Windows Job Object。协议绑定任务、尝试、操作、环境、Runtime 摘要与截止时间；单消息最多 1 MiB，累计输出最多 8 MiB。最多一个执行 worker 与一个管理探针并行；排队不计入执行预算。取消/超时只终止所属进程树，清理最多 10 秒，迟到结果不写回。

Each job owns a worker and Windows Job Object. The protocol binds task, attempt, operation, environment, runtime digest and deadline, with 1 MiB frames and 8 MiB cumulative output. At most one execution worker and one management probe run concurrently. Queueing is outside the execution budget. Cancellation/timeouts terminate only the owned process tree, with a maximum 10-second cleanup window and no late writeback.

API 重启后核对 PID 和进程启动标识，不按裸 PID 杀进程。无法确认清理时保留 `cleanup_pending` 并阻止派发；系统设置中显式检查 WorkBuddy 可重新核对。worker 不写正式数据库、不应用修订、不发布或启用。

After API restart, PID and creation identity are reconciled; a bare PID is never terminated. Uncertain cleanup remains `cleanup_pending` and blocks dispatch; an explicit WorkBuddy check in System Settings rechecks it. Workers never write the production database, apply revisions, publish or activate.

WorkBuddy 草稿修改及工作流 v2 编写须明确确认可信本地模式，可调查副本、编辑并运行本地测试。Windows 受限模式、原生续接、steer、图片和子 Agent 未验证时拒绝或显示不可用。平台会话上下文、队列及逐轮 Diff 负责承接，不跨 SDK 复用线程 ID。

WorkBuddy draft revisions and workflow v2 require explicit trusted-local confirmation and may inspect, edit and test their local copy. Unverified Windows restricted mode, native continuation, steering, images and subagents are rejected or shown unavailable. Platform history, queues and per-round diffs provide continuity; thread IDs are not shared across SDKs.

WorkBuddy 草稿修改支持直接编辑隔离的 `agent-package` 文件，再返回简短的结构化 `revise_agent` 终态，由平台读取候选；无需在答复中重复整个文件。该路径只在用户已确认可信本地模式时开放，不改变公开反馈接口，也不自动写入正式包。候选仍通过原有身份、规则、Schema、修订与 Diff 门禁；平台源码变更及受保护身份/验收字段变更会被拒绝。没有有效终态、取消或超时后，工作副本中的文件不构成可导入的结果。显式 JSON 编辑方式及 Codex 路径保持不变。

WorkBuddy draft revision can edit the isolated `agent-package` files and return a compact structured `revise_agent` terminal reply for platform-side candidate readback, without repeating whole files in the answer. This requires explicit trusted-local confirmation, does not change the public feedback API and never writes a production package automatically. Existing identity, rule, Schema, revision and Diff gates still apply; platform-source edits and changes to protected identity/acceptance fields are rejected. Without a valid terminal reply, or after cancellation/timeout, workspace files are not importable results. Explicit JSON edits and the Codex path are unchanged.

原生进度诊断记录工具类型、回合内调用序号、耗时、错误标志，以及最终结果接收与 SDK 关闭阶段。它们不保存命令、参数、文件路径、工具正文或 SDK 调用 ID，也不把中间答复或已经编辑的文件当成完成结果。工作流 v2 与草稿反馈复用同一 WorkBuddy JSON 对象解析器；语法、Schema 或嵌套工作流错误保存安全约束诊断，不修改业务字段、不隐式重新编译绑定。

Native diagnostics retain tool type, a per-turn ordinal, duration, error flag, terminal-result receipt and SDK shutdown phases—not commands, arguments, paths, tool bodies or SDK call IDs. Intermediate replies and edited files are not completion. Workflow v2 and draft feedback share the WorkBuddy JSON-object parser; syntax, Schema and embedded-workflow failures produce safe constraint diagnostics without repairing business fields or recompiling bindings.

工作流 v2 将平台冻结的输出 Schema 通过 SDK `extra_args` 传给锁定 CLI 的 `--json-schema`。该版本用本地 `StructuredOutput` 工具捕获结果；仅在提供 Schema 时启用它，不赋予 SAP、文件编辑、邮件或发布权限。2026-10-05 的无 SAP 实机探针已确认 `ResultMessage.structured_output` 返回且清理完成。平台继续校验外层 Schema、完整工作流及固定绑定；缺少结构化终态或旧 worker 未确认原生输出时明确拒绝，不静默退回普通文本或导入副本文件。工作流业务操作是否取得资格以验证记录为准。

Workflow v2 passes its platform-frozen output Schema through SDK `extra_args` to the pinned CLI's `--json-schema`. This version captures output with the local `StructuredOutput` tool, enabled only when a Schema is supplied; it grants no SAP, file-editing, mail or publication permission. A SAP-free live probe on 2026-10-05 confirmed `ResultMessage.structured_output` and completed cleanup. Platform checks still validate the outer Schema, complete workflow and fixed bindings. Missing structured termination or an old worker without native-format confirmation is rejected, never silently downgraded to text or workspace-file import. Business-operation certification is recorded separately.

草稿解释与修改也传入冻结的原生输出 Schema。`StructuredOutput` 仅负责本地格式捕获；解释仍不能修改，可信本地编辑仍须用户确认。完成请求及必要的针对性检查后收尾，不限制已授权编写工具或删减 Harness 能力。平台继续校验终态、候选包、保护字段和逐轮 Diff；原生终态无效时不自动派发第二轮编辑来修复 JSON，其他操作的原有格式修复行为及 Codex 路径保持不变。

Draft explanation and revision also pass a frozen native output Schema. `StructuredOutput` only captures local formatting; explanation cannot edit, and trusted-local editing still requires explicit confirmation. Finalization follows the requested task and necessary focused checks without removing authorized authoring tools or Harness capabilities. Terminal output, candidate-package, protected-field and per-round Diff validation remain mandatory. Invalid native termination does not automatically dispatch a second editing round to repair JSON; other operations retain their existing repair behavior, and the Codex path is unchanged.

可信本地草稿修改使用隔离包的文件引用，不再同时内联完整 Manifest、规则和 README 正文；必要资料仍可从同一副本读取。原始上下文大小门禁、历史决定、澄清、修订绑定及编写权限保持不变。2026-10-05 已通过离线回归；该提示投影优化尚未重新执行完整真实修改，不作为修改资格。

Trusted-local draft revision references the isolated package files instead of also inlining entire Manifest, rules and README bodies. Required material remains readable in the same copy. Original context-size limits, history, clarifications, revision binding and authoring permissions are unchanged. Offline regressions pass on 2026-10-05; this prompt-projection optimization has not rerun a full live revision and does not qualify modification.

**可信本地模式不是操作系统安全沙盒。** 平台只保证其提供的工具逐次授权；本机账户的命令权限仍可产生旁路。worker 不接收 SAP/邮件凭据，不自动加载个人插件/MCP；SAP 保持只读，邮件发送及发布启用保留现有确认。独立基线仅有其业务问题、契约及取证工具，不包含候选源码或历史答案。固定 Agent 与已发布工作流不新增模型选工具行为。

**Trusted-local mode is not an OS security sandbox.** The platform enforces its own per-call tool authorization, but native commands retain the local account's bypass risk. Workers do not receive SAP/mail credentials or automatically load personal plugin/MCP configuration. SAP remains read-only; sending, publishing and activation retain their confirmation gates. Independent baselines receive only the business question, contract and evidence tools—not candidate source or historical answers. Fixed Agents and published workflows remain deterministic.

## 验证顺序 / Validation order

### 剩余操作契约与预算 / Remaining-operation contracts and budgets

WorkBuddy 的旧工作流创建与反馈使用同一 3600 秒执行预算；容量预留在派发前完成，排队不计时，外层不再用 180 秒截断 WorkBuddy worker。Codex 原有外层等待及配置不变。Windows 临时目录占用只记录安全警告，不覆盖原始执行/超时错误；进程树清理未确认时仍阻断。

Legacy WorkBuddy workflow composition and feedback share a 3600-second execution budget. Capacity is reserved before dispatch, excluding queue time, and an outer 180-second wait no longer truncates its worker. Codex timing/configuration remains unchanged. Windows directory locks produce safe warnings without masking execution/timeouts; uncertain process-tree cleanup still blocks use.

查询、选样、验收、计划补全、旧工作流及岗位匹配将冻结的输出 Schema 放入 worker 顶层 `output_schema`，使用已锁定版本的原生 `StructuredOutput`。报告校验成功不等于原生终态捕获成功；终态缺失、无效 JSON、类型/枚举/引用错误均拒绝，不截取 Markdown、不补造字段。未取得终态时长度与摘要记为未知，不当作零长度输出。

Query, sampling, acceptance, grounding, legacy workflows and role matching pass a frozen top-level `output_schema` to the existing worker's native `StructuredOutput`. Report validation does not replace terminal capture. Missing termination, invalid JSON, types/enums or references are rejected without Markdown extraction or invented fields. An unreceived terminal has unknown length/hash, not a presumed empty response.

WorkBuddy 计划按 Provider 实际契约使用 `filters`、`select_fields`、`top` 及 `filter_from_previous`；`filter`、`keys`、`select`、`skip` 等未消费成员在读取前拒绝。计划补全不能删掉已确认过滤、字段或提高读取上限。平台冻结明确用户/选样/案例参数，校验规范化后的实际计划及 Skill 参数；无法映射时澄清。关联不能凭任意上游步骤绕过业务字段约束；派生键必须来自同范围证据，并保留实际配对。未知表达式不允许用整表读取后本地筛选代替。该防护不宣称能理解所有自然语言范围，也不构成操作系统隔离。

WorkBuddy plans use the actual Provider members `filters`, `select_fields`, `top` and `filter_from_previous`. Ignored aliases are rejected before reads. Grounding cannot remove confirmed filters/fields or increase read limits. Explicit user/sample/case parameters are frozen and checked against normalized plans and Skill inputs; unresolved mappings require clarification. An arbitrary upstream binding cannot bypass business-field constraints; derived keys must retain pairs from scoped evidence. Unknown scope expressions cannot be replaced with a full-table read/local filter. This is not universal natural-language interpretation or OS isolation.

旧工作流返回完整编译提案，不返回 JSON Patch 或自行固定版本。阶段明确声明 `confidence`，只有核对的精确能力匹配才用 `high`；公开输入补丁为字段/值对象，候选预期为输出/操作符检查数组。岗位结论必须使用提供的 `document_id`、`chunk_id`，不能任意补引用。共用编译、证据及引用校验仍为最终门禁。

Legacy workflows return complete compiler proposals—not JSON Patch or model-pinned versions. Stages declare `confidence`, with `high` reserved for a verified exact match. Input patches contain public field/value replacements; candidate expectations are output/operator check arrays. Role conclusions cite supplied document/chunk pairs without synthetic references. Shared compiler, evidence and citation validation remain authoritative.

先运行不安装 WorkBuddy 的 Codex 回归、独立协议/进程与 UI 测试，以及前后脱敏基线对照。取得核验安装制品后，依次执行安装/认证、无 SAP 模型/工具探针、一次只读查询及反馈、隔离草稿解释/修改和选样、岗位匹配与工作流 v2、单案例三级验收。真实失败保留诊断，不反复重跑，不把离线 fixture 标成真实验证。

First run Codex regressions without WorkBuddy installed, isolated protocol/process/UI tests and before/after redacted controls. Once the verified installation artifact is available, validate installation/authentication, non-SAP model/tool probes, one read-only query/feedback, isolated draft explanation/revision/sampling, matching/workflow v2 and a single-case three-stage acceptance. Retain failures without automatic reruns; offline fixtures never count as live validation.
