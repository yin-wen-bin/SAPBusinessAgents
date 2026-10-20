# SDK 账号与认证 / SDK account and authentication

## 中文

在系统配置的每个 SDK 卡片中使用“账号与认证”。未登录时点击“登录”；已登录时可“切换账号”或“刷新认证”，“退出登录”是次要操作。Codex 和 WorkBuddy 均通过其官方认证协议执行，不要求在平台粘贴 API Key。未实现认证操作的 Provider 显示具体不可用原因，不提供假按钮。

登录操作显示等待状态；未自动打开浏览器时，可打开临时的官方登录链接。链接只存在于当前 API 进程内存中，不保存到操作记录、日志或浏览器存储。关闭页面不取消任务；刷新后恢复进度。API 重启后不会自动继续登录，需先刷新认证。认证操作上限 360 秒；取消或超时额外最多 10 秒用于所属进程清理。清理未确认时保持阻断，可刷新认证重新核对；不按进程名称结束其他客户端。

“登录成功”“模型兼容”“Runtime 启用”是三个不同状态。登录不自动调用模型、不启用 Runtime，也不切换默认 SDK。账号身份只有官方接口能够确认时才显示脱敏值，否则显示“身份未知”。额度默认显示“未核对”，不根据登录成功推测剩余额度。

退出和切换账号需要确认，因为官方 CLI 缓存可能与 Codex App、IDE 或其他本机客户端共享。平台活动模型任务或待清理进程期间禁止修改认证；认证操作期间禁止派发新平台模型任务。平台无法锁住其他客户端直接修改官方缓存。用户在外部切换账号后应刷新认证；平台只能识别官方接口实际提供的身份变化，不能保证识别未公开的账号身份。

确认的账号变化增加认证修订并使该 Provider 的账号相关模型检查失效；不会删除 SDK 环境、历史操作验证、业务验收或历史对话。既有会话、草稿、队列和 Campaign 保持原账号修订绑定，不能静默改用新账号；请创建新会话或使用后续明确迁移流程。旧记录缺少可靠账号绑定且账号已经变化时明确阻断，不推定有效。

API Key / 外部认证配置继续由官方客户端或外部配置管理。平台只识别可确认的认证方式和不可用原因，不收集密钥、不解析或删除凭据文件。WorkBuddy 使用已验证独立解释器和 CLI；本功能不修改其锁定 release、SDK 或 CLI。

WorkBuddy 认证进程使用与现有 worker、命令行登录入口相同的 `-I -B` 参数，避免 Python 导入产生缓存而改变锁定安装的文件清单。认证前后仍执行原完整性检查；安装摘要、模型及操作资格门禁不会被重写或绕过。

### WorkBuddy 服务站点

系统配置的 WorkBuddy 卡片提供“国内站／国际站”和“保存站点”。新配置及旧配置首次迁移均采用国内站；下拉选择不会立即登录，保存也不会退出原站点账号或删除官方缓存。WorkBuddy 必须停用且不是默认 Runtime，相关作业、认证及清理必须结束后才能保存。活动状态阻止首次迁移时，先处理阻碍再保存；不能使用未迁移绑定启动调用。

国内站使用官方 `internal` 环境，国际站使用海外环境。登录、刷新认证、退出、切换账号及模型执行共享冻结的站点、修订和摘要。站点与认证方法 ID 不同；`external` 认证方法不能代替站点配置。登录 URL 必须匹配所选官方站点。外部 Key 或自定义端点由外部配置管理，不能在此覆盖。

保存后先刷新认证或登录，再检查所选站点可用的模型和当前业务操作。登录成功不证明模型权限、额度或完整启用资格。切回原站点也不自动恢复旧资格；历史结果保留，缺少站点绑定的历史会话仅供查看。模型探针不访问 SAP，但会使用对应账号的模型服务。服务切换不会改变 Codex。

worker 站点协议更新必须创建新的不可变 release，使用原 SDK／CLI 及依赖锁；旧 release、摘要和资格证据不改写。日常站点切换不创建 release。命令行登录脚本读取同一已保存配置，未完成迁移时明确拒绝。

## English

### WorkBuddy service site

The WorkBuddy card offers China / International with an explicit Save site action. New configurations and the first guarded migration of legacy configurations default to China. Selecting an option does not sign in; saving does not sign out the former account or delete official caches. WorkBuddy must be disabled, not selected as default, and free of active jobs/authentication or unconfirmed cleanup. If migration is blocked, resolve the blocker before saving; unbound calls cannot start.

China uses the official `internal` environment; International uses the overseas environment. Authentication initialization, sign-in, refresh, sign-out, account switching and model execution share a frozen site/revision/digest. An authentication method ID is not a site selector. Returned login URLs must match the selected official site. External keys/custom endpoints remain externally managed and cannot be overridden here.

After saving, refresh authentication or sign in, then verify models and business operations for that site. Switching back does not revive old checks; historical evidence is preserved and histories without site bindings remain view-only. Login does not prove model permission, quota or Runtime readiness. A model probe uses the account's model service but never SAP. Codex is unaffected.

