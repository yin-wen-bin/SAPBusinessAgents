# AI Runtime 分层验证与增量资格

本文件是验证方法论的权威入口。适用于 SDK 接入、模型切换、环境更新和执行链路修复。
自由查询明确离页停止、恢复查看及本机并发的使用与验证说明见[自由查询生命周期](free-query-lifecycle.md)；离线基础设施通过不等于新的业务PASS。
报告工具与完整终态分别核对；安全历史形状回放、共享枚举反馈和终态收敛说明见[结构化终态纠错与交接](runtime-terminal-repair.md)。
实现版本：`sapba.runtime-validation/1`；WorkBuddy 案例资格协议：`sapba.validation.qualification/2`。

## 状态与边界

协议 2 需要逐安装受控激活，不由安装代码或状态查询自动启用。本机后续授权中已经完成激活；判断其他安装时应读取其正式 `qualification_protocol`，不能从本文推定。最初的离线交付与后续真实验证是不同阶段。
Codex 使用共用验证方法做离线对照，但不迁移其准入或资格存储。

## WorkBuddy 三层准入策略（待受控激活）

策略 `sapba.workbuddy.three-layer/1` 将三种证明独立保存：

| 层 | 证明 | 模型切换 |
|---|---|---|
| SDK 接入认证 | SDK／CLI、worker、执行协议、工具桥接、授权、终态、取消、截止时间和所属进程清理；按操作及权限模式认证 | 相同执行组件继续适用，不绑定模型名称 |
| 模型兼容 | 所选站点、认证修订、环境及模型协议下的原生工具与结构化终态 | 缺少适用结果时做一次轻量检查 |
| 业务质量 | 各模型在案例中的实际业务结果、历史 PASS 与当前适用性 | 独立评测，仅作质量提示；不跨模型转移 PASS |

启用、默认模型选择及派发不再以业务评测 15/15 为门禁。操作总数从案例注册表计算，不硬编码分母。安装、SDK 接入、认证、模型兼容、模式、授权、范围、任务绑定及清理仍是硬门禁。普通业务差异不自动停用 Runtime。质量记录变化不进入配置或模型协议摘要。每次派发仍核对已认证操作／权限模式，未认证的 Windows restricted 模式不回退为 trusted_local。

新版模型检查 `sapba.workbuddy.model-check/3`：一个明确请求只派发一个作业，90 秒执行预算、最多 10 秒所属进程清理。仅提供内存 `compatibility_echo`，返回模型事先不知道的回执；最终对象必须含枚举、nullable、整数和嵌套结构。平台核对实际调用、回执、原生终态、Schema 与清理，不补字段、不纠错非法值、不从文本截取 JSON。不注册 SAP、邮件或发布工具，不允许命令、文件编辑或个人插件。一次作业内工具往返不等于自动重跑一个新作业。

为避免首次安装的循环门禁，这种有界管理探针在安装完整、认证有效且无待清理进程时即可经明确请求执行，不要求先获得 SDK 核心认证。其原生通信制品可作为后续认证的证据之一；兼容通过本身仍不能代替 SDK 核心认证，更不能开启未认证业务操作。安装目录变化只在双方完整执行指纹相同且各自完整性校验通过时复用 SDK 通信证据，不按旧目录编号一律拒绝，也不把不同 worker 当作相同执行组件。

新前端发送 `requestId`；同编号同内容返回原作业，不同内容冲突。响应不确定时仅 GET 原状态。中断的 running 记录需核对所属作业，不能直接重新派发。切换模型只影响新任务，旧会话、草稿、队列及 Campaign 仍使用原冻结绑定。旧且仍适用的模型检查按原版本读取，不伪造新版回显或原始探针制品。

新模型无需历史 Campaign PASS 即可启动正式验收；但身份未知或路由别名不能冒充具体模型。每个新 Campaign 仍执行当前修订预检、独立取证、实际输出、比较、只读审计及前后来源锚点。业务质量中的两项验收 PASS 仍只能由完整隔离 Campaign 授予。SDK 认证、模型兼容均不能替代这一次的业务证据。

