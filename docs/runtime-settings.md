# Agent Runtime与SDK / Agent Runtime and SDK

## 中文

打开“系统配置 → Agent Runtime与SDK”。固定Agent与确定性工作流不需要Runtime；自由查询、岗位匹配、反馈理解和自然语言编写需要可用的Runtime。

1. 在“账号与认证”中登录或刷新认证；退出、切换账号需要确认。安装与版本检查位于折叠的 SDK 详情。登录不会自动检查模型、启用或切换默认 Runtime，详见[统一账号与认证](runtime-authentication.md)。
2. 刷新模型目录。候选来自当前安装SDK及当前账号的非隐藏模型目录；数量、型号和默认值均可能变化。
3. 在模型行选择推理强度，候选来自SDK支持项，首次采用SDK建议默认值而非Codex全局设置。对模型与强度组合执行兼容性检查；检查不访问SAP或Skill，失败时区分认证、兼容性、权限和超时。点击“保存推理强度”不会切换默认模型。
4. 将通过检查的候选设为该Runtime的系统默认模型。目录不完整或过期时不能保存新选择。
5. 启用Runtime并选择默认Runtime；启用不会自动设为默认，停用默认Runtime会清空默认值，允许全部停用。

默认模型变化只影响新会话与草稿。已有会话、等待输入和已排队运行保留Provider、模型与推理强度快照；无静默回退。Agent修改对话是特例：每次接受提交时，沿用草稿绑定模型，读取该模型最新已验证强度，并固定到本轮。进行中的轮次不受设置变化影响。旧记录缺失强度显示“未记录”，不补写历史实际参数。模型、强度来源和版本可在运行技术信息中核对。

SDK升级与模型切换是不同操作。SDK更新后按页面提示重启API，重新读取SDK/CLI版本、刷新目录并检查兼容性；被移除的模型不能用于新会话。配置保存在忽略的本地Runtime状态中，不需要修改Codex Desktop全局模型或`.env`。旧SAPBA_CODEX_MODEL仅参与首次迁移，不持续覆盖页面设置。保留Provider各自的认证及平台验收门禁。

新版更新链路已于 2026-10-28 [受控上线](runtime-version-activation-deployment.md)：后续明确更新 Codex 时，在独立依赖环境准备，等待审核和空闲后由现有启动器受控激活；页面分别显示安装与实际生效。WorkBuddy 保留原独立更新流程。普通刷新只读取状态，响应未知时不重复提交安装。本轮只更新 API／前台及原环境证明，没有实际更新 SDK。

## English

Open System settings → Agent Runtime and SDK. Fixed Agents and deterministic workflows need no Runtime; free queries, role matching, feedback interpretation and natural-language authoring require an available Runtime.

1. Sign in or refresh authentication in “Account & authentication”. Switching or signing out requires confirmation; installation/version checks are in folded SDK details. Login does not probe a model, enable a Runtime or change its default. See [account and authentication](runtime-authentication.md).
2. Refresh the model catalog. Candidates are non-hidden models returned by the installed SDK for the current account; counts, IDs and defaults may change.
3. Select a reasoning effort on the model row from the SDK-supported options. The first choice uses its valid SDK default, not the global Codex setting. Check the model/effort combination; no SAP or Skill is called, and authentication, compatibility, permission and timeout failures are distinguished. Saving an effort does not switch the default model.
4. Choose a passing model as that Runtime's system default. Incomplete/stale catalogs cannot authorize a new selection.
5. Enable the Runtime and choose the default Runtime separately. Enabling does not automatically select it; disabling the default clears that selection, and all Runtimes may be disabled.

Default-model changes affect new sessions and drafts only. Existing sessions, waiting input and queued work retain their Provider/model/effort snapshot without silent fallback. Agent modification feedback is the exception: each accepted submission keeps the draft's bound model but freezes that model's latest verified effort for the new turn. Changes do not affect an executing turn. Missing historical efforts display “Not recorded”; actual past parameters are not fabricated. Inspect versions and binding origins in run technical details.

SDK updates differ from model selection. After an SDK update, restart the API when prompted, refresh SDK/CLI versions and catalog, then recheck compatibility. Removed models cannot start new sessions. Settings live in ignored local Runtime state; no Codex Desktop global model or `.env` edit is needed. Legacy SAPBA_CODEX_MODEL participates only in initial migration. Provider authentication and platform acceptance gates remain enforced.

## 展示状态与执行资格 / Display status and execution eligibility

健康接口的 `ok` 表示本地 API 存活。目录统计在后台更新，未知值为 `null`，并通过 `catalog_status` 标明过期/刷新状态；请求不遍历包或等待目录写锁。插件投影沿用已有状态，不启动新外部探针。

SDK 列表使用最多 60 秒的展示投影，带 `status_refresh.checked_at`、过期/刷新状态及安全错误码。每个 Provider 最多一个独立后台刷新；慢安装检查、失败 Provider 不阻塞整份列表。未知、过期或失败时不能显示为可启用。真正启用、验证和任务派发仍完整核对安装、账号、模型与操作资格，不以展示缓存授权。状态读取不自动登录或探针模型，也不初始化尚未安装的 WorkBuddy 状态。

Health `ok` means local API liveness. Background catalog statistics can be unknown (`null`) or stale, explicitly marked by `catalog_status`; the request does not scan packages or wait on the repository writer lock. SDK-list projections expire after at most 60 seconds, expose check time and safe refresh errors, and allow only one independent refresh per Provider. Unknown/stale/failed displays cannot enable a Runtime. Actual enablement, verification and dispatch retain full installation/account/model/operation gates. Display reads never initiate sign-in/model probes or initialize an uninstalled WorkBuddy registry.

WorkBuddy 的启停事实通过独立的 5 秒展示投影读取，`control_status` 标明修订、检查时间和未知状态，不等待完整资格核对。已启用但资格仍在核对时，页面显示“已启用 · 资格核对中”及确认式停用操作，不显示灰色启用按钮；读取失败不能被解释为已经停用。`status_refresh.running` 表示后台仍在执行，`wait_exceeded` 仅表示超过展示等待时间，不是模型超时或资格失败。可见页面退避读取状态，隐藏时暂停、恢复可见时同步；不重建账号／模型表单或自动发送探针。启停成功结果立即更新页面，旧修订及旧请求不能覆盖新结果。

WorkBuddy enablement facts use a separate five-second display projection. `control_status` identifies its revision, check time and unknown state without waiting for full qualification. An enabled Runtime remains visibly enabled while qualification is pending; it offers confirmed disablement rather than a disabled enable button. A failed read does not mean disabled. `status_refresh.running` denotes continuing background work; `wait_exceeded` is only a display wait, not a model timeout or failed qualification. Visible pages poll with backoff without rebuilding account/model forms or probing a model. Hidden pages pause and resynchronize when shown. Confirmed management responses update immediately and older revisions/responses cannot overwrite them. All execution gates remain unchanged.

SDK 更新使用共用进度弹窗；收起不取消，安装与生效分开，生效后仅局部读取相关状态。断连只核对原操作，不重复安装。详见 [更新进度与结果反馈](sdk-update-feedback.md)。
