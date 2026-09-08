# Agent Runtime与SDK / Agent Runtime and SDK

## 中文

打开“系统配置 → Agent Runtime与SDK”。固定Agent与确定性工作流不需要Runtime；自由查询、岗位匹配、反馈理解和自然语言编写需要可用的Runtime。

1. 检查SDK、绑定CLI、平台支持与现有账号登录状态；按页面指引登录。
2. 刷新模型目录。候选来自当前安装SDK及当前账号的非隐藏模型目录；数量、型号和默认值均可能变化。
3. 对需要的候选执行兼容性检查。检查不访问SAP或Skill；失败时显示认证、兼容性、权限或超时原因。
4. 将通过检查的候选设为该Runtime的系统默认模型。目录不完整或过期时不能保存新选择。
5. 启用Runtime并选择默认Runtime；启用不会自动设为默认，停用默认Runtime会清空默认值，允许全部停用。

当前设置只影响新会话与草稿。已有会话、后续反馈、等待输入和已排队运行继续使用固定的Provider与模型快照；无静默回退。模型来源和版本可以在运行技术信息中核对。

SDK升级与模型切换是不同操作。SDK更新后按页面提示重启API，重新读取SDK/CLI版本、刷新目录并检查兼容性；被移除的模型不能用于新会话。配置保存在忽略的本地Runtime状态中，不需要修改Codex Desktop全局模型或`.env`。旧SAPBA_CODEX_MODEL仅参与首次迁移，不持续覆盖页面设置。保留Provider各自的认证及平台验收门禁。

## English

Open System settings → Agent Runtime and SDK. Fixed Agents and deterministic workflows need no Runtime; free queries, role matching, feedback interpretation and natural-language authoring require an available Runtime.

1. Check SDK, bundled CLI, platform support and account login; follow the login guidance.
2. Refresh the model catalog. Candidates are non-hidden models returned by the installed SDK for the current account; counts, IDs and defaults may change.
3. Check compatibility for your intended candidates. No SAP or Skill is called. Authentication, compatibility, permission and timeout failures are distinguished.
4. Choose a passing model as that Runtime's system default. Incomplete/stale catalogs cannot authorize a new selection.
5. Enable the Runtime and choose the default Runtime separately. Enabling does not automatically select it; disabling the default clears that selection, and all Runtimes may be disabled.

Changes affect new sessions and drafts only. Existing sessions, feedback, waiting input and queued work retain the pinned Provider/model snapshot, with no silent fallback. Inspect versions and model origin in run technical details.

SDK updates differ from model selection. After an SDK update, restart the API when prompted, refresh SDK/CLI versions and catalog, then recheck compatibility. Removed models cannot start new sessions. Settings live in ignored local Runtime state; no Codex Desktop global model or `.env` edit is needed. Legacy SAPBA_CODEX_MODEL participates only in initial migration. Provider authentication and platform acceptance gates remain enforced.