### 审计与迁移

`scripts/audit-workbuddy-admission.py --repository <repo> --historical-index <index> --output <report>` 仅检查历史原始记录、源码归档、所属作业及业务制品，生成迁移清单；不应用认证、不调用模型或 SAP。SDK 核心认证需逐项可检查的证据及目标完整职责文件摘要。历史真实作业可以证明其 SDK 通信和执行能力，但不改写为其他模型的业务通过，也不要求基础设施证明依赖原模型兼容探针。

核心认证不能仅填写 `complete=true`：必须引用已完成且清理成功的原生 SDK 真实作业、目标绑定的基础设施检查报告、JUnit 案例结果，以及当时完整检查器源码与制品摘要。检查器源码保存在证据归档中，不能用今天的测试文件补证，也不会因测试注释或无关质量测试变化自动撤销仍适用认证。模拟作业只用于离线回归，不可冒充正式 SDK 真实作业。

策略激活是独立维护操作：先确认所有旧写入程序、相关任务和待清理进程退出，在维护锁内再次核对有时效的进程清单、状态摘要及 SDK 认证证据，再追加认证和激活记录。GET、模型切换、前端部署不会自动迁移。证据不足时保留原后端并列出具体补证，不默认重跑 15 项。回退保留新记录，不降级或改写历史格式。当前源码包不代表正式服务已激活此策略。

English: WorkBuddy admission separates model-independent SDK integration, lightweight per-model compatibility and advisory business evaluation. Business evaluation is not a universal enablement gate. Existing model checks retain their original contract and history. A new compatibility job uses one memory-only echo tool and strict native structured output within 90 seconds plus at most 10 seconds cleanup. Unknown responses are read back, never redispatched automatically. New models may start formal acceptance without historical Campaign PASS; identity, permissions, SAP scope, evidence, comparisons and source anchors remain mandatory. Migration is explicit, audited and maintenance-locked. Codex admission and history remain unchanged.

区分三个问题：历史是否通过过、当前绑定是否有有效资格、本次尝试是否完成。三者不得互相替代。
资格只能证明某项能力已验证，不能替代一次新业务查询或验收所需的实时证据。

## 工作顺序

1. 只读盘点历史作业、参数、源码、检查器和制品，保存工作区与配置哈希。无法取得进程清单时明确标为未知，不用“无任务记录”代替进程清理证明。
2. 选择案例，分别确认依赖、权限模式、对象、参数和授权有效期。展开依赖只形成计划，不扩大授权。
3. 冻结当前相关源码（包括未提交、未跟踪文件）、公开定义、案例、模型、站点、认证修订、环境和检查规则。
4. 顺序执行已授权且前置条件满足的案例；失败只阻断其依赖分支，独立分支继续。清理未确认、认证或绑定变化时停止相关派发。
5. 一次汇总独立问题，集中修复；冻结新批次，按依赖影响重验或审计承接。失败案例不自动重跑。

## 案例注册表

权威声明在 `src/sap_business_agents_platform/runtime_validation_registry.py`，成功检查在原有平台验证夹具中。
声明包含案例版本、操作、权限模式、执行前置条件、代码组件、资源及成功断言。案例内容摘要独立计算，不使用全局注册表版本清空资格。

| 层级 | 案例 | 依赖／说明 |
| --- | --- | --- |
| 环境、基础设施 | `environment.current`、`infrastructure.current` | 只核对有效的保存检查、启动与清理证据，不隐式登录或发送探针 |
| Agent | `agent.create`、`agent.explain`、`agent.revise` | 解释 bounded，修改 trusted_local；两个模式均必需 |
| 工作流 | `workflow.compose`、`workflow.review`、`workflow.repair`、`workflow.feedback`、`workflow.authoring` | repair 依赖 review；feedback 依赖 compose；v2 为 trusted_local |
| 查询反馈 | `query.feedback`、`query.presentation` | 独立；重新解释不读取 SAP |
| 岗位 | `role.analysis`、`role.feedback` | 反馈依赖适用分析的保存结果 |
| SAP | `query.limited`、`query.complete`、`sample.scope` | 有限样本不能声称完整；free_query 两案例均必需 |
| 验收 | `acceptance.campaign` | 一次完整隔离 Campaign 对应两项操作资格，无无关编写／岗位／选样前置条件 |

