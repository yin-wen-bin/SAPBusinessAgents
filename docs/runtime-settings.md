# Agent Runtime与SDK / Agent Runtime and SDK

## 中文

打开“系统配置 → Agent Runtime与SDK”。固定Agent与确定性工作流不需要Runtime；自由查询、岗位匹配、反馈理解和自然语言编写需要可用的Runtime。

1. 检查SDK、绑定CLI、平台支持与现有账号登录状态；按页面指引登录。
2. 刷新模型目录。候选来自当前安装SDK及当前账号的非隐藏模型目录；数量、型号和默认值均可能变化。
3. 在模型行选择推理强度，候选来自SDK支持项，首次采用SDK建议默认值而非Codex全局设置。对模型与强度组合执行兼容性检查；检查不访问SAP或Skill，失败时区分认证、兼容性、权限和超时。点击“保存推理强度”不会切换默认模型。
4. 将通过检查的候选设为该Runtime的系统默认模型。目录不完整或过期时不能保存新选择。
5. 启用Runtime并选择默认Runtime；启用不会自动设为默认，停用默认Runtime会清空默认值，允许全部停用。

默认模型变化只影响新会话与草稿。已有会话、等待输入和已排队运行保留Provider、模型与推理强度快照；无静默回退。Agent修改对话是特例：每次接受提交时，沿用草稿绑定模型，读取该模型最新已验证强度，并固定到本轮。进行中的轮次不受设置变化影响。旧记录缺失强度显示“未记录”，不补写历史实际参数。模型、强度来源和版本可在运行技术信息中核对。

SDK升级与模型切换是不同操作。SDK更新后按页面提示重启API，重新读取SDK/CLI版本、刷新目录并检查兼容性；被移除的模型不能用于新会话。配置保存在忽略的本地Runtime状态中，不需要修改Codex Desktop全局模型或`.env`。旧SAPBA_CODEX_MODEL仅参与首次迁移，不持续覆盖页面设置。保留Provider各自的认证及平台验收门禁。

## English

Open System settings → Agent Runtime and SDK. Fixed Agents and deterministic workflows need no Runtime; free queries, role matching, feedback interpretation and natural-language authoring require an available Runtime.

1. Check SDK, bundled CLI, platform support and account login; follow the login guidance.
2. Refresh the model catalog. Candidates are non-hidden models returned by the installed SDK for the current account; counts, IDs and defaults may change.
3. Select a reasoning effort on the model row from the SDK-supported options. The first choice uses its valid SDK default, not the global Codex setting. Check the model/effort combination; no SAP or Skill is called, and authentication, compatibility, permission and timeout failures are distinguished. Saving an effort does not switch the default model.
4. Choose a passing model as that Runtime's system default. Incomplete/stale catalogs cannot authorize a new selection.
5. Enable the Runtime and choose the default Runtime separately. Enabling does not automatically select it; disabling the default clears that selection, and all Runtimes may be disabled.

Default-model changes affect new sessions and drafts only. Existing sessions, waiting input and queued work retain their Provider/model/effort snapshot without silent fallback. Agent modification feedback is the exception: each accepted submission keeps the draft's bound model but freezes that model's latest verified effort for the new turn. Changes do not affect an executing turn. Missing historical efforts display “Not recorded”; actual past parameters are not fabricated. Inspect versions and binding origins in run technical details.

SDK updates differ from model selection. After an SDK update, restart the API when prompted, refresh SDK/CLI versions and catalog, then recheck compatibility. Removed models cannot start new sessions. Settings live in ignored local Runtime state; no Codex Desktop global model or `.env` edit is needed. Legacy SAPBA_CODEX_MODEL participates only in initial migration. Provider authentication and platform acceptance gates remain enforced.