The site-aware worker requires a new immutable release with the same pinned SDK/CLI and dependency lock; old releases/digests are not rewritten. Ordinary site switches never rebuild an environment. Console sign-in reads the same saved configuration and rejects incomplete migration.

Use **Account & authentication** in each SDK card. Sign in when signed out; when authenticated, switch account or refresh authentication, with sign-out as a secondary action. Codex and WorkBuddy use official authentication protocols. Unsupported Providers show unavailable reasons. No platform API-key entry or credential storage is introduced.

Sign-in progress survives a page reload. A temporary official link is available when automatic browser opening fails; it stays only in API-process memory, never durable operations, logs or browser storage. Closing the page does not cancel the operation. An API restart does not automatically resume login: refresh authentication first. Operations have a 360-second deadline and at most 10 additional seconds for owned-process cleanup. Unconfirmed cleanup blocks retries until explicitly rechecked. Other clients are never killed by process name.

Authentication, model compatibility and Runtime enablement are separate. Login never probes models, enables a Runtime or changes the default Provider. Account identity is masked when the official interface supplies it; otherwise it remains unknown. Usage remains “not checked”; sign-in does not prove available quota.

Sign-out and switching require confirmation because the official CLI cache may be shared with desktop/IDE clients. Platform model activity and unconfirmed cleanup block credential changes; authentication operations block new platform model dispatch. External clients cannot be locked by this platform. Refresh authentication after an external account change; identity changes can only be detected when the official interface actually supplies a reliable identifier.

Confirmed account changes increment an authentication binding revision and invalidate account-dependent model checks for that Provider only. SDK environments, historical operation evidence, business acceptance and conversation history remain intact. Existing conversations, drafts, queued messages and Campaigns retain their original revision and cannot silently use the new account. Old records with no reliable binding are blocked after an account change.

External/key authentication remains managed by official clients or external configuration. No keys are collected, and credential files are not parsed or deleted. WorkBuddy authentication uses its verified isolated interpreter and CLI without altering its locked release.

WorkBuddy authentication uses the same `-I -B` interpreter flags as the worker and console sign-in launcher, preventing imports from adding bytecode caches to the locked installation. Existing pre/post authentication integrity checks and model/operation gates remain enforced; installation digests are never rewritten to accept changes.

## Internal interface

All paths are under `/api/system/sdk-runtimes/{provider_id}/authentication`:

- `GET /`: safe account projection, no implicit probe.
- `POST /check`: explicit authentication check, no model task.
- `POST /operations`: `action`, required `requestId`, and matching `confirm_provider_id` for logout/switch; returns `202` and `operation_id`.
- `GET /operations/{operation_id}`: recover progress; responses use `Cache-Control: no-store`.
- `POST /operations/{operation_id}/cancel`: cancel only the owned operation.

Mutations require `X-SAPBA-Action: runtime-authentication` and an allowed UI Origin (if present). Request IDs are idempotent; conflicting content returns `409`. Validation errors never echo request values. Account state and safe operations use local SDK Runtime state storage, not a new database table. Login URLs and credentials are excluded.

WorkBuddy adds `PUT /api/system/sdk-runtimes/workbuddy/service-site` with `service_site` (`china` / `international`), `expected_revision` and `requestId`. It uses the same mutation headers and returns a frozen configuration. Authentication requests may include `service_site_digest`; the UI always sends it and uses `X-SAPBA-Service-Site-Digest` for refresh checks. A stale site is rejected before authentication. Historical request IDs cannot be reused at another site. Installation and service-site digests are separate.

Offline regression uses fake adapters and SDK messages. Real sign-in, account switching and sign-out require user-confirmed manual verification; offline success is not proof of current login, model availability or quota.

## WorkBuddy 续期失败与状态同步 / Renewal failures and synchronization

### 无 401 的早期认证失败 / Early failure without HTTP 401

锁定 CLI 明确返回 `Authentication required. Please use /login command to sign in to your account` 时，同样属于认证失败，但不伪造 HTTP 401。普通模型文字、SAP 错误和任意日志片段不属于此信号。当前所属作业的明确错误可同步认证可用性，仍不得覆盖更晚的官方复核或改变账号修订、历史模型检查和业务 PASS。

一般 trace 仍要求原请求摘要。仅在初始化过早失败、trace 完全没有输入时，可采用单独的严格联合证明：Job Object 内锁定 CLI 映像及 PID 创建标识、当次失败 trace、唯一执行工作目录、同日该工作目录日志和精确 PID/时间对应的 CLI Error 记录全部成立。缺少任何一项均不停止任务。日志和 trace 共用每轮最多 16 文件、8 MiB 读取上限，不保存原文；trace 先落盘、日志后落盘时继续有界核对，不能把第一次未看到日志永久缓存为无错误。

English: an exact owned-CLI login-required error is not assigned a fabricated HTTP status. Input-free early traces need an additional matching workspace log, PID/birth/image/Job Object ownership and time window. Normal traces retain prompt-digest checks. Reads share the existing per-poll budget; raw logs and credentials are never persisted. This detects failure promptly; it does not manufacture valid credentials or prove future server-side renewal cannot fail.