当前为 **15 项操作、16 个业务案例、2 个保存状态检查**。案例数不是操作数。
每个操作的全部必需案例和权限模式通过才合格；一个成功不能掩盖另一个必需案例失败。
旧操作定义不变，增加案例只产生缺失案例要求。

## 私有工具

在仓库根目录使用平台 Python，不使用独立 SDK 环境来运行管理脚本：

```powershell
.venv\Scripts\python.exe -B scripts\validate-ai-runtime.py plan --case acceptance.campaign
.venv\Scripts\python.exe -B scripts\validate-ai-runtime.py audit-reuse --output .local-data/runtime-validation-audit/history.json
.venv\Scripts\python.exe -B scripts\validate-ai-runtime.py report --batch .local-data/runtime-validation-audit/example
```

默认只读检查不派发任务、不写正式资格。`plan` 中未授权项显示 `validation_not_authorized`。
`audit-reuse` 无 receipt 时盘点历史；带 receipt 时只验证承接建议。输出只能写入隔离审计目录。

### 后续获准执行时的输入

批次输入为 JSON，至少包含：

- `runtime`：既有、有效、类型化的 Runtime 快照，不能用默认值猜测。
- `selected`：案例 ID；`parameters` 与 `fixture_parameters`：来自 `runtime_validation_fixture_policy.parameters(case)` 的确切请求。当前首组夹具固定原批准 PO／日期／组织，不支持任意替换范围。
- `targets`：对应 `.local-data/runtime-validation-audit/<batch>/fixtures/<fixture>`，不能指向正式草稿。岗位分析／反馈、工作流依赖使用同一隔离目录保存前置制品。
- `authorizations`：逐案例 `case_id`、`case_digest`、`mode`、`target`、`parameters_digest`、`runtime_digest`、`expires_at`、`request_id`、`sap`。必须来自用户明确批准的范围；不是模型可签发的生产工具权限。

授权清单只用于把用户批准绑定到一次私有验证，实际 SAP 调用继续经过原有 Broker／工具授权。可信本地模式仍不是操作系统级防旁路沙箱。

```powershell
# 归档准备不执行模型；正式 run 和 apply-reuse 必须分别获得授权。
.venv\Scripts\python.exe -B scripts\validate-ai-runtime.py plan --manifest approved-input.json --batch .local-data/runtime-validation-audit/example --freeze
.venv\Scripts\python.exe -B scripts\validate-ai-runtime.py run --manifest .local-data/runtime-validation-audit/example/manifest.json --batch .local-data/runtime-validation-audit/example --execute
```

`run` 要求协议已受控激活；命令不会隐式激活。父进程使用 Windows Job Object，子进程在归属确认前不能执行；新解释器从归档加载平台模块。
源码快照包含必要动态导入闭包；资格依赖仍按具体操作计算，不以归档总摘要使无关操作失效。worker 只引用已有 release 清单，绝不复制或改写安装环境。
现有 API 不能证明加载代码绑定时，不可拿它来替代冻结验证进程。排队不计执行预算，沿用所属流程预算及最多 10 秒清理，不另加整轮统一上限。

批次 manifest、源码、参数、账户或其他冻结控制项改变时拒绝执行／登记。没有有效终态、平台业务检查或清理证明，不登记 PASS。
冻结输入使用保存运行所消费的完整类型化 Runtime 快照；缺少默认字段时先拒绝，不能在授权指纹生成后静默补入。
新请求在生成授权指纹之前通过现有认证服务绑定，同时保存 `authentication_revision` 与 `authentication_binding_revision`。冻结、派发及资格写回使用相同的严格认证断言；缺失、过期或错站绑定不会在执行中补齐。隔离验收还在创建草稿和 Campaign 前核对实际执行服务中的认证状态，不隐式登录或重复探针。
Harness 案例还须在授权指纹生成前冻结调度器使用的 `execution_flow` 和当前 `orchestration_version`，执行时仍逐字段严格核对。运行中先进入保存的 Runtime 上下文，再选择其事件订阅；不从全局默认 Provider 借用模型或资格。
验收控制器的辅助脚本及其导入依赖归档为验收案例组件。派发前检查实际冻结导入路径，依赖缺失时不创建 Campaign。
原始制品保留在现有受保护的存储；公共报告只含安全状态、引用及摘要，不归档凭据、登录缓存、原始推理或受限 SAP 行。

### 重复请求、恢复与取消

跨进程锁和原子日志在调用模型前保存稳定派发声明与预分配父作业 ID。同批次同案例同绑定只派发一次。
重复请求返回原作业；已有 claimed/running/cleanup_pending 时不得重发。重新验证必须建立明确关联的新批次。
中断恢复必须核对该父作业的原生作业、进程启动标识、终态和清理制品。单凭 PID 不存在、没有报告或用户口头确认，不能宣称清理成功。
原失败与清理失败分开保存；未知清理阻断后续派发，不重写所有操作的历史业务结论。

旧 `verify-workbuddy-operations.py` 与 `verify-workbuddy-sap-continuation.py` 支持 `--manifest` 委托到共用入口。协议 2 激活后旧参数式执行被拒绝；旧代码只保留迁移前兼容，不能再授予协议 2 资格。

## 资格、摘要与有限承接

协议 2 台账追加保存：案例／权限模式、请求与实际模型、站点／账号修订、完整执行环境、业务依赖闭包、检查器、源码归档、检查报告及所属真实作业。
模型、SDK 或身份范围不同，不转移业务 PASS。模型探针成功不能授予业务资格。

依赖使用完整组件文件，不做 AST 截取或由模型判断哪些代码无影响。选样专属组件变化只影响选样；公共执行组件变化影响其所有使用者。混合文件尚未拆分时保守纳入整个文件，不能通过忽略片段规避重验。

| 情况 | 决策 |
| --- | --- |
| 同案例、执行和业务绑定一致 | 当前真实 PASS 可保留；另一环境仍须目标环境依据 |
| `identical_execution/1` | 相同内容、仅环境放置等允许的非执行信息不同；必须证明目标启动、身份与清理 |
| `non_execution_projection/1` | 仅明确白名单的展示／审计投影文件改变；必须有绑定的回归、Codex 对照及目标基础设施证据 |
| `identical_operation/1` | 分别从原、目标完整归档重算实际案例组件、夹具、输入与检查器；精确匹配各自原绑定后，允许闭包外文件变化。须有目标启动、依赖回归与 Codex 对照，不能只比较文件名或当前摘要 |
| Schema 编码、权限、范围、终态、清理、预算或算法改变 | 受影响案例真实重验 |
| 不明确、缺原始源码或检查依据 | 保留历史结论，列出缺口；不生成可应用凭据 |

每份 `ReuseReceipt` 指向**原始真实 PASS**，保存源／目标、精确变更、规则、证据、审核人和请求 ID。不允许从派生承接无限串联。
首期非执行白名单仅包含规则中列出的文档和 Skill 文件；被派发、摘要或完整性校验调用的诊断／哈希辅助代码不在白名单内。以后扩充必须修改规则并补充证据，不能只贴上“日志修改”标签。
`apply-reuse --receipt ... --execute` 是单独授权动作；应用前持管理锁重新核对当前绑定、失败范围、完整性和证据。审计建议不等于应用结果。
检查器／资格规则独立版本化；改变规则不自动宣称业务执行发生变化，也不能自动为新要求背书。