WorkBuddy 官方认证缓存只观察文件身份、大小和修改时间，不读取、解密、复制或删除凭据内容。元数据变化只表示需要官方复核，不证明账号切换，也不直接使模型或业务 PASS 失效。GET 保持只读，不发起登录或认证探针；实际执行取得认证租约前，必要时复用现有官方检查。确认同一账号的正常令牌续期不增加账号修订；确认不同账号时继续阻断旧修订绑定，不能静默迁移。

认证检查失败时保留原身份依据和最后成功检查，当前可用性标为未知／失效；不能把续期失败解释成从未登录或供应商无数据。401 明确标记认证需要处理；其他续期故障保持独立分类。旧任务的错误不得覆盖其启动之后完成的新官方检查，SAP 自身返回的 401 不得改写 WorkBuddy 账号状态。

WorkBuddy API 与认证 helper 使用版本化内部上下文协议，核对源码和用户目录摘要，使用与命令行登录一致的用户目录选择；用户目录还须与当前 Windows 令牌的 Known Folders 一致。目录不一致或 AppContainer 上下文会明确拒绝，不通过重写路径逃离隔离，不传递连接凭据。源码在 API 运行后被替换、或 helper 与 API 协议不一致时明确拒绝，而不是报告未登录。此防护不证明此前旧 API 差异的精确历史成因。部署必须在维护窗口成套切换；不能将候选 helper 单独覆盖到仍运行旧代码的 API。

锁定 SDK 未发送错误终态时，Supervisor 可以利用所属 worker 的安全认证错误信号，以及已验证 CLI `2.141.0` 的终态 trace。trace 必须同时匹配本作业 Job Object 内的实际 CLI 映像、PID 创建标识、本轮时间和原请求摘要；被取消的输入子项还必须能在同一 trace 中追溯到明确失败的父项，拒绝无关错误及循环关系。仅有文本中的 401、其他作业、过期 PID 或不完整 trace 不足以停止任务。只在内存中解析并输出白名单错误码、归属及状态，不保存原文。每秒至多一轮、最多 16 个文件和 8 MiB 总读取，目录项也有上限，不扫描全局历史作业。

明确错误立即进入现有失败与最多十秒清理流程，原始错误和清理结果分开记录；不自动登录、重试、延长预算或登记资格。没有可靠错误信号时仍使用原截止时间，不将“暂时没有助手消息”猜测为 401。未知 CLI 版本、关闭／无法读取 trace 或归属无法确认时，回退到原 SDK 消息和截止时间规则；不能宣称已消除所有上游静默故障。

本功能不修改或重装 SDK、CLI、worker，不改变 Codex 的命令、环境变量和认证逻辑。基础设施变化需审核目标测试及认证，不能直接沿用旧证书，也不因此自动重跑 15 项业务评测或改写历史 PASS。

English: WorkBuddy observes official-cache metadata only. A metadata change triggers a scoped official recheck at execution, not a login, identity change, model invalidation or business evaluation. Same-account renewal retains the revision; changed identity fences old bindings. Authentication failures preserve historical identity/proof and last-success metadata. A current owned-job 401 can invalidate readiness, but never overwrites a later official check or treats a SAP 401 as a WorkBuddy account failure.

The versioned API/helper context handshake checks source/user digests without credentials. Mixed old/new implementations fail explicitly and require an atomic maintenance rollout. A silent locked CLI can be stopped on a strictly owned, time/prompt-bound terminal-error trace or a bounded authentication error signal, with original cleanup deadlines. Unknown/unavailable signals retain the original timeout; no short silence timer, automatic login or query retry is added. Codex and installed SDK/CLI/worker remain unchanged. Offline success is not live renewal verification or a guarantee against future provider-side errors.
# WorkBuddy 初始化与草稿绑定恢复（2026-10-18）

官方 `initialize` 没有返回完整账号（`userId` / `token`）时，只能说明本次认证未被确认。平台阻断执行并记录 `workbuddy_authentication_confirmation_required`，不能据此判定用户主动退出、推进账号修订或覆盖最后一次成功身份。显式退出仍使原绑定失效；401、续期失败与运行环境变化继续分别阻断。

认证检查期间缓存元数据变化时，不把未被本次结果覆盖的新缓存标记成已确认；后续使用前仍需官方复核。元数据不是凭据或账号身份，不读取、复制或解密官方认证缓存。

对旧版误推进认证修订的记录，仅允许显式维护操作追加同账号恢复凭据：原始及当前官方身份摘要一致、站点与修订相同、worker 完整性相同、原模型探针的真实作业与结构化终态可核验。原模型检查及历史业务 PASS 不改写，未知身份、跨账号、跨站点、跨模型、证据缺失或清理未完成均不可承接。

只有新的草稿反馈请求会解析可用的恢复凭据；队列及运行中绑定不自动迁移。修改成功时，新绑定与新草稿修订同一事务保存。前台单独提供认证复核，不代用户重发修改请求。