原生作业、模型身份、业务检查报告和源码完整性检查已原样提取到 `runtime_validation_checker.py`。案例摘要使用此完整文件及共用基础模块；资格账本／承接管理代码不再混入业务检查器摘要。唯一已登记的 `checker_extraction/1` 映射同时固定原两个文件的精确摘要和提取后完整检查器摘要，验证原始完整归档，且只允许 `case_checker` 表示变化；其他业务依赖、案例、权限、模型、账号、环境均须相同。原 PASS 不改写、不改绑，仍由完整检查器复核。未知检查器改动不自动兼容。

### 历史恢复与决策投影

`runtime_validation_history.py` 仅生成审计投影，复用原资格验证器，不增加 PASS 存储或准入路径。逐案例区分 `retain_current`、`reuse_ready`、`live_retest_required` 与 `evidence_unresolved`；待补充承接凭据不是业务失败，也不是可直接启用。

历史模型只能由原作业与精确匹配的原 Runtime 快照归属；包含多 Provider 的原 freeze 使用其显式保存的 WorkBuddy 绑定。未知模型单列，不能计入所选模型。恢复源码只从完整验证的不可变归档、明确的历史 Git 提交或保存的历史编写副本核对原记录摘要，不回退到当前工作区。副本只恢复精确内容，不冒充完整归档；恢复了源码内容不等于恢复了当时未保存的案例或检查报告。

真实依赖比较包含删除的文件、组件关系及预算；公共执行、异常、终态或清理变化仍要求受影响案例重验。已经完整验证的原案例可以形成待审计承接候选；只有原规则及绑定凭据核验成功，才能标记 `reuse_ready`。状态查询不会应用凭据。

持续目标的真实验证轮次由 `runtime_validation_rounds.RoundLedger` 复用原锁和原子日志记录。每个冻结批次先独占声明再派发；响应丢失返回原声明，启动失败不退回轮次，未核对结果与清理不得开启下一轮。Campaign 单独占一轮；离线审计、回归不占真实轮次。此计数器不是新调度器，不替代现有案例授权、作业归属或结果验证。

已确认的契约／业务失败由修复后的真实通过解除；审计承接不能覆盖它。取消、网络和超时不改写原 PASS，本次不得算成功。公共组件、环境和未知故障保留显式阻断，解除须有追加依据。
同一案例版本、模型及环境的新完整真实 PASS 可以通过追加、可核对的引用解除原先不透明的 `validation_execution_error` 尝试。夹具统一返回的 `validation_fixture_failed` 也适用，但额外要求旧尝试明确完成清理、冻结业务数据摘要一致。未知的其他错误、公共组件／环境故障及待清理任务不适用；复用凭据、离线测试和无关案例的成功均不能解除。原失败保留，解除引用每次使用时重新核验真实 PASS 的完整制品与绑定。

### 单次核对与状态展示成本

系统配置页可通过 `GET /api/system/sdk-runtimes?view=summary` 和
`GET /api/system/sdk-runtimes/workbuddy/models?view=summary` 读取 WorkBuddy 展示摘要。
未携带参数的接口及 Codex 路径保持原语义。`display_status` 表示五秒有效的后台元数据投影，
包括快照编号、进程实例、时间、过期／刷新状态及安全错误码；原 `status_refresh` 仍表示完整核对，不能混同。
摘要只读取白名单管理字段、已记录版本、SDK 已发现目录和上次认证事实，不校验整套历史台账、
遍历安装或发起官方认证／模型探针。未知不是未登录，已记录版本也不是安装完整性通过。

完整后台核对保持原六十秒展示策略，模型详情复用同一次同步检查取得的目录结果。
账号操作能力通过原认证 GET 独立读取，不等待无关模型／业务评测；原认证检查及确认规则不变。
展示指纹仅控制界面更新，不是新的执行摘要，不作为启用、模型保存、队列绑定或任务派发依据。
实际操作仍从当前真实状态执行原完整安装、认证、模型、范围与权限门禁；页面缓存不得授予执行权。
源文件或锁异常时保留上次显示并标过期，迟到结果不能覆盖新绑定，也不隐式修复或应用资格。

WorkBuddy settings summaries are display-only. Saved authentication and version facts do not certify current
credentials or installation integrity. Authentication actions, model saves and business dispatch retain their
existing complete server-side gates. Summary reads never sign in, probe a model or read SAP.

### SDK 调用后的认证上下文同步（离线候选）

WorkBuddy 官方 SDK 可能在调用中续期或重写共享认证缓存。缓存文件元数据变化仅表示
“需要官方复核”，不证明退出、身份改变或仍然认证有效。既有调用前复核保持不变。

候选修复在最后一个认证使用租约正常返回后，若已确认账号、站点、用户环境和认证来源未变，
且仅缓存观察值变化，后台合并一次现有官方只读复核；缓存未变不增加调用。
模型检查、目录刷新、模型保存和业务调用复用同一租约入口，不分别实现同步。
原 SDK 结果不等待该后台复核，也不因此延长模型预算。复核仍使用原 30 秒认证检查上限及
所属助手的原清理窗口；关闭服务时取消并纳入既有最多 10 秒清理等待。

官方结果与缓存观察一致且确认同账号后，才记录新的认证观察值；认证修订、模型兼容及
历史业务 PASS 保持原样。新请求在此之前必须等待同一复核并通过自身完整绑定检查，
不能因前一次 SDK 调用成功获得认证权限。真正换账号、换站点、换来源、401／登录失效、
结果不确定或清理未确认仍按原规则阻断，不自动登录、退出、重试或重发业务。
GET 只展示当前事实及可选 `authentication_sync` 状态，不发起认证操作。

此候选只证明离线认证生命周期修复；上线前须单独核对 SDK 基础设施绑定及目标测试证据，
不能直接沿用不匹配的接入认证。未改变模型协议或 worker 的事实，也不能代替完整准入检查。
Codex 的认证、调用与历史规则不采用此 WorkBuddy 专用后台同步。

The offline candidate reconciles cache-only drift after a completed managed WorkBuddy use through one
bounded, shared official read-only authentication check. A successful SDK result is never authentication
proof. Unchanged caches do not trigger checks; GETs never initiate one. Failed, cancelled or uncleared
operations remain fail-closed. Same-account confirmation preserves revisions and model/business history;
Codex behavior is unchanged. Deployment and target infrastructure certification are separate actions.

WorkBuddy 启动检查的单轮复用、安全围栏和活动索引边界见[本地启动检查](workbuddy-startup-checks.md)。该候选不跨请求缓存执行权，也不把未激活的活动索引作为“无待清理作业”的证明。

检查失败解除记录时先按实际案例、组件、环境及序号筛选受影响失败，再完整验证这些失败对应的证据；不反复打开无关 Campaign。相同目录输入的文件清单和摘要只在一次绑定计算中共享，离开调用即丢弃，并发调用互不复用；下一次准入仍重新读取。案例与输入摘要算法不变。

资格汇总与历史决策共用同一次完整 `inspection`，不在同一核对中重复校验已选原记录。状态计划展开当前有效记录和缺失项，标记 `source_audit=current_and_missing`；显式历史审计默认仍核对全部原记录。该结果不跨核对缓存，后续证据变化仍会被下一次完整校验拒绝。

SDK 状态 GET 仍立即返回展示投影，过期或正在检查时不能显示可启用。WorkBuddy 的完整后台核对即使超过展示等待上限，也只在配置指纹与磁盘保护绑定始终匹配时保存最终结果；保留 `completed_after_limit` 耗时标记，60 秒展示有效期从实际完成时计算。错误、绑定变化和服务关闭不接受迟到结果。此策略不改变 Codex、实际启用或任务派发的完整门禁，不增加模型或 SAP 调用。

应用启动构建集成适配器只读取 SDK 静态定义；Runtime 插件健康检查通过 `installation_status` 只检查目标 SDK 的安装信息，不枚举其他 SDK 的完整资格。安装／健康不等于模型兼容或业务准入，任务执行仍使用原认证、模型、权限及资格门禁。这样已有 WorkBuddy 资格不会反过来拖住 Codex 插件或整个 API 的启动。

## 准入基础设施的有限承接约束（候选规则）

前端展示、计时和事件订阅不进入模型业务资格摘要。前台可以通过启动器的 `-RestartSiteOnly` 独立预构建、验活和切换；失败保留或恢复旧页面，不重启 API、不取消查询，也不应用资格承接。

`admission_infrastructure_equivalence/1` 只处理经过审核的完整文件源／目标映射及对应准入职责拆分。它要求原始真实 PASS、源码归档、业务制品、模型身份、认证和模型检查来源完整；模型、站点、账号修订、worker、案例、提示、Schema、SDK 参数、工具范围、业务检查器、终态及业务预算必须保持一致。锁归属、上下文、取消、截止时间和本地进程清理须有目标源码绑定的离线／基础设施证明。未知变化、未解决业务失败或来源缺失时拒绝承接，不能用当前源码为旧记录补证。

本地测试进程只证明基础设施，不登记真实模型或业务 PASS。规则版本与业务契约分别维护；首次拆分后，准入调度职责使用独立完整组件摘要，不能通过 AST 切片或忽略代码规避变化。原始模型检查缺少探针来源时，保留其历史结论，但不得据此签发新基础设施承接凭据。

候选规则只有在逐案例审计通过、旧写入者退出并完成显式维护后才生效。维护批次绑定固定请求编号，先完整校验再原子追加基础设施记录及引用原 PASS 的凭据；任何一项失败均不部分应用。状态 GET、普通启动和前端部署不执行承接。无法安全承接时保留旧后端；回退保留台账，不改写历史 PASS，也不强制降级协议。

Frontend presentation changes do not invalidate model/business qualification. The candidate `admission_infrastructure_equivalence/1` rule requires exact whole-file transition maps, original live provenance, unchanged business/model bindings and source-bound local infrastructure evidence. Missing probe provenance blocks adoption; offline tests never become live PASS records. Apply only through an explicitly authorized, exclusive-writer atomic maintenance batch. Read-only status and frontend deployment never apply receipts.

## 验收约束

保存独立基线、验收自由查询、固定执行、比较和来源锚点的阶段制品。前段通过后段失败，前段制品不丢弃，但不能授予验收操作资格。
两项验收资格必须同时具备本轮完整 Campaign PASS、两路比较 MATCH、来源锚点 PASS、完整性和只读审计。每次新 Campaign 仍须新 SAP 证据及前后锚点；历史能力资格不代替这些证据。独立基线不能读候选源码、试运行答案或历史标准答案。

## 一次性协议迁移

新安装应按以下机制执行单次迁移；检查与验证 CLI 不提供隐式激活：

1. 核对所有旧 API、验证脚本及管理写入进程，确认退出；无活动任务不等于无写入者。
2. 持管理锁重新核对状态摘要及进程检查时效，追加协议标志和旧格式拒绝围栏。
3. 只恢复支持新协议的写入程序；无法证明旧写入者已退出则拒绝迁移。
4. 旧读写器对围栏报错，不可回退旧 PASS。保留新台账，回退只允许兼容只读检查，不强制降级格式。

本地文件哈希不是对拥有本机写权限攻击者的防篡改签名。以上是平台边界内的绑定与审计机制，不声称操作系统级隔离。

## 验证 Skill 与交付

仓库源码包位于 `skills/validate-ai-runtime/SKILL.md`。它只指导盘点、计划、工具调用、集中诊断和增量重验，算法仍在共用工具中。
源码包不会自动安装到个人目录。需要在支持此格式的宿主中显式加载／安装；其他 SDK 使用本文件和 CLI，不假定支持同一种 Skill。

离线测试使用模拟 Provider，覆盖依赖／授权、幂等、冻结、原始证据、承接、失败范围和协议互斥。按实际修改范围选择回归；前端发生变更时补前端／Astro／构建。
历史审计矩阵和后续最小真实验证清单是交付物，但不是自动执行指令。

## 共享报告的定向离线验证

报告契约、精确反馈与收敛状态机由 Codex、WorkBuddy 共用，必要的终态接收差异保留，详见[共享报告契约](shared-report-contract.md)。报告工具校验、原生终态和业务保存是不同检查，不能用候选通过代替全部成功。新增依赖组件按现有摘要算法正常纳入；离线测试不登记业务PASS、不改写旧PASS，也不自动应用接入认证或承接。

本轮报告专属回归允许补充明确的预期Schema／提示变化对照，但不得重写旧行为快照或删断言消除失败。无关的旧快照差异先在修改前源码核对并单独记录，不扩展为全量模型或SAP验证。

查询/金额候选增加 `tests/test_shared_query_money_chain.py`，以隔离Broker、合成证据及网络阻断验证查询契约、幂等、分阶段收敛、Decimal分组、金额凭据和原生交接。原历史终态摘要仍必须匹配原夹具；新增字段只在测试中显式重建旧Schema，不修改历史摘要或原记录。新增计算模块必须列入真实操作职责依赖，不修改摘要算法来维持旧资格。金额未验证及来源语义未证明必须保留业务缺口，结构通过不能取代业务正确性。

## English summary

### Shared acceptance date scope and failure reasons

Codex and WorkBuddy use the same frozen read-scope resolver. Explicit public inputs `posting_date_to` and `posting_date_from` mean inclusive calendar-day upper/lower bounds on `PostingDate`, not equality aliases. Full authoritative metadata must prove the field and date semantics (OData V2 `Edm.DateTime` with `display_format=Date`, or V4 `Edm.Date`). A proven subset is allowed; missing/wider predicates, unknown types, timezone/timestamp guesses and unreviewed DDIC mappings remain blocked. Neither the input nor the query plan is repaired, rewritten or stripped. Independent baselines do not borrow the candidate Agent plan.

Unsupported public-input mappings are platform scope-contract failures with value-free field paths and reasons, not generic model clarification or authentication failures. Invalid calendar inputs and genuine business questions are classified separately from execution-environment failures. Campaign reports preserve these reasons and cleanup status; historical reports are not rewritten. Offline coverage must enter through Campaign admission, the real scheduler, shared Harness and actual scope parser for both providers, mocking only vendor/SAP boundaries. Successful offline startup does not grant a business PASS or authorize retrying a live Campaign.

### Settings refresh is display-only

Runtime display polling follows stale/unknown intermediate states until completion, pauses while hidden, and stops automatic retries after a genuine read failure. For an unchanged binding, account identity and model compatibility retain their last confirmed information with an explicit pending label; stale projections never grant authentication actions, model saving, enablement or execution rights. The settings page reuses authentication information from the complete background projection instead of repeating installation/activity/context checks through an additional authentication GET. Authentication mutations and actual admission retain their original complete validation; Codex admission, model protocols and historical PASS records are unchanged.

Inventory historical evidence, freeze code/cases/authorization, run only eligible authorized cases, collect independent failures, then repair and incrementally revalidate. Preserve original PASS records. Historical success, current qualification and this batch's results are separate.
Each installation needs an explicitly approved, exclusive-writer protocol migration; inspect its saved state rather than inferring activation from this document. Codex admission and history are unchanged. Read-only planning does not call a model or SAP. Reuse requires original live evidence, exact identity, allowed changes and bound checks; it never crosses models/SDKs or replaces fresh Campaign evidence. Historical source recovery cannot invent missing original case/checker evidence.
