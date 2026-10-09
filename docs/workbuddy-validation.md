# WorkBuddy 实施与验证记录 / Implementation and validation record

最近检查日期 / Latest check date: **2026-10-08**。以下分阶段记录按其标注日期保留；最新状态见文末 / Dated stage records are retained; see the final section for current status.

## 2026年10月4日续接检查 / Continuation checks on 2026-10-04

本轮继续原项目的独立接入，未启用 WorkBuddy。新增安装清单及管理状态的结构检查：即使 JSON 语法合法，错误字段类型也会阻断 WorkBuddy，而不会使共享 Runtime 列表或 Codex 配置失效。安装清单中的环境 ID 必须与目录摘要一致；无法读取安装时明确返回 `workbuddy_environment_access_denied`，不将 Windows 账户权限问题误报为未安装。

The original project's isolated integration remains disabled. New structural validation rejects malformed manifests or management state even when they are valid JSON, without breaking shared Runtime discovery or Codex configuration. The embedded environment ID must match its release digest. Installation access failures now return `workbuddy_environment_access_denied` rather than presenting a Windows account-permission issue as a missing installation.

新增 [锁定 CLI 登录脚本](../scripts/login-workbuddy-runtime.ps1)，系统配置页提供相同命令与双语提示。`-VerifyOnly` 在本机用户权限下核验通过：SDK `0.3.247`、CLI `2.141.0`、环境摘要仍为 `9ef9e0b9ec1b1e785df91078253146e25cb0456c77ae197ea3660b95b99db309`。该检查没有启动登录。用户登录前的实际探针为 `login_required / workbuddy_existing_login_unavailable`；WorkBuddy App 的登录不能代替这项证据。同日用户完成修正后的浏览器认证，最新结果见“登录确认 2026 10 04”；具体模型及业务操作仍待验证。

A [pinned CLI sign-in script](../scripts/login-workbuddy-runtime.ps1) and bilingual System Settings instructions were added. Its `-VerifyOnly` mode passed under the local Windows user: SDK `0.3.247`, CLI `2.141.0`, and the same environment digest. This verification did not start sign-in. Before user authentication, the actual probe returned `login_required`; WorkBuddy App sign-in does not substitute for this evidence. The user subsequently completed the repaired browser flow on the same date; see the final sign-in confirmation section for current results. Model and business-operation checks remain pending.

| 本轮检查 / Check | 结果 / Result |
|---|---|
| WorkBuddy 隔离、反馈、Runtime 分发及 SDK 管理 / isolation, feedback, routing and management | 80 passed，包括新增 22 项边界及 PowerShell 检查 / including 22 new boundary and PowerShell checks |
| 相关平台、Codex 执行、编写、选样、匹配及工作流回归 / related platform, Codex execution, authoring, sampling, matching and workflow regressions | 沙箱下 258 passed、1 failed（634.39s）；唯一失败为 DPAPI 权限，该用例用本机用户权限单独复测 1 passed / sandbox: 258 passed, 1 failed; the sole DPAPI permission failure passed on a separate local-user rerun |
| 前端全量及生产构建 / frontend suite and production build | 107 passed；构建通过 / build passed |
| Astro | 49 files，零错误、警告、提示 / zero errors, warnings or hints |
| Manifest / catalog | 34 manifests、32 catalog records validated |
| Codex 脱敏前后对照 / redacted controls | `continue-20261004-before.json` 与 `continue-20261004-after.json` 完全一致：提交、平台依赖、Codex 源码与配置摘要未变 / identical commit, platform dependencies, Codex source and configuration hashes |
| 登录脚本 / sign-in helper | 原直接启动 headless CLI 的脚本只完成安装校验后退出，未开启登录；已改为独立 SDK 认证控制流程，真实登录仍待用户完成 / direct headless launch exited after inventory verification without starting login; replaced by isolated SDK authentication control flow; user sign-in remains pending |
| 文档索引 / documentation index | 未通过：仍存在 `ar-collection/README.md` 的失效 `tests` 链接及过期受控块；未修改已发布包 / existing broken `tests` link and stale controlled block; published package unchanged |

保留既有 Starlette 弃用警告。上述结果不是全量 Python 或远端 CI 通过证明，也不代替 Python 3.11 / 3.13、未安装 WorkBuddy 的干净平台验证和实际窄屏、键盘浏览器验收。本轮未进行外部模型或 SAP 调用、环境升级、服务切换、业务对象发布、Git 提交或推送；当前前台稳定构建未切换。

The existing Starlette deprecation warning remains. These results do not certify the full Python suite, remote CI, supported Python versions, a clean platform without WorkBuddy installed, or actual narrow-screen/keyboard browser acceptance. No external model or SAP calls, environment updates, service switches, business-object publication, Git commits or pushes were performed; the active stable frontend build was not switched.

### 登录修正 2026 10 04

用户报告旧脚本未打开浏览器并立即返回 PowerShell。核对锁定 SDK 的公开 `auth.py` 和传输实现后，改为显式 `initialize → authenticate → auth_url_callback → auth_result_callback` 流程，补齐该 CLI 必需的 `--print`。不修改 SDK 安装，不启动交互 Agent、不发送模型任务。认证回调成功后另启 CLI 核对持久化登录；静默退出不能记作成功。

The user reported no browser and an immediate return to PowerShell. The pinned SDK's public authentication and transport implementations support the explicit control flow above; the helper now supplies the CLI-required `--print`. Installed SDK files are unchanged. No interactive Agent or model task starts. A fresh CLI checks persisted login after the successful callback; silent exit is never success.

修复后真实执行 `-CheckLogin`，得到明确 `workbuddy_login_required`，不是超时或静默成功；该检查不启动 OAuth。浏览器登录仍需用户重试完成，未写入认证成功、模型或操作验证资格。

The repaired helper's actual `-CheckLogin` returned `workbuddy_login_required`, not a timeout or silent success; this check does not initiate OAuth. Browser sign-in still requires the user's retry. No authentication success, model certification or operation qualification was recorded.

登录协议、WorkBuddy 隔离与反馈、Runtime 分发和 SDK 管理定向回归：105 passed，保留现有 Starlette 弃用警告。覆盖静默退出、认证回调、凭据持久化、浏览器回退、超时和拒绝诊断；不以模拟浏览器或离线回调代替真实用户登录。

Targeted login-protocol, WorkBuddy isolation/feedback, Runtime routing and SDK-management regressions: 105 passed, retaining the existing Starlette warning. They cover silent exit, callbacks, credential persistence, browser fallback, deadlines and rejection diagnostics. Simulated browsers and offline callbacks do not replace actual user sign-in.

## 交付状态 / Delivery status

平台代码及离线边界检查已实现，锁定 SDK 已完成独立安装及本机登录；**尚未达到上线门禁**。平台已确认 CLI 登录可复用，具体模型及业务操作仍待验证；WorkBuddy 保持禁用、不可选择，Codex 仍为默认。本记录不是业务操作或 SAP 验收证书。

Platform implementation, offline boundary checks, pinned isolated installation and local sign-in are in place; **rollout gates are not satisfied**. The platform confirms reusable CLI authentication; specific models and business operations remain unverified. WorkBuddy remains disabled and unselectable, with Codex as the default. This record is not business-operation or SAP acceptance certification.

基准提交 / Baseline commit: `2a815cf62c4efe86c6bc2945dcea643212eda44e`，正式目录保持 / checkout remains on `main`.

## 本机检查 / Local checks

| 检查 / Check | 结果 / Result |
|---|---|
| Windows 全量 Python 回归 / full Python regression | 1311 passed, 1 failed, 1 skipped（904.32s）；现有 Starlette 弃用警告 / existing deprecation warning |
| 上轮定向回归 / previous targeted regression | 116 passed：隔离、反馈、选样、Runtime 分发和 SDK 管理 / isolation, feedback, sampling, routing and SDK management |
| 本轮新增安装与认证回归 / continued installation and authentication regression | 58 passed：认证传输、候选发现、登录门禁及管理隔离 / authentication transport, candidate discovery, sign-in gate and management isolation |
| 前端 / frontend | 106 tests passed |
| Astro | 49 files, zero errors/warnings/hints |
| Manifest / catalog | 34 manifests and 32 catalog records validated |
| 前端生产构建 / production site build | Passed |
| Python 编译与 Diff 检查 / compilation and diff check | Passed；Git 换行提示不代表内容校验失败 / Git line-ending notices are not validation errors |

上轮追加的 WorkBuddy 报告诊断、查询预检、固定 CLI 路径、排队取消和受控时钟测试包含在 116 项定向回归中。本轮另覆盖登录协议、脱敏、模型候选与新环境失效，不以定向数量推算全量通过数。

The previous 116-test run includes WorkBuddy report diagnostics, query preflight, explicit CLI binding, queued cancellation and controlled-clock tests. The continuation also covers sign-in transport, redaction, candidate discovery and environment invalidation. Targeted counts are not used to infer a full-suite result.

全量唯一失败为 `test_generated_documentation_is_current_and_linked`：`agents/FI/ar-collection/README.md` 包含指向 `tests` 目录的本地链接，但该目录不存在。该 README 及文档生成器本轮均未修改；保留失败，不修改已发布包、重写摘要或移除断言来掩盖它。文档检查仍未通过。

The sole full-suite failure is `test_generated_documentation_is_current_and_linked`: `agents/FI/ar-collection/README.md` links to a nonexistent `tests` directory. Neither that README nor the documentation generator was changed. The failure remains visible; no published package, digest or assertion was altered to hide it. Documentation checks remain failing.

## Codex 对照 / Codex controls

前后脱敏记录保存在本地忽略目录 `.local-data/workbuddy-baseline/before.json` 与 `after.json`。核对结果：

- 已安装依赖名称与版本完全一致，未安装、卸载或升级现有平台包。
- `config/sdks.json`、Codex Planner、Codex 编写执行器、Runtime 执行器及本地默认 Runtime 配置摘要一致。
- `pyproject.toml` 的预期差异为移除必装 WorkBuddy 依赖、声明独立适配器所需的版本化数据资源。
- 公共入口增加 WorkBuddy 条件分支，未修改 Codex 私有提示、SDK 参数生成或原有 Codex 测试断言以消除回归。

Redacted before/after controls are stored locally in the ignored `.local-data/workbuddy-baseline/` directory. Installed dependency names and versions, Codex registry/private executors/planner and default selection hashes match. The intentional `pyproject.toml` changes remove the mandatory WorkBuddy dependency and package versioned adapter data. Shared entry points gain conditional WorkBuddy dispatch; Codex private prompts, SDK argument generation and existing Codex assertions were not relaxed.

这些结果证明本轮已检查的离线对照，不代表同机资源完全隔离、全部真机行为已证明零回归或远端 CI 已通过。

These results cover the tested offline controls, not complete machine-resource isolation, proven zero regression for every live behavior or a successful remote CI run.

本轮前后对照另保存在 `resume-before.json`、`resume-after.json`：平台依赖、全部基线文件摘要及 Git 提交均一致。独立安装及 worker 更新没有通过平台解释器安装或运行 SDK。

Continuation controls are stored in `resume-before.json` and `resume-after.json`: platform dependencies, all baseline file hashes and the Git commit match. SDK installation/execution used the isolated interpreter, not platform Python.

## 安装与安全探针 / Installation and safe probes

- 已核对历史官方 Windows wheel，SHA-256：`1189d1f06adc3358e62e134bcd704c8ea2089cb271addd90f8f924d1c25fd398`。当前 PyPI 索引及版本 JSON 不再列出该版本，但历史官方文件下载成功；原因未知。
- 独立安装：SDK `0.3.247`，内置 CLI `2.141.0`；本轮最终环境摘要 `9ef9e0b9ec1b1e785df91078253146e25cb0456c77ae197ea3660b95b99db309`。旧环境保留，未原地覆盖。
- 首次登录检查曾超时，保留失败及清理记录。锁定 SDK 传输缺少 CLI stream-json 所需的 `--print`；独立 worker 显式补齐并使用严格 MCP 配置，改为核对已有登录态，不自动启动完整 OAuth 流程。
- 修正后的真实检查为 `login_required / workbuddy_existing_login_unavailable`，作业清理完成。认证结果不包含账号、Token 或登录 URL。
- 显式模型发现从锁定 CLI 帮助读取 17 个候选 ID，来源 `locked_cli_help`、完整性为 false，全部未经账号可用性检查。未发起外部模型或 SAP 查询，也未写入操作 PASS 记录。

The historical official Windows wheel was downloaded and SHA-256 verified. It is no longer listed by the current PyPI index/version JSON; the publisher's reason is unknown. Isolated SDK `0.3.247` and bundled CLI `2.141.0` are installed in the release above, retaining older releases. The first authentication check timed out and its cleanup record was retained. The pinned transport lacked the CLI's required `--print` flag for stream-json; the independent worker now supplies it, enforces strict MCP configuration and checks existing login rather than starting OAuth. The actual result is `login_required`, with confirmed cleanup and credential-free diagnostics. CLI help yielded 17 unverified candidate model IDs, not a complete or account-verified catalog. No model/SAP query or operation certification was performed.

## 未通过的上线门禁 / Pending rollout gates

1. 锁定 CLI 登录已于 2026-10-04 完成，平台核对为 `authenticated=true / existing_login`。更换环境或账号后仍按[本机登录说明](workbuddy-runtime.md#本机登录--local-sign-in)重新核对；不在聊天或平台配置中接收密码、Token。
2. 登录后完成具体模型身份检查及全部操作的实际验证；包括 `workflow_authoring.v2` 和单案例三级验收。安装成功、CLI 声明的模型名单及离线测试都不代替这些门禁。
3. 在未安装 WorkBuddy 的全新平台环境完成 Codex 对照；CI 已配置该检查，但未提交或触发远端运行。本机保留实施前已存在的 WorkBuddy 包，不自动卸载。
4. 完成 Windows Python 3.11 / 3.13、Node 22 的 CI 验证，以及实际键盘、双语和窄屏浏览器验收。本机 Python 为 3.14；静态/前端测试不替代这些检查。
5. 单独处理上述现有文档索引失败。取得全部门禁证据且没有相关活动任务后，再执行受控平台重启；不得提前显示 WorkBuddy 可用。

CLI sign-in was confirmed on 2026-10-04. The remaining gates are model identity and real operation validation, a clean platform without WorkBuddy installed, declared-version CI and live browser/accessibility checks, and the pre-existing documentation failure. A downloaded wheel, successful installation, sign-in or candidate list is not certification. Local Python 3.14 and offline tests do not substitute for those gates. A controlled restart is deferred until the gates pass and relevant tasks are idle.

## 登录确认 2026 10 04

用户完成修复后的浏览器登录后，独立管理器实际认证检查返回 `authenticated=true / existing_login`，绑定环境 `9ef9e0b9ec1b1e785df91078253146e25cb0456c77ae197ea3660b95b99db309`。显式刷新从锁定 CLI 帮助读取 17 个模型候选；目录不完整，全部尚未完成账号可用性或模型身份验证。当前未选择 WorkBuddy 默认模型，未启用 Runtime。

After the user completed browser sign-in, the independent manager's actual authentication check returned `authenticated=true / existing_login` for the environment above. Explicit discovery read 17 candidate models from pinned CLI help; the catalog is incomplete and no candidate has passed account-availability or model-identity checks. No WorkBuddy default model was selected and the Runtime remains disabled.

`login-20261004-before.json` 与 `login-20261004-after.json` 完全一致：Codex 源码及配置、平台依赖与 Git 提交未变。仅更新 WorkBuddy 独立认证、候选目录及管理作业记录；没有模型或 SAP 调用、服务重启、业务发布、Git 提交或推送。

The redacted before/after controls match exactly: Codex source/configuration, platform dependencies and Git commit are unchanged. Only independent WorkBuddy authentication, candidate discovery and management-job records were updated. No model or SAP call, service restart, business publication, Git commit or push occurred.

本轮没有 SAP 或外部模型调用、服务重启、业务 Agent 发布/启用、Git 提交或推送。历史记录保持不变。

No SAP/external model calls, service restart, business Agent publication/activation, Git commit or push were performed. Historical records remain unchanged.

## 模型身份门禁及具体模型探针 2026 10 04

本次续接修复了路由别名被误标为具体身份的问题。身份仅来自锁定 SDK 的 `AssistantMessage.model`；初始化消息不能覆盖答复身份，同轮出现不同模型时保持未知。新检查、历史检查投影、正式验收新绑定及保存绑定使用相同规则；历史模型检查及验收记录不重写。

This continuation fixes routing aliases being treated as concrete identities. Identity comes only from the pinned SDK's `AssistantMessage.model`; initialization metadata cannot overwrite it, and conflicting identities remain unknown. New checks, historical projections and both new/saved formal bindings share these rules without rewriting historical checks or acceptance.

- 定向离线回归：129 passed，1 项既有 Starlette 弃用警告。覆盖别名、未知/非法身份、来源缺失、历史错误标志、消息来源与身份漂移，以及 WorkBuddy/Codex 管理隔离、反馈、登录及 Runtime 路由。
- 以同一核验 SDK 0.3.247 和 CLI 2.141.0 构建新独立环境 `0ff7575be60a4cac86c4d77d88d9a7be13c8d342211b617e3391e43a5b1d83c4`，保留旧环境，未升级 SDK/CLI。新环境已有 CLI 登录核对通过。
- 对 `gpt-5.6-sol` 执行一次无工具、无 SAP 的模型探针：`compatible=false / workbuddy_execution_failed`，实际身份未知。现有 SDK 失败信息在执行适配中被收敛为通用错误码，不能据此断言模型无权限、配额不足或服务中断；没有重跑或自动改用其他模型。
- 没有保存 WorkBuddy 默认模型，没有启用 Runtime。18 项首期操作仍未取得真实验证资格，未发起 SAP 查询、正式验收、业务发布、Git 提交或推送。
- `model-identity-before.json` 与 `model-identity-after.json` 完全一致：Codex 受保护源码/配置、平台依赖和 Git 提交未变。本次模型诊断保存在忽略的 `.local-data/workbuddy-baseline/model-identity-probe.json`。
- 前述 `ar-collection` 文档索引问题已在此前单独修复；旧失败记录保留，不作为当前仍未解决的门禁。
- 增加文档回归后的定向套件为 133 passed，1 项上述既有警告；文档检查 55 项、0 错误、0 过期受控块。
- 核对无活动作业后仅受控重启 API，使新身份门禁生效；API 健康正常，前台进程保持不变，Codex 仍为默认 Runtime。`model-identity-after-reload.json` 与原对照一致。此前岗位匹配的 4 条残留 queued 回合对应作业已于 9 月取消，本轮仅读取核对，没有改写这些历史记录。

The identity gate is active after a controlled API-only restart with no active jobs. API health passes, the frontend process is unchanged, Codex remains the default, and post-reload controls still match. Four historical queued matching turns were confirmed to belong to already cancelled September jobs; those records were not rewritten.

新环境的显式 CLI 目录刷新返回 21 个候选（`locked_cli_help`，目录仍不完整）；仅证明声明目录可读，不证明账号权限或底层具体身份。模型探针失败后没有再次调用模型。WorkBuddy 基线制品读取也使用同一身份门禁，不能凭制品内自称 `model_identity_known=true` 为别名或来源缺失的身份背书。

Explicit CLI discovery in the new environment returned 21 candidates (`locked_cli_help`, still incomplete), not proof of account access or underlying identity. No model was called again after the failed probe. WorkBuddy baseline artifact validation also shares the identity gate; a self-declared true flag cannot certify a route alias or an identity without its source.

最终定向回归为 135 passed，另有契约、投影、来源锚点及 Campaign 离线回归 114 passed，合计 249 passed。仅有上述 1 项既有 Starlette 弃用警告。等待用户确认账号可用的具体模型，再继续下一项真实验证；没有因当前失败切换模型、修改预期或伪造操作资格。

Final targeted regressions pass 135 tests plus 114 offline acceptance/contract/projection/anchor/campaign tests (249 total), with only the existing Starlette deprecation warning. Further live validation awaits a concrete model usable by the account; no automatic model fallback, expectation changes or fabricated capability qualification occurred.

The targeted offline suite passes (129 tests, one existing Starlette deprecation warning). A new immutable release retains the same verified SDK/CLI versions and the old release. Existing login passes in the new environment. One tool-free, SAP-free probe for `gpt-5.6-sol` returns `workbuddy_execution_failed`, with no concrete identity. The adapter's generic failure code cannot establish whether the cause is permissions, quota or service availability. No retry or automatic model fallback occurred. WorkBuddy has no saved default model and remains disabled; all 18 operation validations are still pending. Redacted Codex controls match exactly. The earlier ar-collection documentation failure was separately repaired; historical failures remain unchanged.

## HY4 PREVIEW 模型检查 2026 10 05 / HY4 PREVIEW model check

用户明确选择 `hy4-preview` 后执行无工具、无 SAP 的模型检查。首次 30 秒探针超时，所属进程树清理完成；随后临时目录发生 Windows 文件占用错误，覆盖真实超时并使接口返回 HTTP 500，检查记录未保存。没有据此推定模型无权限或改变任何业务验收结论。

After the user explicitly selected `hy4-preview`, a tool-free, SAP-free model check was performed. The first 30-second probe timed out with its owned process tree cleaned. A subsequent Windows temporary-directory lock masked the timeout, returned HTTP 500 and prevented saving the check. This did not establish a model-permission failure or change any business acceptance conclusion.

- 修复仅涉及 WorkBuddy 模型管理：独立探针预算改为 90 秒，目录清理错误保存为安全警告，不覆盖真实结果；进程清理不完整仍明确阻断。不改变 Codex 或业务/SAP 时限，不升级 SDK/CLI，不更新独立环境。
- 对修复后的探针进行一次受控复核：`compatible=true`、`actual_model=hy4-preview`、`identity_known=true`、`model_identity_source=assistant_message`。使用独立 SDK `0.3.247`、CLI `2.141.0` 和原环境 `0ff7575be60a4cac86c4d77d88d9a7be13c8d342211b617e3391e43a5b1d83c4`，检查与清理均完成。
- 已保存 WorkBuddy 默认模型 `hy4-preview`，推理策略保持 `null / sdk_default`。没有启用 WorkBuddy 或切换默认 Provider；全部 18 项首期业务操作仍未取得真实验证资格。剩余阻断为 `workbuddy_operation_validation_required`、`runtime_disabled`。
- 模型管理、进程隔离、反馈、登录及 Runtime 路由离线回归 134 passed；契约、投影、来源锚点、Campaign 及文档离线回归 118 passed，合计 252 passed。仅有 1 项既有 Starlette 弃用警告。这是定向结果，不声称本轮完整 Python 套件或远端 CI 已通过。
- 忽略的本地诊断为 `.local-data/workbuddy-baseline/hy4-20261005-probe.json`，包含首次失败摘要、复核结果及默认模型保存结果；原失败探针和历史检查记录不改写。没有 SAP 读取、业务 Agent 发布/启用、Git 提交或推送。

The WorkBuddy-only fix uses a 90-second model-management budget and saves a safe directory-cleanup warning without replacing the probe outcome. Uncertain process cleanup still blocks; Codex and business/SAP budgets, SDK/CLI versions and the environment are unchanged. One controlled recheck passed with the exact `hy4-preview` identity reported by `AssistantMessage.model`. The WorkBuddy default model is now `hy4-preview`, with `null / sdk_default` reasoning, but WorkBuddy remains disabled and Codex remains the default Provider. All 18 real business-operation validations are still pending. Targeted regressions pass 252 tests with one existing warning; this is not a full-suite or remote-CI claim. The ignored local report preserves the initial failure and recheck. No SAP, business publication/activation, commit or push was performed.

文档检查 55 项、0 错误、0 过期受控块。`hy4-20261005-before.json` 与 `hy4-20261005-after.json` 一致：Codex 受保护源码、配置、默认 Provider、平台依赖及 Git 提交未变。确认没有活动作业后仅受控重启 API，使管理修复生效；API 健康，前台进程保持不变。重载后的目录正确显示 `hy4-preview` 兼容且身份已知、WorkBuddy 禁用，以及原历史模型检查状态。

Documentation checking reports 55 documents, no errors or stale generated blocks. Redacted before/after controls match for protected Codex source/configuration, default selection, platform dependencies and Git commit. After confirming no active jobs, only the API was restarted to load the management fix; API health passed and the frontend process was unchanged. The reloaded catalog shows compatible, identity-known `hy4-preview`, disabled WorkBuddy and the unchanged historical model-check statuses.

## 工具桥接及草稿编写分阶段验证 2026 10 05 / Staged tool and authoring validation

本轮继续使用同一独立环境、SDK/CLI 和已检查的 `hy4-preview`，没有变更模型、推理策略或全局业务时限。真实验证只使用本地工具及独立测试数据库/草稿副本，不连接 SAP，不修改正式草稿或已发布 Agent。

This stage keeps the same isolated environment, SDK/CLI, checked `hy4-preview`, reasoning policy and global business deadlines. Live checks use only a local tool and isolated test databases/draft copies, with no SAP access or production draft/package changes.

- **工具桥接通过：** SDK 实际调用一次平台提供的本地回传工具，准确取得验证标记并返回结果；实际模型身份与冻结绑定一致，进程树清理完成。耗时约 17 秒，报告为忽略的 `tool-relay-20261005.json`。它只证明 SDK MCP 传输链，不为 SAP、Skill 或其他业务操作登记资格。
- **隔离草稿解释通过：** 通过真实 `AgentLifecycleService` 提交回合、保存答复和完成操作，修订 1 保持不变、Diff 为空、规则及执行 Digest 不变。耗时约 82 秒，`review_agent_feedback` 只登记 `bounded` 模式，真实作业为 `agent_op_b93eceb3c13645a689de`。
- **可信本地修改未通过：** 首次 SDK 已在隔离副本追加双语 README 文案，但没有在测试实例的 180 秒预算内返回可完成修订保存的结构化终态，结果为 `agent_feedback_timeout`，修订仍为 1。正式配置的 3600 秒预算未改变；不能把此测试时限等同于生产时限。文件编辑不是完整修改资格，未登记 `trusted_local`。原记录保留在 `agent-feedback-20261005.json`。
- **适配改进及一次复核：** WorkBuddy 现在可编辑隔离 `agent-package` 后返回简短终态，由平台读取候选并沿用原有校验/事务，不必重复整个文件。包外源码或受保护身份/验收字段变化被拒绝，取消/超时后绝不导入文件。该改进不修改 Codex 或公开反馈契约，也不被宣称是已证明的超时根因。修复后仅复核修改一次，仍在相同测试预算内超时；记录为 `agent-feedback-workspace-20261005.json`，未调整预期或标为 PASS。
- **终态证据：** 复核事件包含 16 条 `agent_runtime_response_received`，没有 `agent_runtime_turn_completed`，阶段停留在 `generating_revision`，随后平台按 180 秒测试预算结束。现有安全事件没有原生工具名称、参数或终态收尾细节，尚不能确定是具体工具等待、模型持续规划还是其他 SDK 收尾原因。两次修改作业均为 `cancelled / cleanup_complete=true`，不是清理未完成。
- **回归及启用门禁：** 综合定向离线回归 261 passed（1 项既有 Starlette 警告）；另有 1 项新增启用门禁回归通过，合计 262 项不同用例。即使其余操作都通过，仅 `bounded` 解释资格仍不足以启用 WorkBuddy，必须另有真实 `trusted_local` 修改资格。它们不代替全部 Python、声明版本 CI 或剩余真实操作验证。
- **当前状态：** WorkBuddy 默认模型仍为 `hy4-preview`，Runtime 保持禁用，Codex 仍为默认。草稿解释已有资格，可信本地修改及其他 17 项操作仍待验证。未启动 SAP 查询、选样或正式验收，不自动继续重复模型尝试。

The local SDK MCP relay passed with one actual call and complete cleanup. A real isolated draft-lifecycle explanation passed with no revision, Diff or execution/rule change; only the bounded feedback mode was certified. Trusted-local modification edited the copied README but did not deliver a valid terminal result within the test instance's 180-second budget. The production 3600-second budget is unchanged. A WorkBuddy-only candidate-readback improvement and one controlled recheck did not resolve that observed timeout; neither attempt certifies modification. Sixteen assistant-response events but no terminal-completed event are insufficient to attribute the delay to a specific native tool, model planning or SDK finalization. Both jobs were cancelled with confirmed cleanup. Targeted offline checks cover 262 distinct passing cases with one existing warning, including a gate proving that explanation cannot qualify modification or enable WorkBuddy. WorkBuddy stays disabled, Codex stays the default, and further native-terminal diagnosis is required before expensive SAP-dependent checks. No business publication/activation, commit or push occurred.

`operations-20261005-before.json` 与 `operations-20261005-after.json` 完全一致：受保护的 Codex 源码、配置与默认 Provider、平台已安装依赖及 Git 提交未变。文档检查仍为 55 项、0 错误、0 过期受控块。测试副本及失败记录保留本地，不能当作业务验收或发布制品。

The redacted operation-stage controls match exactly for protected Codex source/configuration/default selection, installed platform dependencies and Git commit. Documentation checks report 55 documents with no errors or stale blocks. Local test copies and failure records are retained, not treated as business acceptance or publication artifacts.

确认生产业务任务及 WorkBuddy 待清理作业均为零后，仅受控重启 API 加载候选读取改进；API 健康、设置页 HTTP 200，前台进程未变。`operations-20261005-after-reload.json` 仍与实施前对照一致；默认 Runtime 及 WorkBuddy 禁用状态保持不变。

After confirming no active production tasks or pending WorkBuddy cleanup, only the API was restarted to load candidate readback. API health passes, Settings returns HTTP 200 and the frontend process is unchanged. Post-reload Codex controls still match, with the default Runtime and disabled WorkBuddy state preserved.

## 原生收尾诊断及工作流 v2 2026 10 05 / Native finalization diagnostics and workflow v2

- 增加安全原生工具进度、最终结果接收及 SDK 关闭诊断。新不可变环境为 `4658e4fb0c11ba669e3208b04790dfce4028a5f89750af4d12d8646a7ee3df53`，旧环境和资格记录保留，不原地修改或继承旧检查。SDK `0.3.247`、CLI `2.141.0`、模型 `hy4-preview` 及 SDK 默认推理保持不变；新环境重新确认已有登录，并通过一次无工具模型检查。
- **可信本地草稿修改通过：** 作业 `agent_op_aaeb1992e0244e04b0b2`，约 110 秒完成修订 1 → 2，唯一 Diff 为 `/readme`；执行、规则、身份及验收字段不变，原正式包未修改。6 次原生工具调用取得结果，最终结果接收后 SDK 关闭约 0.1 秒，清理完成。此次没有提高 180 秒测试预算，也没有改变原生执行逻辑，因此不能断言诊断代码修复了之前两次超时的根因。记录为本地忽略的 `agent-feedback-native-diagnostics-20261005.json`。
- **新环境草稿解释通过：** 作业 `agent_op_2f8128ff52c7451dad71`，约 105 秒，修订保持 1、Diff 为空、清理完成。当前 `review_agent_feedback` 登记 `bounded` 和 `trusted_local` 两种真实资格。原环境的解释记录保持原绑定；新报告为 `agent-feedback-explain-new-release-20261005.json`。
- **工作流 v2 首次失败：** SDK 最终结果已返回，所属作业清理完成，但平台导入答复失败，原诊断为 `workflow_assistant_failed`。当时没有保存具体解析错误，不能反推它一定由 Markdown 或某个缺失字段引起；原报告 `workflow-v2-20261005.json` 保留。
- **工作流诊断改进及一次复核：** 复用既有 WorkBuddy JSON 对象解析器，继续拒绝额外字段、非法枚举、多对象及无效规范工作流；保存安全的约束/字段路径，不记录敏感值。复核解释约 61 秒通过，修订保持不变；修改约 131 秒取得最终答复，但没有可解析 JSON 对象，明确为 `workbuddy_structured_output_invalid / json_object`，不是超时。模型在隔离副本使用原生读取/编辑工具，但平台没有导入修改，草稿修订及固定绑定保持原样。两阶段作业均完成清理；不登记 `workflow_authoring.v2`。报告 `workflow-v2-recheck-20261005.json` 保留，不再次自动重跑。
- 读取锁定 SDK 源码及离线 CLI 帮助，确认 `extra_args` 可转发其声明的 `--json-schema` 参数；当前尚未接入该约束。下一步应验证原生结构化输出，而不是放宽规范工作流校验或把副本文件当作成功终态。
- 定向后端回归 **276 passed**，1 项既有 Starlette 弃用警告；前端设置、推理策略及草稿回归 **59 passed**。Codex 受保护源码、配置、默认 Provider、平台依赖及 Git 提交前后对照一致。本轮没有修改前端源码，未声称完整 Python/前端套件、Astro、生产构建或远端 CI 重新通过。
- WorkBuddy 保持禁用，Codex 保持默认。其他 17 项操作仍待完成；没有 SAP 调用、正式业务对象修改、发布、启用、Git 提交或推送。

Safe native diagnostics now distinguish tool execution, terminal receipt and SDK shutdown without retaining commands or content. A new immutable worker environment retains the same SDK/CLI/model and passes fresh authentication/model checks; old releases and records remain intact. A real trusted-local draft revision passed in about 110 seconds with only a README Diff and unchanged execution/rules/validation, followed by a successful 105-second explanation. Both feedback modes are certified in the new environment. This does not prove the root cause of earlier timeouts was fixed: only diagnostics changed and the test budget stayed at 180 seconds.

The first workflow-v2 import failure lacked a precise parser diagnosis and is retained. After shared parsing and safe diagnostics, one controlled recheck passed explanation but rejected revision in about 131 seconds because its terminal reply was not a parseable JSON object. Native file edits were not imported; the saved revision and fixed bindings remain unchanged, with cleanup complete. Workflow v2 is not certified and no repeated retry was made. Pinned CLI help documents native `--json-schema`; integration and real validation are the next step, not weaker workflow checks or importing files without a valid terminal. Targeted regressions pass 276 backend and 59 frontend tests, with unchanged Codex controls; these do not certify full suites, current production builds or remote CI. WorkBuddy remains disabled and the other 17 operations remain pending. No SAP, business publication/activation, commit or push occurred.

本阶段结束时：正式业务任务、工作流 v2 活动回合及 WorkBuddy 所属活动/待清理作业均为零。仅受控重启 API，健康检查通过、前台 PID 未变、设置页 HTTP 200；重载后的 Codex 脱敏对照仍与实施前完全一致。新环境离线模型目录刷新为 21 个候选，目录仍不完整，只有 `hy4-preview` 具备当前环境的模型检查资格。Manifest 校验通过 34 包；文档检查 55 项、0 错误、0 过期受控块。忽略的 `native-stage-final-20261005.json` 保存当前资格与未完成门禁。

At stage completion there are no active production tasks, workflow-v2 rounds or owned/pending-cleanup WorkBuddy jobs. A controlled API-only reload passes health checks; the frontend PID is unchanged and Settings returns HTTP 200. Post-reload Codex controls still match exactly. Offline discovery yields 21 candidates in an incomplete catalog; only `hy4-preview` has a current-environment model check. All 34 manifests validate, and documentation checks cover 55 documents with no errors or stale blocks. The ignored `native-stage-final-20261005.json` records current certification and pending gates.

## 原生 Schema 与工作流 v2 验证 2026 10 05 / Native Schema and workflow v2 validation

- 工作流 v2 将平台冻结的输出 Schema 通过 SDK `extra_args` 传入原生 `--json-schema`，不修改 Codex、SDK/CLI 版本、模型、推理策略或生产业务时限。平台仍校验完整规范工作流、逐轮 Diff、修订与固定绑定，不把格式正确等同于业务正确。
- 首次无 SAP 探针在独立环境 `fe61d55deb9a6f594ab725b92e660f1c1e1e0d863a1a3f232b1b55f1fa7cefe8` 返回正常 SDK 终态，但没有 `structured_output`，被明确拒绝为 `workbuddy_structured_output_missing`。原记录 `native-schema-stage-20261005.json` 保留，不标为 PASS。
- 只读检查锁定 CLI 实现确认：Schema 通过专用本地 `StructuredOutput` 工具捕获；`tools=[]` 隐藏该工具后，CLI 的重试不能获得捕获结果。只在有 Schema 时精确启用这个格式化工具，不授予 SAP、文件、邮件、发布或其他工具权限。缺少原生结构化终态、旧 worker 未确认格式以及取消/超时均不能降级导入文本或副本文件。
- 修复后创建不可变环境 `016c4f7c47c583100594a6d1996744627593274c2b57f0176f9daf5d649e9c69`，保留全部旧环境及绑定。已有登录和一次无工具 HY4 模型检查通过；SDK `0.3.247`、CLI `2.141.0` 未升级。一次受控 Schema 复核实际调用 `StructuredOutput`，返回 `ResultMessage.structured_output`，清理完成；记录 `native-schema-formatter-20261005.json`。格式化探针本身不登记工作流操作资格。
- **工作流 v2 通过真实生命周期验证：** 解释回合 `workbuddy-workflow-v2-validation-19ff20feb5f4464bb5c98bacf036741e` 约 94 秒，修订保持 1、Diff 为空；修改回合 `workbuddy-workflow-v2-validation-8a8b556b8ffd447a956cea7160c77509` 约 102 秒，修订 1 → 2，只修改 `/title/en` 和 `/title/zh`。两回合均为原生 Schema 输出，固定 Agent ID、版本、Digest 和其他字段不变，每个相同请求只派发一次，清理完成。当前环境登记 `workflow_authoring.v2 / trusted_local`；记录 `workflow-v2-native-schema-20261005.json`，历史失败不重新分类。
- 新环境草稿解释已重新通过：作业 `agent_op_9584592601a14d7d9c13`，约 110 秒，修订 1 不变、Diff 为空；记录 `agent-feedback-schema-explain-20261005.json`。旧环境资格没有继承。
- **新环境草稿修改未通过：** 一次独立复核在 180 秒测试预算内未取得有效终态，返回 `agent_feedback_timeout`；记录 `agent-feedback-schema-revise-20261005.json`。SDK 有 8 次取得结果的原生工具调用，但没有最终结果或完成事件，平台没有导入已编辑副本，修订保持 1，正式包不变。生产 3600 秒时限未调整；该结果不等同于生产时限超时，也不能仅凭工具进度断定底层原因。只保留当前环境 `bounded` 解释资格，不登记 `trusted_local`、不继承旧环境成功记录、不重复该用例。
- 定向后端回归 **281 passed**，1 项既有 Starlette 弃用警告；前端设置、推理策略及草稿回归 **59 passed**。Astro 检查 49 文件，0 错误、0 警告、0 提示；34 个 Manifest 校验通过。隔离生产构建生成 **82 页面**，输出到忽略的 `site-schema-stage-20261005`，未覆盖在用构建。这些结果不代替完整 Python/前端套件或远端 CI。
- Codex 脱敏前后对照一致；没有 SAP 访问、正式 Agent/工作流修改、业务发布/启用、Git 提交或推送。WorkBuddy 保持禁用，Codex 保持默认。

Workflow v2 now passes a frozen platform Schema to native CLI output capture and retains full platform workflow/binding validation. The first SAP-free probe failed because `tools=[]` hid the pinned CLI's local `StructuredOutput` tool; its record is preserved. Only that formatter is enabled when a Schema is supplied, without granting data access or other permissions. A new immutable release retains the pinned SDK/CLI/model, passes fresh login/model checks and one controlled native-Schema probe, with complete cleanup. Missing native termination or an old worker cannot silently fall back to text or workspace-file import.

A real isolated workflow lifecycle now passes explanation and title-only revision in about 94 and 102 seconds. Both use native Schema output, preserve all fixed Agent bindings and other fields, and dispatch each repeated request once. Only the title changes; the saved revision advances once with its exact per-round Diff. Workflow v2 is certified for trusted-local operation in this release; historical failures remain unchanged. Draft explanation was separately revalidated, but one trusted-local draft modification timed out within its 180-second test budget after eight completed native tool calls and no terminal result. Edited files were not imported; the saved revision and production package remain unchanged. The production 3600-second deadline is unchanged, and progress alone does not establish the underlying cause. Only bounded feedback is certified; the failed case was not repeated and old-environment qualification was not inherited. Targeted checks pass 281 backend and 59 frontend tests, plus zero Astro diagnostics and a separate 82-page production build; they do not certify full suites or remote CI. Codex controls match and WorkBuddy stays disabled. No SAP, production business-object change, publication/activation, commit or push occurred.

本阶段最终核对：`schema-stage-final-20261005.json` 将完整资格与部分资格分别保存，完整资格仅有 `workflow_authoring.v2 / trusted_local`，草稿反馈仅有 `bounded`；17 项完整操作验证待完成。WorkBuddy 所属活动及待清理作业为零，生产任务为零；API 健康、设置页 HTTP 200。仅更新独立环境和平台代码文件，本轮未重启 API 或前台。21 个模型候选仍为不完整目录，只有 HY4 的当前环境检查可为新任务背书；历史检查不改写。`schema-stage-20261005-before.json` 与 `schema-stage-20261005-final.json` 完全一致，Codex 受保护源码/配置、默认 Provider、平台依赖及 Git 提交未变。

The final report separates complete operation certification from partial modes: only workflow v2 is complete, while feedback has bounded qualification only; 17 complete operation validations remain. Owned running/pending-cleanup jobs and active production tasks are zero. API health and Settings HTTP 200 pass, without restarting the API or frontend. The incomplete model catalog has 21 candidates; only HY4's current-environment check qualifies new tasks, and historical checks remain unchanged. Final redacted controls match for protected Codex source/configuration, default Provider, platform dependencies and Git commit.

## 草稿原生 Schema、收尾隔离及上下文投影 2026 10 05 / Native draft Schema, finalization isolation and context projection

- 草稿反馈新增冻结原生 Schema，与工作流 v2 使用相同的锁定 CLI 格式捕获。无效终态或旧 worker 未确认原生格式时拒绝导入；不自动进行第二轮编辑来修复 JSON。其他 WorkBuddy 操作的原有修复路径、Codex、模型及生产业务预算不变。
- worker 明确要求完成请求及必要的针对性检查后使用本地 `StructuredOutput` 收尾。创建不可变环境 `add87d43e14b9fddb5b59b64e94e5b31fda7659bf4ccd0904d9f37d7411b1a07`，保留旧环境；SDK `0.3.247`、CLI `2.141.0` 和 HY4 不变。已有登录、一次模型检查及一次无 SAP Schema 探针通过，记录 `feedback-native-schema-stage-20261005.json`。
- **草稿解释通过：** 作业 `agent_op_3b24671c16e241a2b435`，约 55 秒，修订 1 不变、Diff 为空，原生终态及清理完成；记录 `agent-feedback-native-schema-explain-20261005.json`。
- **草稿修改仍未通过：** 作业 `agent_op_85fa1bcdc45546c39983` 在 180 秒测试预算内完成 13 次原生工具调用，但没有结构化收尾或最终结果，返回 `agent_feedback_timeout`。隔离文件回读确认精确 README 追加、Manifest 和规则均未改变；仍不导入文件，保存修订保持 1。所属进程已取消且清理确认。记录 `agent-feedback-native-schema-revise-20261005.json` 和只读回读 `agent-feedback-native-schema-readback-20261005.json`；不改原结论、不重跑该用例。生产一小时时限未变化，不能将验证实例超时等同于生产超时。
- **工作流新环境复核：** 解释约 80 秒通过，原生 Schema、修订及固定绑定检查通过；标题修改约 181 秒超时，修订保持 1，不导入候选，不登记新环境的工作流 v2 资格。记录 `workflow-v2-feedback-native-schema-20261005.json`；旧环境的成功资格保持原绑定。
- **最小原生编辑/收尾探针通过：** 在新测试目录执行 Read → Edit → Read → StructuredOutput，精确修改 ASCII 测试文本并取得原生终态，SDK 关闭及清理正常；记录 `native-edit-finalization-20261005.json`。这确认原生编辑与格式捕获可以组合，不证明完整草稿任务已通过，也不定位其所有时间消耗原因；不登记业务操作资格。
- 可信本地编写同时提供约 8 万字符的完整包内联内容和可读隔离包，存在可核对的重复。移除重复正文，改为稳定文件引用；保留原始大小门禁、完整隔离文件、全部既有本地权限、历史决定及澄清。该投影优化通过离线回归，完整真实修改尚未复核，不作为通过声明。本阶段不再次派发失败用例，也不通过提高预算取得资格。
- 最终独立管理报告 `feedback-native-stage-final-20261005.json`：没有完整操作资格，仅草稿反馈 `bounded` 部分资格，18 项完整操作待验证；21 个候选模型，目录不完整，WorkBuddy 禁用。所属活动及待清理作业为零；不继承旧资格、不自动启用。

Draft feedback now uses frozen native Schema capture and refuses missing or invalid native termination without replaying an editing round to repair JSON. A new immutable worker release retains the pinned SDK/CLI/HY4 and passes fresh login/model checks and a SAP-free formatting probe. Draft explanation passes in about 55 seconds without revision or Diff. One full revision still exceeds its 180-second test budget after thirteen completed native calls and no terminal result. Offline readback confirms the exact requested README append and unchanged Manifest/rules, but files are not imported: the saved revision stays unchanged and cleanup is confirmed. This is not a production one-hour timeout claim.

The new-environment workflow explanation passes in about 80 seconds; title revision times out without importing changes or qualifying workflow v2. Older successful qualification is not inherited. A separate minimal Read/Edit/Read/StructuredOutput probe passes exact-file, native-terminal and cleanup checks; it confirms coexistence, not full business qualification or the complete timeout cause. Trusted-local prompts now reference the complete workspace package instead of duplicating roughly 80,000 characters inline, with original bounds, history and permissions preserved. Offline checks cover the projection; no additional full live revision is claimed or dispatched in this stage. Current certification is bounded feedback only, with eighteen complete operations pending and WorkBuddy disabled.

最终回归 **286 passed**，1 项既有 Starlette 弃用警告；前端定向 **59 passed**，Astro 49 文件零诊断，34 个 Manifest 通过，隔离生产构建生成 82 页，文档检查 55 项零错误。`feedback-schema-20261005-before.json` 与 `feedback-native-20261005-final.json` 完全一致，受保护 Codex 源码/配置、默认 Provider、平台依赖及 Git 提交未变。API 健康、设置页 HTTP 200，生产活动任务为零。没有重启 API/前台、访问 SAP、修改正式业务对象、发布/启用、提交或推送；这些定向检查不代替完整套件或远端 CI。

Final targeted regressions pass 286 backend and 59 frontend cases, with one existing Starlette warning. Astro reports no diagnostics across 49 files; all 34 manifests validate, an isolated build generates 82 pages and documentation checks cover 55 documents without errors. Redacted before/final Codex controls match for protected source/configuration, default Provider, platform dependencies and Git commit. API health and Settings HTTP 200 pass, with no active production tasks. No API/frontend restart, SAP access, production business-object modification, publication/activation, commit or push occurred. Targeted checks do not certify full suites or remote CI.

## 一小时预算与真实重跑 2026 10 05 / One-hour budget and live rerun

- 按用户要求将新工作流助手前台请求及 `WorkflowFeedbackRequest` API 默认值从 600 秒改为 3600 秒，仍允许显式较短预算，最大值不变。排队不计入执行预算，历史回合、待重传的原始请求及请求指纹不改写。该业务预算同时适用于 Codex 和 WorkBuddy，不修改 SDK、模型、推理或权限选项。
- **工作流 v2 通过：** `workflow-v2-3600-20261005.json` 保存同一环境、同一 HY4 模型下的一次解释和一次仅标题修改，执行预算均为 3600 秒。解释约 83.21 秒，修订 1 不变；修改约 154.09 秒，修订 1 → 2，Diff 仅 `/title/en`、`/title/zh`。两轮原生 Schema、完整规范工作流、固定绑定、幂等派发和所属进程清理均通过，登记 `workflow_authoring.v2 / trusted_local`。
- **草稿修改仍未通过：** `agent-feedback-3600-20261005.json` 保存作业 `agent_op_9744c0e3438445579573`，执行预算 3600 秒，约 164.59 秒取得原生结果并完成 SDK 关闭，随后返回 `runtime_agent_feedback_failed`。独立只读回放 `agent-feedback-3600-readback-20261005.json` 重现 `UnicodeDecodeError`：本地测试产生 `files/tests/__pycache__/test_manifest_contract.cpython-314.pyc`，候选回读将其按 UTF-8 文本处理。README 精确追加、Manifest 和规则不变，保存修订仍为 1；不导入文件、不登记修改资格。这是候选回读错误，不是时间不足；本轮不扩大修复范围或重复派发该用例。
- `3600-stage-final-20261005.json` 核对完整资格仅工作流 v2，草稿反馈仍为 `bounded` 部分资格，17 项完整操作待验证。WorkBuddy 禁用，21 个模型候选仍为不完整目录，所属活动及待清理作业为零。
- 本阶段没有切换模型或 SDK，也未放宽候选、Diff 或执行绑定校验。此前 180 秒结果保留原结论。此次修改还使用上一阶段已完成的上下文投影优化，两轮工作流实际耗时均少于 180 秒，因此不将成功单独归因于延长预算。

New workflow UI submissions and the API default now use 3600 seconds as explicitly requested, while explicit shorter limits, historical rounds, pending requests and fingerprints remain unchanged. Queue time stays excluded. This applies to both workflow Providers, not SDK/model/reasoning/permission settings.

One current-environment HY4 workflow explanation and one title-only revision pass with the one-hour budget in 83.21 and 154.09 seconds. Native Schema output, canonical workflow validation, fixed bindings, idempotency, exact Diff and cleanup pass; revision advances once only for the title change. Workflow v2 is qualified for trusted-local use.

One draft revision obtains native termination and closes the SDK in 164.59 seconds, then fails candidate readback with `UnicodeDecodeError`: a locally generated test `.pyc` cache is interpreted as UTF-8 text. Read-only replay confirms the exact README append and unchanged Manifest/rules, but saved revision stays unchanged and no modification qualification is granted. This is not a timeout. The failure is preserved without another dispatch or scope expansion. Seventeen complete operation validations remain pending and WorkBuddy stays disabled. Historical short-budget results remain intact; completion under 180 seconds is not attributed solely to the larger budget, especially with the previously implemented context projection in place.

最终定向回归 **287 passed**（1 项既有 Starlette 弃用警告）、前端 **63 passed**、Astro 49 文件零诊断、34 个 Manifest 校验通过、55 项文档检查零错误，生产构建生成 82 页。核对生产及独立 Runtime 无活动任务后，使用现有启动器先构建、再受控重启 API/前台；启动报告 `.local-data/startup/20261005T121945/result.json` 为 completed。运行中的 OpenAPI 默认值及实际返回的工作流 JavaScript 均确认为 3600 秒，中英文设置/工作流页均 HTTP 200，构建指纹匹配；记录 `3600-live-settings-20261005.json`。启动器清理 3 个旧的可重建前台缓存，未删除源码或历史业务记录。前后脱敏 Codex 对照完全一致；只调整用户要求的工作流业务预算，不改 Codex SDK、模型、权限或依赖。未访问 SAP、发布/启用业务对象、启用 WorkBuddy、提交或推送。这些定向检查不代替全量套件及远端 CI。

Final targeted checks pass 287 backend and 63 frontend tests, with one existing Starlette warning, no Astro diagnostics across 49 files, all 34 manifests, 55 documentation checks and an 82-page production build. After confirming production and owned Runtime jobs were idle, the existing launcher built first and performed a controlled API/frontend restart. Live OpenAPI and the served workflow JavaScript confirm 3600 seconds; bilingual Settings/workflow pages return HTTP 200 and the build fingerprint matches. Three rebuildable old frontend caches were pruned; source and historical business records remain. Redacted Codex controls match before and after, apart from the explicitly requested platform workflow budget change not included in SDK controls. No SAP, business publication/activation, WorkBuddy enablement, commit or push occurred. These targeted checks do not certify full suites or remote CI.

## 测试缓存回读修复 2026 10 05 / Test-cache candidate readback repair

- `AuthoringWorkspace.read_package` 增加默认关闭的内部选项，仅 WorkBuddy 原生候选回读显式启用；Codex 调用及默认回读行为不变。只排除 `__pycache__`、`.pytest_cache` 目录及 `.pyc`/`.pyo`，不排除真实源码、文档或任意二进制。不删除缓存文件。排除前仍检查缓存中的符号链接、目录联接、硬链接及大小；其他无法解码的文件返回安全错误码 `agent_harness_binary_file_unsupported`。
- 离线回放原失败候选通过，读取到精确 README 追加且原文件、Manifest、规则及执行不变；原隔离修订及失败记录不改写、不登记资格。记录 `test-cache-offline-replay-20261005.json`。新增真实字节码编译、pytest 缓存、相似但合法文件名、任意二进制、链接及大小回归；77 项定向测试通过。
- 新的唯一真实验证记录 `agent-feedback-test-cache-fix-3600-20261005.json`：作业 `agent_op_269e5373117745c7b011`，预算 3600 秒，约 198.68 秒取得原生终态并清理完成。平台回合为 completed，隔离修订 1 → 2，Diff 仅 `/readme`，执行、规则、验收及原文件均不变，旧缓存解码错误不再出现。
- **完整验证仍未通过：** 逐字断言发现模型只追加 1 个而非明确要求的 2 个前导换行。原正文保留，双语文字相同；工作区与保存后的 README 一致，缺少换行已存在于模型候选，平台未丢失内容。只读诊断 `test-cache-live-diagnosis-20261005.json`、`test-cache-workspace-readback-20261005.json` 保存具体检查；不放宽断言、不手工修正候选、不重复调用模型、不登记 trusted-local 修改资格。该次候选没有残留 `.pyc`；缓存修复的直接证据来自原冻结失败的离线回放和生成真实字节码的回归，不将无缓存的真实运行声称为缓存覆盖。
- `test-cache-stage-final-20261005.json` 确认工作流 v2 资格保留、草稿反馈仍仅 bounded、17 项完整操作待验证、WorkBuddy 禁用，所属活动/待清理作业为零。锁定 SDK、CLI、环境、HY4 和默认 Codex 均不变，未访问 SAP 或修改正式业务对象；历史结论保留。

WorkBuddy now opts into narrowly filtered candidate readback; Codex's existing default path is unchanged. Python bytecode and pytest caches are excluded from proposals without deleting files. Links, hard links and size bounds are checked even inside excluded caches; arbitrary non-cache binary content remains rejected with a safe code. The original frozen failure replays successfully with the exact README append and unchanged authored files/execution, without changing its historical failure or qualifying it. Regression tests include genuine compiled bytecode and cache-boundary failures.

One live isolated revision with the unchanged 3600-second budget terminates natively in 198.68 seconds and persists one README-only revision. All execution, rule, acceptance and authored-file checks pass, with complete process cleanup. Full exact validation still fails because the model appends one rather than two requested leading newlines. Workspace and persisted README match, locating this separate precision issue in the candidate rather than platform persistence. No assertion relaxation, manual correction, repeat dispatch or modification qualification occurs. This live candidate has no residual bytecode; direct cache coverage comes from the frozen-failure replay and regression tests, not an unsupported live-cache claim. Workflow qualification remains, feedback is bounded-only and WorkBuddy stays disabled with seventeen complete operations pending. Historical results are unchanged.

本轮扩大后端回归 **347 passed**，另有 Codex Planner/执行权限/编写工具 **23 passed**，共 370 项，1 项既有 Starlette 弃用警告；前端 63 项通过，Astro 49 文件零诊断，34 个 Manifest、55 项文档检查通过，隔离生产构建生成 82 页。`test-cache-20261005-before.json` 与 final 对照完全一致。核对生产与所属 Runtime 作业空闲后，仅受控重启 API 使修复生效，前台 PID 不变；中英文设置与工作流页均 HTTP 200，工作流默认仍为 3600 秒，Codex 仍为默认，WorkBuddy 仍禁用。记录 `api-test-cache-fix-20261005.json`。没有 SDK/模型更新、正式业务对象修改、SAP 调用、发布/启用、提交或推送；本轮不修改或重判历史验收。

Expanded checks pass 347 backend cases plus 23 separate Codex planner/permission/authoring-tool cases (370 total), with one existing Starlette warning, and 63 frontend tests. Astro reports zero diagnostics across 49 files; all 34 manifests and 55 documentation checks pass, with an isolated 82-page production build. Before/final Codex controls match. A controlled API-only restart after idle checks activates the repair; the frontend PID is unchanged and bilingual Settings/workflow pages return HTTP 200. The workflow default stays at 3600 seconds, Codex remains default and WorkBuddy stays disabled. No SDK/model update, production business-object change, SAP call, publication/activation, commit, push or historical reclassification occurred.

## 剩余操作逐项验证 2026 10 05 / Remaining operation validation

同一锁定环境 `add87d43e14b9fddb5b59b64e94e5b31fda7659bf4ccd0904d9f37d7411b1a07`、SDK `0.3.247`、CLI `2.141.0` 和 `hy4-preview` 下，逐项调用真实 SDK。平台业务记录使用隔离数据库；没有启用 WorkBuddy 或更改默认 Codex。成功仅在原生作业、模型身份、清理和完整平台契约都通过后登记，不以返回 JSON 或执行完成代替资格。

| 操作 | 本轮结果与边界 |
| --- | --- |
| `plan`、`summarize` | 通过。前者校验精确 GET 计划、字段和输入范围；后者使用明确的离线证据缺口样例，不冒充 SAP 查询。 |
| `author_draft` | 通过。隔离 Agent 创建、Manifest/双语/只读校验、保存和相同请求幂等均通过；没有发布。首个测试入口的快照默认字段比较错误在 SDK 派发前失败，修正测试入口后首次实际派发约 63.81 秒通过。 |
| `review_agent_feedback` | 可信本地修改通过，约 179.35 秒；精确追加含两个前导 LF，修订只增加一次，Diff 仅 `/readme`，执行、规则及验收定义不变。与此前 bounded 解释资格合并；先前少一个换行的失败保留。 |
| `review_workflow`、`repair_workflow` | 通过。使用平台编译的隔离工作流样例；完整契约、已声明端口和固定 Agent 绑定通过，修复只改变连接。 |
| `review_free_query_feedback` | 通过。重新取证、仅调整展示、澄清和新会话四种分流均校验，不执行 SAP。 |
| `revise_free_query_presentation` | 通过。真实运行协调器保存隔离结果，原始行值、证据引用和完整性不变，原运行不改写，SAP 调用为零。 |
| `ground_plan` | 未通过。SDK 正常完成并清理，但未满足冻结计划的完整断言；该轮未保留原始返回计划，不能进一步断言具体字段差异。未登记资格或自动重跑。 |
| `compose_workflow`、`review_workflow_feedback` | 未通过。两项旧接口仍使用 120 秒适配器预算，所属作业均 `timed_out` 且清理完成；随后 `TemporaryDirectory` 的 Windows 文件占用错误覆盖超时，平台保存通用工作流失败。新 `workflow_authoring.v2` 的 3600 秒资格不受影响；本轮未改变预算。 |
| `analyze_role_matching` | 未通过。原生作业完成，但平台拒绝 `role_matching_evidence_required`：结论缺少可核对的文档引用。岗位匹配反馈依赖有效前序结果，本轮未派发。 |
| `free_query` | 未通过。经平台 Broker 的实际查询使用了未被现有执行器消费的 `filter`、`keys`、`select`、`skip` 成员；未形成有效目标 PO 过滤，出现重复读取及较大订单集合。验证者停止所属隔离主进程，Job Object 清理子进程；核对 worker 已消失后只将本轮记录终结为 `verification_query_scope_violated`。没有登记资格，不将其当成业务验收失败。 |
| `sample_discovery` | 未通过。约 430.07 秒完成原生回合并清理，但返回内容不能被现有入口解析为所需 JSON（`JSONDecodeError`）；目录/Schema 等调用成功，业务数据读取为零，不能形成真实样本。未保留该轮原始终态文本，不能断言是代码围栏、自然语言前言还是其它格式问题。 |
| `acceptance_baseline`、`acceptance_free_query` | 暂未派发。自由查询的计划口径和范围检查未通过，先修复共享阻断再进行一次隔离三级案例；没有访问或改绑历史 Campaign。 |

真实 SAP 验证只经现有平台 Broker GET 及已批准的只读 Skill，没有 shell 直连、写操作或向模型传递连接凭据。首次隔离查询遗漏插件启动检查，全部工具被 `selected_provider_unavailable` 拒绝，SAP 数据读取为零；首次选样使用不存在的 `delivery_date` 字段，在 SDK 派发前拒绝。随后只修正验证脚本为标准插件启动和当前 `schedule_line_delivery_date`，失败记录保留，生产权限/启用门禁不变。普通查询实际验证与随后单独的选样使用不同隔离记录，不反复重跑已确认的模型或业务失败。

本轮新增 8 项完整资格，加上既有工作流 v2，当前为 9/18；其余资格仍有门禁。Codex 受保护源码、配置、依赖、Python 和 Git 提交的前后对照一致。定向回归 165 passed，另有既有 Starlette 弃用警告和默认 pytest 缓存写权限警告。生产 API 健康、设置页 HTTP 200；未重启服务、修改正式业务对象、发布/启用、提交或推送。没有平台实现代码改动，仅记录验证结果与忽略的本地验证脚本；本轮定向回归不代表全量套件或远端 CI。

Eight additional operations are fully qualified against the same pinned environment/model. Including the previously qualified workflow v2, nine of eighteen are currently qualified. Draft modification now passes exact byte-level append, one revision and README-only Diff checks; the earlier precision failure is preserved. Other passes cover frozen planning, honest summarization, isolated Agent creation/idempotency, workflow review/connection repair, all four free-query feedback decisions and persisted presentation-only revision without SAP.

Grounding fails the full frozen-plan assertion, without a retained raw plan sufficient to identify a specific field. Legacy workflow composition/feedback time out under their unchanged 120-second adapter budget; Windows temporary-directory cleanup masks those real timeouts. Role analysis completes natively but lacks required document references, blocking its dependent feedback validation. Actual Broker queries reveal unsupported plan members and ineffective target-order filtering, leading to repeated/broader reads. Only that owned validation is stopped and its confirmed-cleaned records finalized; no capability is granted. The separate sample check completes natively in 430.07 seconds but its terminal text fails JSON parsing, with catalog/Schema calls but no business-data reads. Without retained terminal text, the specific formatting defect remains unknown. Acceptance model stages are deferred until the shared plan/scope blocker is repaired. Initial fixture startup/field errors are retained and corrected only in isolated verification, not by weakening production gates.

SAP remains read-only through existing platform services, with no direct shell access or credentials delivered to the model. Protected Codex controls match; 165 targeted tests pass with two non-failing warnings. Production health/Settings checks pass and Codex stays default. No service restart, production object changes, publication/activation, SDK update, commit or push occurs. These checks do not certify a full suite, remote CI or historical acceptance.

忽略的本地记录保留在 `.local-data/workbuddy-baseline/`：`remaining-bounded-4659e9d1d05b4253a1774a314bc4666c`（计划、校准、摘要及首次创建入口）、`remaining-bounded-45d3fd2195dd406ba3de674064f529fa`（实际创建）、`remaining-role-bc4d8c5e52b74a5dafba08a38e809620`、`remaining-workflow-2706801022e14826964dfa16900b4908`、`remaining-feedback-deca8575c7cf4e1bab47e9352ceb9abc`、`agent-feedback-exact-suffix-3600-20261005.json`、`remaining-sap-c83aa56116b3435080ab4e0053c406bd`（首次入口失败）、`remaining-sap-683a1b3f791e43c99900fae65981c9b0`（实际范围失败）、`remaining-sap-8bf0058eb34749f5ac084240132b8958`（选样终态）及 `remaining-failures-20261005.json`。它们不是发布制品，也不替代正式 Agent 验收。

Ignored local reports under `.local-data/workbuddy-baseline/` retain every pass, fixture failure and actual failure separately. They are verification records, not release artifacts or formal business-Agent acceptance.

## 剩余项修复与一次验证 2026 10 06 / Remaining-operation repairs and one validation pass

本轮仅修改 WorkBuddy 适配、提示/契约、离线夹具与其 CI 测试入口；保留既有未提交修改。没有改变锁定 worker、SDK `0.3.247`、CLI `2.141.0`、`hy4-preview` 或环境 `add87d43e14b9fddb5b59b64e94e5b31fda7659bf4ccd0904d9f37d7411b1a07`。Codex 受保护源码、配置、默认 Provider、依赖、解释器及 Git 提交的前后脱敏对照一致。

- 查询契约拒绝未消费成员；实际读取前复查规范化计划与明确范围，限制上游关联和派生键配对，Skill 参数不能扩大范围。grounding 保留来源、过滤、字段和上限。
- 剩余入口接入顶层原生输出 Schema，继续进行 JSON/枚举/业务/证据检查；岗位提供文档/片段引用注册表。输出错误保存操作、阶段、形式、摘要、长度及安全路径；未收到终态的长度/摘要保持未知。
- 旧工作流创建与反馈统一 3600 秒，排队在容量预留阶段，Codex 旧等待不变。文件清理异常不覆盖真实错误，进程清理仍须确认。
- 真实失败暴露了旧工作流内层提案缺少完整编译契约，以及查询报告与原生终态的区别；已补齐提案/反馈契约、原生收尾说明和安全诊断，新增离线回放通过。没有自动重跑这些失败项，不能以离线修复登记真实资格。

| 本次实际派发操作 | 结果 |
| --- | --- |
| `ground_plan` | **通过**，约 31.77 秒；原生终态、模型身份、冻结 PO 过滤/字段/上限及清理全部通过，新增当前环境资格。 |
| `compose_workflow` | 未通过，约 101.42 秒；原生作业完成，但提案缺少阶段 `confidence`，编译器保留能力缺口，没有强行选择 Agent。随后补充完整提案契约和字段诊断，仅离线验证。 |
| `review_workflow_feedback` | 未通过，约 95.82 秒；返回补丁形态提案与对象形态的候选预期，不符合完整提案及数组契约。保留失败，没有宽松转换或任意补值；随后离线补齐说明及校验。 |
| `analyze_role_matching` | 未通过，约 122.78 秒；原有 120 秒适配器预算到期，未取得终态，不能推断是否还有引用问题。未擅自增加岗位预算。 |
| `free_query` | 未通过，约 196.83 秒；一项规范、`top=1`、目标 PO 过滤的 `A_PurchaseOrder` GET 完成。第一次报告参数混淆外层运行对象与内层报告，保存具体路径；后续报告校验通过，但 SDK 缺少 `structured_output`，仍明确拒绝。 |
| 岗位反馈、选样、验收基线及验收自由查询 | **未派发**；各自前置验证未通过。没有新 Campaign，没有重复派发追求 PASS。 |

所有本轮所属作业均确认 `cleanup_complete=true`，没有活动/待清理 worker。当前仍是 **10/18** 项完整资格，WorkBuddy 保持禁用，Codex 保持默认；此次修复不等于可开放选择。此前失败、历史业务验收与正式 Agent 均未改写。下一轮需验证补齐后的旧工作流和查询收尾；岗位超时须另行核对预算与性能，再进入依赖项。原生 Schema 只证明格式，不能证明业务结论正确。

离线结果：定向回归 **171 passed**；追加真实编译器兼容检查后，专项 **33 passed**，覆盖范围/契约/预算/诊断及由平台固定版本的编译结果；全量初跑 **1444 passed、9 failed、1 skipped**，9 项均为当前沙箱账户 DPAPI 失败，在正常登录用户环境补验 **9 passed**，不修改加密逻辑或跳过断言。这是初跑及定向补验结果，不冒充单次全量零失败记录。另有既有 Starlette 弃用警告。前端 **114 tests passed**，Astro **0 errors/warnings/hints**，Manifest **34 包**，生产构建 **82 页**，均使用隔离构建目录，不覆盖在用前台制品；文档检查 **55 项、0 过期受控块、0 错误**。本地结果不代表远端 CI。

安全验证记录位于忽略的 `.local-data/workbuddy-baseline/`：`repairs-ground-3f97c378c50d41579e2d58bb18e032bf`、`repairs-workflow-57c289bfe687470abbfeb362a7428046`、`repairs-role-47c7c040f28647659e2d52267a825f49`、`repairs-sap-e2512bab8563449bb65a350334fa53b0`、`repairs-dpapi-20261006.xml` 及 `repairs-20261006-final-user.json`。最后一份正常用户投影确认资格、清理、GET 范围和 Codex 对照；沙箱投影无法核验该用户锁定安装环境，不代表安装丢失。

This pass changes only WorkBuddy adapters/contracts, fixtures and its offline CI entry while preserving existing uncommitted work. The pinned worker, SDK/CLI/model/environment remain unchanged, and redacted Codex source/configuration/default/dependency/interpreter/Git controls match. Query admission rejects ignored members and rechecks normalized scope, bounded joins, paired derived keys and Skill inputs. Remaining operations use native output Schema plus strict backend checks; legacy workflow composition/feedback use 3600 seconds excluding queue time, without changing Codex timing. Directory locks cannot mask execution failures.

Grounding passes in 31.77 seconds and is newly qualified. Legacy composition and feedback finish natively but fail their inner compiler/feedback contracts in 101.42 and 95.82 seconds. Role analysis times out under its unchanged 120-second adapter budget in 122.78 seconds, leaving citation correctness unknown. A single target-filtered, top-one PO-header GET executes; report validation is corrected during the same run, but missing SDK structured termination rejects the run at 196.83 seconds. No dependent role feedback, sample discovery or acceptance stage is dispatched. Complete proposal/feedback contracts, final-capture instructions and safe diagnostics are subsequently repaired offline, without repeating failed live operations or granting qualification from fixtures.

All owned jobs confirm cleanup, with no active/pending-cleanup worker. Qualification is **10/18**, WorkBuddy stays disabled and Codex remains default. Targeted checks pass **171** tests; after adding a real-compiler compatibility case, the focused contract/scope suite passes **33** tests. The initial full suite has **1444 passes, nine sandbox-user DPAPI failures and one skip**; all nine pass separately as the logged-in user without crypto changes or skipped assertions. This is not claimed as a single zero-failure full-suite run. Isolated frontend checks pass **114** tests, zero Astro diagnostics, **34** manifests and an **82**-page build; documentation checks pass **55** documents. Local results do not certify remote CI. No business publication/activation, commit or push occurs; historical results remain immutable.

生产任务为空时完成一次受控 API 重载，健康检查通过、前台 PID 不变；重载后的 Runtime 目录仍默认 Codex，WorkBuddy 已认证但保持禁用及验证未完成阻断。重载前后 Codex 脱敏对照完全一致。

With production tasks idle, a controlled API-only reload passes health checks and preserves the frontend PID. The reloaded runtime catalog still defaults to Codex; WorkBuddy is authenticated but disabled and blocked on incomplete operation qualification. Redacted Codex controls match before and after reload.

## 共享编排离线重构 2026 10 06 / Shared-orchestration offline refactor

本轮保留实施开始时已有的未提交实现，将全部 **18 项操作** 接入 SDK 无关的共享业务入口。12 项 Planner 操作共用提示、Schema、阶段、分页和业务检查；草稿反馈共用提示、解码及候选修复检查；工作流 v2、选样及三种 Harness 操作共用各自生命周期。Codex 与 WorkBuddy 驱动只转换原生调用、事件、终态及所属进程管理，独立安装、解释器、认证和锁定 worker 保持分离。固定 Agent、已发布工作流及历史验收不变。

- `codex-orchestration-before.json` 与 `codex-native-orchestration-before.json` 保存迁移前源码的代表性离线轨迹，合计覆盖 18 项操作。模拟 SDK 响应核对提示、完整 Schema、阶段顺序、权限参数和结果；测试不重新生成这些对照文件，也没有额外模型或 SAP 调用。
- 公共 Broker 在实际读取入口检查规范化计划及 Skill 参数；等价升序写法先规范化再检查。不存在或截断的 Schema 不能成为删除已确认字段的依据。岗位使用共用分页流程及 300 秒阶段预算；WorkBuddy 旧工作流创建/反馈仍为 3600 秒，Codex 原有预算保留。
- WorkBuddy 原生会话 ID 仅保存为元数据；新平台句柄独立绑定 Provider，历史 ID 可作兼容输入但不启用未经验证的原生续接。原生 ID、工具权限及不加载个人配置的断言保留；相同原生 ID 不能合并不同会话。未收到终态的输出长度/摘要为未知，真实空文本才记录零长度。
- 资格额外绑定共享编排版本与操作摘要。摘要覆盖共享模块、驱动及仍承担原生启动/格式转换的兼容入口；改变这些入口也使旧资格失效。历史验证记录不改写，新状态投影提示需要重新验证，不能用过去的 10/18 资格为本轮代码背书。

### 检查结果 / Check results

| 检查 | 本轮离线结果 |
| --- | --- |
| 完整 Python 回归 | **1410 passed、1 skipped、1 warning**，退出码 0；847.63 秒。跳过原因为本机无法创建测试符号链接，警告为既有 Starlette 弃用提示。使用正常 Windows 登录账户，DPAPI 断言未修改或跳过。 |
| 最后资格摘要覆盖补充后的定向回归 | **164 passed**；包含六个原生兼容入口的摘要变更回归及共享编排、驱动、隔离检查。此最后修改仅扩展资格摘要覆盖名单；完整回归在此元数据补充之前完成，二者不是相加的全量计数。 |
| 禁止 WorkBuddy SDK 导入的模拟检查 | **78 passed**。平台既有安装未卸载；用导入拒绝模拟 SDK 不可用，不冒充全新安装或远端 CI。 |
| 前端、目录及隔离构建 | **114 tests passed**，34 个 Manifest 校验通过；生产构建生成 **82 页**，未覆盖在用前台。 |
| Astro | 49 文件，**0 errors / warnings / hints**。 |
| 文档 | **55 项、0 过期受控块、0 错误**。 |

最终脱敏对照确认依赖、解释器、Git 提交、`config/sdks.json`、默认 Runtime 选择、`pyproject.toml` 及固定执行模块均与本轮实施前一致；源码变化通过行为轨迹核对，不用源码摘要代替行为检查。当前本地检查使用 Python **3.14.5**、Node **24.16.0**，不代表 Windows Python 3.11/3.13、Node 22 的远端 CI 已通过。中英文诊断和布局自动检查通过；本轮未做浏览器像素级窄屏截图复核。

持久测试报告保存在忽略的 `.local-data/shared-results/`：`full-offline-final-v2.xml`、同名日志与状态、`final-qualification.xml`、`no-workbuddy-import-final.xml`。中断的测试迭代和原生句柄旧断言失败不计为完整通过；相关问题修复后才形成上述完整报告。SDK `0.3.247`、CLI `2.141.0`、锁定 worker、模型和生产配置未升级。

本轮没有访问真实模型或 SAP、派发 Campaign、启用 WorkBuddy、重启服务、发送邮件、发布/启用业务对象、提交或推送。**全部 18 项操作的新摘要仍待另行确认的真实验证**；离线通过不满足开放选择条件。平台工具仍逐次授权，可信本地 `full_access` 仍不是操作系统级防旁路隔离。

All eighteen operations now enter SDK-independent business orchestration, retaining pre-existing uncommitted work. Twelve Planner operations share prompts, Schema, stages, pagination and business checks; draft feedback shares prompting/decoding/candidate checks, while workflow v2, discovery and the three Harness operations share their respective lifecycles. Thin drivers translate native execution, progress, terminal output and owned-process management. SDK installations, interpreters, authentication and the pinned worker remain isolated. Fixed Agents, published workflows and historical acceptance are unchanged.

Immutable before-source fixtures cover representative mocked-SDK paths for all eighteen operations, including full prompt/Schema/permission/stage/result comparisons, without regenerating fixtures or making model/SAP calls. Common Broker admission validates normalized scope and Skill parameters; ascending normalization precedes strict plan checks, and incomplete Schema cannot justify removing confirmed fields. Role matching shares paginated 300-second stages; legacy WorkBuddy workflow composition/feedback retains 3600 seconds, without extending Codex deadlines. Native WorkBuddy IDs remain metadata rather than shared platform conversation keys; compatible legacy input never enables unverified native continuation. Unknown terminal length/hash is distinct from a received empty string.

One complete logged-in-user Python run exits successfully with **1410 passes, one existing symlink skip and one Starlette warning** in 847.63 seconds; crypto rules and DPAPI assertions remain intact. A final metadata-only expansion of qualification digest coverage is followed by **164 targeted passes**, including six native-compatibility mutation cases. The complete suite precedes that metadata expansion, and these overlapping counts are not summed as a new full suite. An SDK-import-denial simulation passes **78** cases without uninstalling existing packages or claiming clean-install CI. Frontend checks pass **114** tests, all **34** manifests validate, an isolated production build generates **82** pages, Astro reports no diagnostics across **49** files, and **55** documentation checks pass without stale blocks or errors. Bilingual/layout automation passes; browser pixel-level narrow-screen screenshot verification was not performed.

Redacted dependency/interpreter/commit/SDK-configuration/default-selection/project-dependency/deterministic-execution controls match the start of this migration. Behavior comparisons, not unchanged source hashes alone, protect Codex compatibility. Local Python **3.14.5** and Node **24.16.0** do not certify remote Windows Python 3.11/3.13 or Node 22 CI. Durable reports under `.local-data/shared-results/` distinguish completed checks from interrupted iterations and the repaired legacy-session assertion. SDK/CLI/worker/model/production configuration remain pinned. Qualification binds orchestration version/digest, including native compatibility helpers; historical records remain immutable and earlier qualification cannot certify changed code.

No real model/SAP call, Campaign, Runtime enablement, service restart, mail send, business publication/activation, commit or push occurred. **All eighteen operations require separately authorized live validation against their new digests** before WorkBuddy becomes selectable. Platform tools retain per-call authorization, and trusted-local full access remains outside OS-level bypass isolation.

## 新编排启用门禁真实验证 2026 10 06 / Live enablement gate check

本轮按已确认的分阶段完整验证启动，但在模型前置探针失败后停止；不将尚未执行的阶段记为失败或通过。

- 保存本轮前后 Codex 脱敏对照，结果完全一致：配置、默认 Runtime、依赖、解释器、Git 提交和固定执行链路未改变。
- 活动任务为空后，通过现有启动器恢复 API 与前台；当前构建生成 82 页，API 和中英文首页健康。API 返回的 18 项编排摘要与当前源码相符。启动器按既有缓存保留策略清理一份旧前台缓存，不删除业务资料。
- 锁定 SDK **0.3.247**、CLI **2.141.0** 和环境 `add87d43e14b9fddb5b59b64e94e5b31fda7659bf4ccd0904d9f37d7411b1a07` 不变。各执行一次登录和 `hy4-preview` 模型探针：登录成功；模型探针返回 **`workbuddy_execution_failed`**，未取得有效输出或实际模型身份，当前兼容性检查记录为失败。
- 此次 SDK trace 记录 `Error in agent run`、生成阶段 `cancelled / Error streaming response`，trace 时长 1156 ms。它没有保存 HTTP 状态或更具体的根因；不能据此认定网络、额度、模型权限或 SAP 故障，也不能将 SDK 的生成阶段标签解释为用户取消。平台作业终态为 `failed`，不是 90 秒超时。
- 两个所属探针作业均确认进程清理，无活动或 `cleanup_pending` worker。**18 项新编排操作全部未派发，有效资格仍为 0/18；SAP 调用、选样、Campaign 均为零。** 未自动重跑探针或恢复旧模型检查为当前成功结果。
- 当前阻断为 `runtime_model_check_required`、`workbuddy_operation_validation_required`，另保留用户停用状态 `runtime_disabled`。WorkBuddy 未启用，Codex 仍默认；服务保持健康，没有提交、推送或发布业务对象。

独立报告位于忽略的 `.local-data/workbuddy-baseline/shared-live-20261006/verification-summary.json`，包含逐项未执行矩阵、安全 SDK 诊断、所属作业、清理及服务状态；原始本轮失败保留在 `bootstrap-report.json`。前后对照为同目录上级的 `shared-live-20261006-before.json` 和 `shared-live-20261006-after.json`。后续需先解决模型流式响应失败，再经明确安排启动新探针；本轮不为新编排授予任何真实操作资格。

门禁相关离线回归为 **108 passed**，报告为 `gate-regression.xml`；两项警告分别是既有 Starlette 弃用提示和沙箱账户不能写入正常账户的 pytest 缓存，不修改缓存权限或跳过断言。文档检查 **55 项、0 错误**。这些离线结果不替代失败的真实模型探针。

This separately authorized live-validation pass stops at the model prerequisite, without treating undispatched operations as passed or failed. Redacted Codex before/after controls are identical. With no active tasks, the existing launcher restores the API and frontend, builds 82 pages and confirms API/bilingual-page health; the running API projects the current orchestration digests. The launcher's existing retention policy removes one obsolete frontend cache, not business data.

The pinned SDK **0.3.247**, CLI **2.141.0** and environment remain unchanged. One authentication probe passes; one **hy4-preview** compatibility/identity probe returns **`workbuddy_execution_failed`**, with no valid result or verified actual-model identity. Its SDK trace contains `Error in agent run` and a generation span labelled `cancelled / Error streaming response` (1156 ms trace duration), but no HTTP status or specific cause. These labels do not establish a user cancellation, network/quota/permission fault or SAP error. The platform job is `failed`, not a 90-second timeout.

Both owned probes confirm cleanup. **All eighteen business operations remain undispatched and qualification remains 0/18; no SAP read, discovery or Campaign runs.** No automatic retry or restoration of an old successful model check occurs. The current technical gates are model compatibility and new-orchestration operation qualification; WorkBuddy also remains disabled by choice. Codex stays default, services remain healthy, and no publication, activation, commit or push occurs. The separate safe summary and retained initial failure under `.local-data/workbuddy-baseline/shared-live-20261006/` distinguish prerequisites, undispatched operations and unknown root cause. A newly authorized probe is required after resolving the streaming failure; this pass grants no live-operation qualification.

Focused offline gate regressions pass **108** tests (`gate-regression.xml`), with the existing Starlette deprecation and a sandbox-user pytest cache warning; no permissions or assertions are weakened. Documentation checks pass **55** documents with zero errors. Neither offline check substitutes for the failed real model probe.

## 用户要求继续后的新编排验证 2026 10 06 / Explicitly requested continuation

用户再次要求继续后，各项只派发一次新的验证；此前探针、夹具与业务失败保留，不覆盖上一节记录。

- 新的 `hy4-preview` 探针 **通过**，42.33 秒，实际模型身份明确，清理完成；当前模型兼容门禁已解除。此次成功不能证明此前流式响应失败的根因已经确定或修复。
- 冻结相同 SDK **0.3.247**、CLI **2.141.0**、安装环境及各操作编排摘要；所有验证均从当前共享入口进入。工作流创建/反馈及编写仍使用 3600 秒，岗位模型阶段仍使用 300 秒，计划生成保持原有 120 秒。没有为取得 PASS 增加预算、补造字段或放宽检查。
- 记录、Agent 草稿及工作流均使用隔离数据目录。本轮 **SAP 调用为零、Campaign 为零**；非 SAP 前置门禁未通过，SAP 查询、选样和正式验收没有派发。

### 本轮逐项结果 / Operation results

| 操作 | 结果与实际核对 |
| --- | --- |
| `plan` | **未通过**，122.15 秒（含收尾）；`workbuddy_deadline_exceeded`，120 秒内没有取得终态。所属进程清理完成；没有输出可供判断字段或 JSON 错误，不自动重跑。 |
| `ground_plan` | **未执行**，计划生成前置失败。 |
| `summarize` | **通过**，22.63 秒；双语说明离线样例没有 SAP 证据，不改写确定性不完整结论。 |
| `author_draft` | **通过**，82.25 秒；真实 SDK 输出经平台工厂保存到隔离草稿，Manifest、双语说明、只读定义及重复创建幂等检查通过。输入明确为离线样例，不代表 SAP 试运行或验收。 |
| `review_agent_feedback` | **通过两种模式**；`bounded` 解释 60.58 秒，修订不变；`trusted_local` 修改 94.52 秒，README 字节精确追加，只产生一个修订及 `/readme` Diff。执行、规则、验收与执行摘要不变。 |
| `compose_workflow` | **通过**，35.71 秒；真实编译器及隔离保存成功，固定 Agent 版本/摘要明确，无邮件或其它集成。 |
| `review_workflow` | **通过**，37.65 秒；规范结构与双语检查通过。 |
| `repair_workflow` | **通过**，19.24 秒；只修复错误连接端口，固定绑定与其它定义不变。 |
| `review_workflow_feedback` | **通过**，59.63 秒；反馈经平台生成隔离修订，固定绑定不变。 |
| `review_free_query_feedback` | **通过**，四类真实调用共 146.58 秒；展示修改、查询范围变更、新问题和需求不明分别进入 reinterpret、requery、start_new_session、clarify。旧夹具缺少必需 `thread_id` 的 `TypeError` 发生在 SDK 派发前；修正夹具后仅执行首次真实分类验证，失败记录仍保留。 |
| `revise_free_query_presentation` | **通过**，22.62 秒；平台保存展示修改，事实值、字段、完整性和证据来源不变，零 SAP 读取。 |
| `analyze_role_matching` | **未通过**，55.51 秒；SDK 原生终态和进程清理成功，但业务理解阶段的内嵌 `analysis_json` 不符合 JSON 语法，保存错误为 `Expecting ',' delimiter: line 1 column 2677 (char 2676)`。外层原生 Schema 的字符串字段不保证内层 JSON 正确，不能取得岗位资格。原始内层文本未由夹具保存，不推断具体业务字段或自动补逗号。 |
| `review_role_matching_feedback` | **未执行**，没有有效的首次岗位分析修订。 |
| `workflow_authoring.v2` | **通过**，可信本地解释 97.73 秒、仅改标题 93.78 秒；解释不产生修改，修改只产生一个修订和 `/title/zh`、`/title/en` Diff。完整 JSON 终态、稳定引用、原请求幂等和固定 Agent 绑定核对通过。 |
| `free_query`、`sample_discovery` | **未执行**；计划、计划补全、岗位分析及岗位反馈前置资格不足。 |
| `acceptance_baseline`、`acceptance_free_query` | **未执行**；前置资格不足，未创建 Campaign，没有接触历史答案或验收资料。 |

本轮 **10/18 项取得当前编排完整资格**，两项实际失败，六项因依赖未执行。草稿反馈资格同时包含 `bounded` 和 `trusted_local`；工作流 v2 只声明已验证的 `trusted_local`。当前技术阻断为 `workbuddy_operation_validation_required`，另保留 `runtime_disabled`；**`can_enable=false`，WorkBuddy 仍停用、Codex 仍默认**。18 项门禁未通过，不开放用户选择。

确认全部所属作业 `cleanup_complete=true`，无活动或待清理 worker。API 和中英文首页健康，API/前台 PID 分别仍为 3172/8716，本轮没有重启服务。Codex 前后脱敏对照完全一致，配置、默认选择、依赖、解释器、Git 提交及固定执行链路不变。没有更改共享编排或 SDK 源码、升级环境、发布/启用业务对象、提交或推送。

追加离线定向回归 **189 passed、1 warning**，41.89 秒；警告为既有 Starlette 弃用提示，未跳过断言。此数字只代表本轮共享编排、原生轨迹、环境隔离、模型身份及剩余契约的定向测试，不重复声称全量 Python、前端或远端 CI 已验证。

独立汇总为忽略的 `.local-data/workbuddy-baseline/shared-live-20261006/continuation-summary.json`，包含 18 项矩阵、按权限模式的资格、失败与未执行区别、所属作业、安全诊断、服务和 Codex 对照。`continue-model-probe-report.json`、`continue-isolated-report.json` 与各唯一目录/回合报告保留本轮实际记录；`continuation-regression.xml` 保留离线结果。前后对照是上级目录的 `shared-live-continue-20261006-before.json`、`shared-live-continue-20261006-after.json`。早先的 `verification-summary.json` 和 `bootstrap-report.json` 保持原样。

After the user's explicit request to continue, one new **hy4-preview** probe passes in **42.33 seconds**, with known actual-model identity and complete cleanup. The earlier streaming failure remains preserved and its root cause is still undetermined. SDK **0.3.247**, CLI **2.141.0**, the installed environment and orchestration digests stay frozen. All live operations use the current shared entry points and isolated persistence; no SAP call or Campaign occurs, and no failed model/business operation is automatically repeated.

**Ten of eighteen operations are fully qualified:** honest bilingual summarization, isolated Agent creation/idempotency, draft explanation and exact README-only modification in both required modes, legacy workflow creation/review/connection repair/feedback, all four free-query feedback decisions, persisted presentation-only revision, and trusted-local workflow v2 explanation/title-only revision with stable references, one revision, unchanged fixed bindings and exact-request idempotency. Native SDK termination is not sufficient on its own; platform contracts and persistence checks pass before each qualification is saved.

Planning times out under its unchanged **120-second** budget (**122.15 seconds** including cleanup), with no terminal output to inspect. Grounding is not dispatched. Role analysis receives a valid native outer object but fails parsing the embedded **analysis_json** during business understanding in **55.51 seconds**, with a saved missing-comma error at line 1, column 2677; an outer string Schema cannot guarantee inner JSON syntax. Its dependent feedback is not dispatched. No inner-text repair or inferred business-field diagnosis is made without retained raw output. SAP free query, discovery and both acceptance stages remain gated, leaving **two actual failures and six undispatched operations**, not eight execution failures. A missing required thread ID in an old classification fixture is rejected before SDK dispatch; that fixture is corrected and the first actual classification attempt passes, without repeating presentation-only revision or erasing the fixture failure.

All owned processes confirm cleanup; no active/pending-cleanup worker remains. API and bilingual pages are healthy, with unchanged API/frontend PIDs **3172/8716**. Redacted Codex controls match exactly. The current gates are operation qualification and the deliberate disabled state: **can_enable=false**, WorkBuddy remains disabled, and Codex remains default. There is no Runtime/source/environment upgrade, service restart, business publication/activation, commit or push. **189 targeted offline tests pass** with one existing Starlette warning in **41.89 seconds**; this does not re-certify the full suite, frontend or remote CI. The separate continuation summary, unique stage reports and before/after controls preserve both this pass and the earlier failed probe without rewriting history.

## 原生输出编码修复后验证 2026 10 06 / Native-output encoding repair

本轮针对上一轮计划生成未收尾和岗位内嵌 JSON 语法错误，修复 WorkBuddy 驱动的原生输出编码，不修改 Codex 原生参数、提示或业务算法：

- 计划生成与其它已迁移操作一样传入原生输出 Schema，必须取得 `StructuredOutput` 终态；仍使用原有 120 秒预算，禁止 Shell、文件读取及外部工具。
- 岗位分析的 SDK 无关传输投影将 `analysis_json` 表示为类型化 JSON 对象，再由平台无损序列化回现有字符串接口。引用仍由原有注册表校验；不截取模型文本、猜补逗号、填充缺失字段或伪造引用。Codex 保留原 wire Schema，平台业务语义不变。
- Windows 锁定环境检查每次核对 worker 入口内容摘要，避免同尺寸快速改写命中旧 stat 缓存。没有原地修改已安装 worker、升级 SDK 或变更安装环境；原来的失败断言保留，新增冻结 stat 的回归样例。
- 编排摘要变化使原先 10/18 资格不能为本轮代码背书，历史记录保持原样。SDK **0.3.247**、CLI **2.141.0**、`hy4-preview` 及锁定环境不变；复用此未改变环境下已通过的模型身份检查，不额外重复认证或模型探针。每项新验证从冻结的共享入口派发，依赖项失败时不自动重跑。

扩展定向回归为 **244 passed、1 warning**，15.63 秒；警告为既有 Starlette 弃用提示。此前扩展初跑的两项失败保留：worker 摘要缓存缺口已修复，计划模拟夹具已改为原生终态，同时保留 Shell、读取、联网、子任务与 Skill 的明确拒绝断言。该结果不是全量或远端 CI 结果。

确认无活动任务后，通过现有启动器完成一次受控 API/前台重启，复用已检查的生产构建，未覆盖当前制品。四条旧排队记录属于已取消父会话，原样保留，不将其视为活动作业或改写历史状态。API 健康，运行中的 18 项摘要与冻结源码相符；Codex 仍默认、WorkBuddy 仍停用。真实业务记录与草稿均在独立数据目录，SAP 调用只能经过原有 Broker，计划输入及范围门禁不因验证而放宽。

This pass fixes WorkBuddy wire encoding rather than changing Codex calls or shared business algorithms. Planning now requires native `StructuredOutput` under its unchanged **120-second** budget, without Shell, file or external-tool access. The SDK-neutral role transport represents the embedded analysis as a typed JSON object and losslessly serializes it back to the existing string envelope. Original citation/business validators remain authoritative; no text extraction, invented punctuation, default fields or citations are added. Codex retains its original wire Schema.

Locked-environment checks hash the worker entrypoint on every check, closing a same-size rapid-write stat-cache gap without changing the installed worker or SDK environment. The original assertion remains, with an added frozen-stat regression. **244 targeted tests pass** with one existing Starlette warning in **15.63 seconds**; initial failures remain preserved and are not relabelled. Changed orchestration digests invalidate earlier qualification without rewriting history. SDK **0.3.247**, CLI **2.141.0**, **hy4-preview** and the installed environment stay pinned; the previously successful model-identity check is reused only because its environment binding is unchanged, without additional probes.

With active work empty, the existing launcher performs one controlled API/frontend restart using an already validated production build. Four queued historical turns under a cancelled parent are preserved, not executed or relabelled. Runtime digests match the frozen source, Codex remains default and WorkBuddy stays disabled. Business fixtures use isolated persistence; SAP access remains Broker-only and validation never relaxes scope admission. The following reconciliation records actual completed checks separately from dependencies that were not dispatched.

### 本轮真实结果 / Live results for this pass

| 操作 | 验证结果与耗时（秒） |
| --- | --- |
| `plan` | **通过**，28.72；合法只读计划，目标过滤、三个字段和 `top=1` 保留。 |
| `ground_plan` | **通过**，24.50；确认字段、过滤和读取上限未丢失。 |
| `summarize` | **通过**，19.79；双语说明离线样例没有真实 SAP 证据。 |
| `author_draft` | **通过**，54.42；隔离工厂保存、Manifest、双语、只读及幂等检查成立。 |
| `review_agent_feedback` | **通过两种模式**；`bounded` 解释 77.43，修订不变；`trusted_local` 修改 116.00，仅精确追加 README，单修订、`/readme` Diff，执行/规则/验收摘要不变。 |
| `compose_workflow` | **通过**，49.72；真实编译及隔离保存，固定 `mm-po-gr-status 0.1.1` 和摘要，无外部集成。 |
| `review_workflow` | **通过**，48.56；规范结构与双语检查成立。 |
| `repair_workflow` | **通过**，28.13；仅修复错误连接端口，其它定义与固定绑定不变。 |
| `review_workflow_feedback` | **通过**，73.76；反馈保存为隔离修订，固定绑定不变。 |
| `review_free_query_feedback` | **通过**，四类调用合计 155.22；正确选择 reinterpret、requery、start_new_session、clarify。 |
| `revise_free_query_presentation` | **通过**，25.22；仅改展示，事实、完整性、来源和原记录不变，零 SAP 读取。 |
| `analyze_role_matching` | **通过**，691.28；31 个 Agent、8 页完整覆盖，引用及汇总有效。每阶段仍限 300 秒，没有延长阶段预算。 |
| `review_role_matching_feedback` | **通过**，793.06；完整目录覆盖和引用检查成立，第二修订保存，第一修订摘要不变。 |
| `workflow_authoring.v2` | **通过**，可信本地解释 82.83、标题修改 110.84；单修订，仅 `/title/zh`、`/title/en` Diff，稳定引用、幂等及固定绑定不变。 |
| `free_query` | **未通过资格门禁**，238.25；详见下方原生终态与证据诊断。 |
| `sample_discovery` | **未执行**；自由查询前置门禁未通过。 |
| `acceptance_baseline` | **未执行**；前置资格不足，没有创建 Campaign。 |
| `acceptance_free_query` | **未执行**；前置资格不足，没有创建 Campaign。 |

本轮 **14/18** 完整资格，一项实际验证未通过，三项依赖未派发。SAP 自由查询是一次作业中的有界只读取证：第一次请求返回 `sap_read_timeout`，随后同范围成功取得一行；两次请求都限定采购订单 `4500001466` 的抬头、三个字段和 `top=1`。没有扩大实际读取范围；查询尝试两次、成功一次，不把一次作业内的工具纠错说成两次独立验证。

阻碍相互独立，不能只处理其中一个：

1. SDK 的结果事件 `is_error=false` 不等于原生结构化终态已取得。虽然可观察到多次 `StructuredOutput` 调用和成功的 `sap_final_report_validate`，锁定 worker 实际收到的 `ResultMessage.structured_output` 仍为空，明确拒绝为 **`workbuddy_structured_output_missing`**。共享 Harness 保存 `harness_runtime_degraded`，时间并未耗尽，并保留只读取证。具体为何原生捕获为空，尚缺原生输出工具的安全字段级校验诊断；不能猜测某个字段、模型权限或网络原因，也不能截取普通文本、补字段或仅凭工具完成事件授予资格。
2. 已存在的共享读取规则在 `embedded_odata.py` 中对任何显式 `top` 设置 `source_complete=false`；本例返回一行仍不能证明完整来源。这与验证夹具同时要求显式 `top=1` 和完整成功的条件冲突。该规则对 Codex 同样适用，本轮没有改动它。后续完整来源用例应先用实时 Schema 核对唯一键，保持同一 PO 的精确过滤，再设计不依赖显式截断的有界用例；不能仅修改期望值追求 PASS，也不能把本轮有限样本改写为完整证据。

平台原始终态事件是 **`run_inconclusive`**，缺口为 `harness_runtime_unavailable`，不是业务差异或 600 秒超时。隔离夹具因拒绝资格随后将其存储状态记为 `failed`；安全诊断明确区分平台事件和夹具状态覆盖，不重写已保存记录。报告工具验证曾成功，但原生终态失败使该报告未成为最终可展示结果。自动选样与两项验收均未派发，Campaign 为零，正式 Agent、生产草稿和历史业务验收没有改写。

最终正常账户核对：`can_enable=false`，阻断为 `workbuddy_operation_validation_required` 及用户保留的 `runtime_disabled`；WorkBuddy 仍停用、Codex 仍默认，API 与中英文首页健康。全部所属进程清理完成，无活动或 `cleanup_pending` worker。Codex 配置、默认选择、依赖、解释器、Git 提交和受保护执行源码的前后脱敏对照一致。没有自动重复失败作业、发布/启用业务对象、提交或推送。

完整正常 Windows 登录账户 Python 回归为 **1546 passed、1 skipped、1 warning**，1585.62 秒、退出码 0；跳过项是本机无法创建符号链接的附件导入测试，警告仍为 Starlette 弃用提示，DPAPI 断言没有修改或跳过。定向 244 项与全量结果是重叠检查，不相加。最终文档检查为 **55 项、0 过期受控块、0 错误**；本轮没有前端源码变化，不将此前前端构建或远端 CI 冒充本轮新验证。

独立记录位于忽略的 `.local-data/workbuddy-baseline/native-live-20261006/`：`verification-summary.json`、`sap-diagnostic.json`、`controller-report.json`、`binding-report.json`、`full-regression.xml`，以及每项唯一目录/回合报告。Codex 对照为上级目录的 `native-repair-20261006-before.json` 和 `native-repair-20261006-after.json`。上一轮 10/18、先前模型探针及所有失败记录保持原样。后续先补齐原生捕获的安全诊断和验证案例口径，再安排剩余操作；若修复改变编排或 worker 环境摘要，必须重新核对资格，不能为新绑定复制旧 PASS。

**Fourteen of eighteen operations are fully qualified.** Planning and grounding preserve the exact scope under the original planning budget; role analysis and feedback now cover all **31 Agents across eight pages**, validate document references and consolidation, and persist revisions without changing the first result. Summarization, isolated Agent creation, both draft-feedback modes, the four legacy workflow operations, all free-query feedback decisions, presentation-only revision and trusted-local workflow v2 pass their platform checks. The recorded durations distinguish whole paginated operations from their unchanged **300-second** model-stage budgets; legacy authoring remains **3600 seconds**.

One SAP free-query qualification attempt does not pass, and **three dependent operations are not dispatched**. Two Broker query attempts remain within the exact PO-header predicate, three selected fields and `top=1`: the first times out and the second returns one row. This is one job's bounded tool correction, not an automatic repeat of a failed validation job. The first blocker is confirmed missing native termination: despite observed `StructuredOutput` calls, a successful report-validation tool and a non-error SDK result event, the pinned worker receives **no `ResultMessage.structured_output`** and rejects it as **`workbuddy_structured_output_missing`**. Why capture is empty still requires safe native-tool validation diagnostics; no field error, provider permission or network cause is inferred without that evidence, and ordinary reply text is never promoted to structured success.

Separately, the existing shared OData policy marks every explicit top-bound read incomplete, conflicting with the fixture's complete-success expectation. This applies to Codex too and is not changed here. A future complete-source fixture needs a live-confirmed unique-key predicate and a bounded design without relying on explicit truncation; this pass's result cannot be upgraded by weakening expectations. The actual platform event is **inconclusive**, with preserved evidence and `harness_runtime_unavailable`, not a business mismatch or deadline exhaustion. The fixture subsequently stores a failed qualification status; the separate diagnosis records that distinction without rewriting either record. A validated intermediate report does not survive the runtime-degraded final result. Discovery and both acceptance stages remain gated, with **zero Campaigns** and no production business-object changes.

Logged-in-user reconciliation confirms **can_enable=false**, operation qualification still required, deliberate disablement, Codex default, healthy API/bilingual pages and complete owned-process cleanup. Redacted Codex controls match. One complete offline Python run passes **1546** tests, with one host-symlink skip and one existing Starlette warning, in **1585.62 seconds**; DPAPI checks are not weakened. The overlapping **244** targeted checks are not added to that full-suite count. The separate frozen-pass reports preserve earlier failures and qualification history. No automatic retry, Runtime enablement, business publication/activation, commit or push occurs; later orchestration/environment changes require fresh binding checks rather than copied PASS records.

## 剩余阻碍的离线诊断准备 2026 10 07 / Offline blocker diagnostics

本轮只做离线代码与用例修复，不启动模型、SAP 查询、Campaign 或服务切换，不修改已安装 release、Runtime 启停及历史资格记录。

新增可协商的原生输出诊断：worker 将候选参数或原生终态临时提交给平台，平台针对冻结 Schema 复用现有校验器；仅返回校验状态，持久化摘要、类型、字段路径及约束，不持久化参数值、工具文本、SDK 会话 ID或受限原始行。消息同时核对 task、attempt、调用编号及 Schema 摘要，沿用帧大小、总输出、截止时间和清理限制；该内部消息不转成 SAP 工具调用。诊断不会修补参数、放宽业务校验，或把合法的候选参数当作 SDK 原生终态。原生终态为空，即使此前工具结果 `is_error=false`，仍拒绝资格。

新的验证用例明确分流：保留显式 `top=1` 的有限样本必须如实报告不完整，不授予完整查询资格；另一完整来源用例先取得本轮实时元数据的完整唯一键，再允许相同订单、实体及三个字段、不含显式截断的精确读取。现有读取完整性策略没有修改。两类用例分别显式执行，使用全新隔离目录；原用例与历史报告保持不变。独立验证断言不再把平台的 `inconclusive` 写成 `failed`。

新增入口默认 dry-run，不读取运行状态、不执行模型或 SAP。真实执行前检查当前操作摘要、权限模式、冻结模型、环境及 worker 内容；更新 worker 需创建新锁定环境，认证、模型和全部受影响操作重新验证。现有资格表不复制、不补写。源码编排摘要变化后，上一轮 14/18 不能直接为新代码背书；运行中旧服务没有在本轮切换。

本轮针对原生候选有效但终态缺失、Schema 字段诊断、消息绑定与越权、无敏感原值、有限样本不授予资格、完整键缺失或不完整元数据拒绝、读取范围保护、平台结果不覆盖，以及现有 Codex 编排对照执行离线回归。正常 Windows 登录账户下 **235 passed、1 warning**，22.89 秒、退出码 0；警告为既有 Starlette 弃用提示。文档检查 **55 项、0 过期受控块、0 错误**；目录校验 **34 个 Agent 包、6 个模块**通过。新入口有限样本 dry-run 退出码 0，不创建验证目录或启动服务。本轮没有前端源码变化，未重新执行全量 Python、前端构建或远端 CI；不将上一轮成绩计为本轮新测试。

This pass prepares diagnostics, not a proven native-capture fix. The worker negotiates a bounded, run/attempt/schema-bound internal check using the existing platform validator. Transient candidate values are not persisted; only safe schema issues and fingerprints cross the diagnostic boundary. No Broker dispatch, permission extension, text repair, terminal substitution or qualification occurs through that check. Missing native termination continues to fail closed even after a valid candidate and non-error tool result.

The new opt-in fixture separates incomplete explicit-top sampling from a live-schema-confirmed exact unique-key complete case. Both retain the same order, entity and field scope, with existing result/pagination/time limits. Assertion failures are stored separately without rewriting platform status. Dry-run does not initialize runtime state, a model or SAP. Installed releases and historical evidence remain unchanged; new worker bytes require a new immutable environment and fresh affected qualifications. No model, SAP, Campaign, service restart, Runtime enablement, business publication, commit or push is performed in this offline pass.

The logged-in Windows account passes **235 targeted offline tests** with one existing Starlette warning in **22.89 seconds**. Documentation validation checks **55 documents**, with no stale controlled blocks or errors; catalogue validation passes **34 packages across six modules**. The limited-sample dry-run exits successfully without creating a validation directory or starting services. The overlapping initial 44-test and 234-test runs are not added to the final count. The final JUnit report is retained locally at `.local-data/workbuddy-baseline/native-diagnostics-20261007/regression-final.xml`. No new full-suite, frontend-build or remote-CI result is claimed. Native capture and operation qualification remain pending live verification under the new binding.

## 新诊断 worker 的实际验证 2026 10 07 / Diagnostic-worker live validation

创建新的不可变环境 `93ac6d54d49d990338d172f14ac511dda9886004bcc0873022241c1b93a03375`，保留旧环境及其报告。使用已核对的本地官方 wheel 和独立安装解释器，SDK **0.3.247**、CLI **2.141.0**、模型 **hy4-preview** 及平台 Python 依赖不变；没有原地改写旧 worker。复用本机登录记录的认证探针通过（4.87 秒），具体模型身份与兼容检查通过（33.43 秒）。

两个只用合成数据的原生捕获探针通过：简单 Schema 15.64 秒，共享 Harness 完整 Schema 与两个模拟工具 31.69 秒。候选参数及 SDK 终态均通过 Schema 检查，并确认原生输出存在。这只能证明该锁定组合能完成这些用例，不能证明上一轮真实 SAP 查询的原生捕获故障已消失，也不授予业务操作资格。

所有业务验证使用当前共享编排、全新隔离记录和平台原有检查器；旧通过记录未复制，新环境中当前资格为 **13/18**：

| 操作 | 本轮真实结果 |
| --- | --- |
| `plan`、`ground_plan`、`summarize` | 通过；分别 26.63、31.97、21.39 秒，冻结范围与结构检查有效。 |
| `author_draft` | 通过；65.62 秒，隔离保存、双语与只读定义检查有效。 |
| `review_free_query_feedback`、`revise_free_query_presentation` | 通过；四类反馈合计 135.49 秒，仅重新解释 30.93 秒；事实、完整性、来源及原记录不变。 |
| `compose_workflow`、`review_workflow`、`repair_workflow`、`review_workflow_feedback` | 通过；分别 33.79、60.95、20.08、45.95 秒，真实编译与固定版本绑定检查有效。 |
| `review_agent_feedback` | 两种模式通过；解释 51.95 秒不修改修订，可信本地修改 124.66 秒仅精确追加 README，执行、规则和验收定义不变。 |
| `workflow_authoring.v2` | 可信本地解释及修改通过；57.71、99.54 秒，单修订、逐轮 Diff、幂等与固定引用不变。 |
| `analyze_role_matching` | 通过；746.82 秒，当前 31 个活动 Agent、8 页均覆盖，引用与汇总完整。每个模型阶段仍限 300 秒。 |
| `review_role_matching_feedback` | 未授予资格；866.16 秒，分页覆盖完整，但最后汇总未完成，详见下方。 |
| `free_query`、`sample_discovery`、`acceptance_baseline`、`acceptance_free_query` | 未派发；岗位反馈前置资格未通过，SAP 调用与新 Campaign 均为零。 |

岗位反馈不是目录缺失或引用失败：第二修订已保存，`agent_catalog_complete=true`、`matching_complete=true`，但 **`consolidation_complete=false`**，并保存 `role_matching_consolidation_incomplete`；没有生成可用工作流建议。平台会话的 `completed` 表示流程已结束，不代表完整业务结果或 Runtime 资格。验证断言拒绝此结果，没有把第二修订删除或改写成成功。

最后汇总在原 300 秒阶段预算末尾提交空对象，原生候选检查明确报告缺少 `/analysis_json`、`/summary_zh`、`/summary_en`，未观察到该作业的有效 SDK 终态。共享编排的既有兜底吞掉汇总异常，当前保存记录不足以确定是哪种异常先触发；不能把缺字段、时限或 SDK 故障互相推定为根因。此前页面阶段的终态都有效，但不为失败的汇总背书。没有自动重跑反馈、提高预算、补字段或继续 SAP 验证。

失败作业的进程已经退出，但留下 `running` 记录。先按 PID 启动标识确认原进程不存在，再使用现有恢复逻辑将该操作状态记为 `interrupted / cleanup_complete=true`；这是可变作业状态的恢复，不改历史验证报告。最终核对没有活动或 `cleanup_pending` worker。下一步须补齐汇总阶段的原始安全异常与输入/输出规模诊断，再决定修复方向；不能直接复制上轮的岗位反馈 PASS。

只读查询验证入口的一项准备检查发现原始 Runtime 字典与平台类型化存储投影的差异：后者带有 `model_catalog_digest=null`。验证器现在使用同一 `RuntimeSnapshot` 投影，保留所有环境、模型、配置及资格绑定，不通过忽略摘要差异放宽范围。该修复只涉及验证脚本和离线用例，不改变共享业务编排或 SDK 驱动，未触发 SAP。

本轮正常账户离线检查分别为 **130 passed、1 warning**（诊断、范围、隔离，9.82 秒）和 **93 passed、1 warning**（共享编排、Codex 对照、历史绑定，29.01 秒）。警告均为既有 Starlette 弃用提示；不冒充全量 Python 或远端 CI。记录保存于忽略的 `.local-data/workbuddy-baseline/native-diagnostics-live-20261007/`，包括 `probe-report.json`、`continuation-report.json`、`role-diagnostic.json`、清理核对及两个 JUnit 报告。WorkBuddy 保持用户停用，Codex 仍为默认；本轮没有 SAP 读取、邮件、业务发布/启用、提交或推送。

The new immutable diagnostic-worker release preserves its predecessor and keeps SDK **0.3.247**, CLI **2.141.0**, **hy4-preview** and platform dependencies pinned. Existing authentication and concrete-model checks pass. Simple native capture and a complete shared-Harness Schema with two synthetic tools pass, including candidate and terminal checks. These probes qualify no business operation and do not prove the historical live-SAP capture fault repaired.

Fresh isolated business validation qualifies **13 of 18 operations** against the new environment and current orchestration. Planning/grounding/summarization, Agent creation, both free-query feedback operations, four legacy workflow operations, both draft-feedback permission modes, trusted-local workflow v2 and complete-catalog role analysis pass their existing platform checks. No historical PASS is copied. Role feedback covers all **31 active Agents across eight pages**, but its saved second revision has **consolidation_complete=false** and `role_matching_consolidation_incomplete`. Session completion is not completeness or qualification, and the assertion rejects it without rewriting that revision.

Near the original 300-second final-stage deadline, the model submits an empty structured candidate, missing `analysis_json`, `summary_zh` and `summary_en`; no valid terminal for that job is observed. The existing consolidation fallback discards the original exception, so these records cannot identify which exception first caused fallback or prove a particular SDK/network cause. Page-stage success does not qualify the final stage. The process exits leaving a running job record; identity-checked existing recovery marks it interrupted with confirmed cleanup, preserving the original verification reports. No active or pending-cleanup worker remains. SAP query, discovery and both acceptance operations remain undispatched; there is no SAP read or Campaign, retry, budget increase or fabricated output.

The query verifier now uses the same typed Runtime projection as stored runs, retaining all binding checks, including the nullable model-catalog field. This is verification-only, not a business/driver change. Logged-in-user targeted suites pass **130** and **93** cases, each with the existing Starlette warning; these are not a new full-suite or remote-CI claim. WorkBuddy remains deliberately disabled and Codex stays default. No mail, business publication/activation, commit or push occurs. Safe reports under the ignored current-pass directory preserve probes, operation results, final-stage schema issues and process reconciliation.

最终核对：Codex 配置、默认 Runtime、依赖、解释器、Git 提交及受保护源码的脱敏对照完全一致，旧 release 未变。启动器确认现有 API（8765）和前台（4321）健康并直接复用，没有重启或重新构建；进一步只读检查中英文页面均为 HTTP 200，运行中 API 返回新环境、当前编排摘要和 **13/18** 资格，`can_enable=false`。技术门禁仍为 `workbuddy_operation_validation_required`，另保留 `runtime_disabled`。文档检查 **55 项、0 过期受控块、0 错误**，目录校验 **34 包、6 模块**；前端源码未改，不宣称本轮新前端构建或远端 CI。`audit-report.json`、`cleanup-reconciliation.json` 和 `service-audit.json` 保存上述最终核对。

Final redacted Codex configuration/default/dependency/interpreter/commit/protected-source controls match exactly, and the old release is unchanged. The launcher reuses the already healthy API/frontend rather than restarting or building. Bilingual pages return HTTP 200; the live API projects the new environment, current orchestration digests and **13/18** qualification with **can_enable=false**. Operation validation remains the technical gate, alongside deliberate disablement. Documentation checks cover **55** documents without stale blocks/errors, and catalogue validation passes **34** packages across **six** modules. No new frontend build or remote-CI result is claimed. Final audit, cleanup and service-projection reports are retained separately.

## 岗位汇总与取消清理离线修复 2026 10 07 / Offline role-final and cleanup repair

确认并修复两类平台缺口：共享编排原先丢弃最后汇总异常；WorkBuddy 外层到期不等待被取消回合清理，且 `shield` 不能保证取消发生在清理期间时调用方仍等待结果。新增安全阶段诊断与字段问题保存；自有客户端和监督器共用最多 10 秒的清理截止时间，未确认时阻止重试。排队任务在派发时重新检查清理状态，迟到输出拒绝写入历史，取消不影响其它作业或 Codex。

实际原生请求只保存输入长度、摘要、Schema 摘要和预算到作业记录，不保存模型提示或命令。岗位报告的阶段指纹对应共享业务提示；驱动的请求指纹另外包含其原生编码与平台上下文，两者不能混称为同一输入。空对象的三个必填字段诊断去重后保留，错误类型与完整性规则不放宽。岗位结果页双语区分“目录匹配完整，但汇总未完成”，技术原因默认折叠，旧报告不重新分类。

原有汇总提示、输出 Schema、300 秒阶段预算、SDK、CLI、模型及已安装诊断 release 均不修改。本轮不重跑真实反馈或 SAP 验证，不能据离线取消测试宣称空对象的生成根因已解决。时间相近的本机 CLI 元数据缺少可靠的平台作业绑定，不用它认定本次 SDK 或网络故障。

新源码绑定的有效资格为 **0/18**：上一轮 13 项真实通过记录保留，但共享诊断、驱动及监督器摘要变化后需要重新验证。不是 18 项都运行失败，也不复制旧 PASS。监督器新增纳入资格摘要，避免以后仅修改取消/清理规则却沿用旧资格。平台侧修改不需要替换不可变 worker 环境；Codex 配置、默认选择、依赖、解释器、原有驱动及提示源码、安装环境和 worker 内容的前后安全指纹相同。首次对照脚本因 JSON 列表与 Python 元组表示差异产生的误报保留，使用同一序列化表示后再次核对一致，不改平台规则掩盖它。

记录位于忽略的 `.local-data/workbuddy-baseline/role-consolidation-repair-20261007/`，保留首次失败的离线测试和最终回归。初次新增测试发现 Python 局部异常名遮蔽，已修复；兼容回归发现不相关入口的新增事件改变顺序，诊断事件限定到岗位阶段，旧断言未删改。前端测试允许读取隔离构建目录，原有断言保持不变，不覆盖正在服务的构建。真实模型、SAP、Campaign、邮件、发布/启用、服务重启、Git 提交或推送均为零。

Two confirmed platform gaps are repaired: the shared final-stage fallback discarded its cause, and WorkBuddy cancellation could return before owned cleanup and record persistence, including cancellation during cleanup itself. Safe diagnostics, a single bounded cleanup deadline, post-queue admission rechecks and late-history fencing address those reproducible paths without changing Codex execution. Only fingerprints, lengths, schema constraints and budgets are saved; the shared-prompt and native-wire fingerprints are distinct. Bilingual UI separates catalog coverage from consolidation and keeps technical details collapsed; historical results remain unchanged.

The consolidation prompt, Schema and 300-second stage budget, pinned SDK/CLI/model and installed immutable worker remain unchanged. No model, SAP or acceptance validation is rerun; the cause of the historical empty native candidate is not inferred from unrelated/unbound CLI telemetry. Current-source qualification becomes **0/18** after digest changes, while the previous **13** genuine passing records are preserved for their old binding. Supervisor behavior is now included in qualification fingerprints. A serialized-type mismatch in the first audit fixture is preserved and corrected using equivalent JSON representations; configuration/dependency/default/installed-worker controls then match. New offline tests also expose and fix a local exception-name shadow and an unrelated event-order change without weakening existing assertions. All original and final reports remain under the isolated ignored directory. No Runtime enablement, service switch, business mutation, commit or push occurs.

最终正常账户定向 Python 回归 **363 passed、1 warning**，109.39 秒；警告是既有 Starlette 弃用提示，没有跳过失败。包含 Codex 不可变请求轨迹、原预算、各 WorkBuddy 入口、历史绑定及清理期间取消等检查，不等同于全量 Python 或真实模型验证。前端全量 **117 项通过**；Astro **50 文件、0 错误/警告/提示**，隔离生产构建 **82 页**。合成组件在中英文、1440px/550px/390px 下键盘展开成功且无横向溢出，不连接实际平台 API。文档检查 **55 项、0 过期块/错误**，目录 **34 包、6 模块**通过；未触发远端 CI、SAP 或服务切换。

Final targeted Python regression passes **363** cases in **109.39 seconds**, with one existing Starlette warning and no skipped failures. It includes immutable Codex request/budget traces, WorkBuddy compatibility, historical binding and cancellation-during-cleanup checks, not a complete Python suite or live-model qualification. All **117** frontend tests pass against an isolated **82-page** production build; Astro checks **50 files** with zero errors/warnings/hints. Synthetic bilingual component checks at **1440/550/390px** confirm keyboard disclosure and no horizontal overflow, without contacting the platform API. Documentation checks **55** documents with no stale blocks/errors and catalogue validation passes **34** packages in **six** modules. No remote CI, SAP or service switch is performed.

## 冻结编排后的岗位实测 2026 10 07 / Frozen-orchestration role validation

确认正式数据库没有活动任务、没有活动或待清理 worker 后，通过现有启动器受控重启 API 和前台，加载上一轮平台修复。启动器先完成目录检查及 **82 页**构建再停止旧服务；API、中英文页面恢复健康。继续使用不可变环境 `93ac6d54d49d990338d172f14ac511dda9886004bcc0873022241c1b93a03375`、SDK **0.3.247**、CLI **2.141.0** 和 **hy4-preview**，没有新建安装环境或复制旧资格。Codex 配置、默认选择、依赖、解释器、受保护源码及 worker/环境文件摘要的前后对照一致。本轮平台编排源码没有修改。

使用旧岗位验证输入及原断言，在新的隔离数据库中执行一次岗位分析，计划通过后再执行反馈。材料理解及 **8 个分页阶段**均取得有效原生结构化终态；最终汇总输入 **43,222 字节**，保持原有 **300 秒**模型阶段预算，但预算内没有取得结构化终态。整个分析 **832.86 秒**后失败，服务错误为 **`runtime_cleanup_incomplete`**，没有保存可审核结果修订或取得操作资格。未观察到该汇总阶段的空对象或字段错误，不能套用上次空对象诊断。岗位反馈、其余非 SAP 验证及自由查询均未派发；**SAP 调用和 Campaign 为零**，不自动重跑。

失败后确认所有所属进程已经退出，作业记录为 `completed` 或 `cancelled / cleanup_complete=true`，没有活动或 `cleanup_pending` worker。代码调查发现独立的平台诊断缺陷：`WorkBuddyClient.__aexit__` 从当前及已完成历史作业的所有清理截止时间取最小值，已完成作业的过期截止时间会让当前回合等待窗口立即归零，错误报告清理未完成并覆盖原始阶段超时。离线模拟确认：加入已完成作业的过期时间时等待仅 **0.0001 秒**即报错；只考虑当前作业时 **0.0203 秒**完成清理且不报错。模拟不调用 SDK、模型或 SAP，也不修改生产代码。它证明清理等待算法存在缺陷，不证明模型为什么未能及时完成汇总。

记录位于忽略的 `.local-data/workbuddy-baseline/frozen-validation-20261007-final/`，包括 `before.json`、`freeze.json`、`roles-summary.json`、新隔离岗位报告、`roles-events.jsonl`、`role-diagnostic.json`、`stale-cleanup-deadline-proof.json` 和 `after.json`。旧 14/18、13/18 及失败记录保持原样；当前资格仍为 **0/18**，WorkBuddy 停用、Codex 默认，未发布/启用业务对象、提交或推送。下一步需先修正已完成作业影响当前清理窗口的缺陷，保留原始超时与独立清理诊断，再根据汇总输入规模与时限进行单独修复评估；不通过扩大预算、补字段或挑选历史 PASS 消除本次失败。

With no active business tasks or pending workers, the existing launcher rebuilds **82 pages before stopping services** and performs one controlled API/frontend restart. The same immutable worker, SDK **0.3.247**, CLI **2.141.0** and **hy4-preview** are retained; no qualification is copied. Redacted Codex configuration/default/dependency/interpreter/source and immutable-release controls match before and after, and orchestration source remains frozen.

One isolated role-analysis attempt obtains valid native terminal objects for understanding and **eight catalogue pages**. Final consolidation receives **43,222 input bytes** but no native terminal within the unchanged **300-second** stage budget. The operation fails after **832.86 seconds**, reported as **runtime_cleanup_incomplete**, without a saved result revision or qualification. No empty final candidate or final field error is observed, so the previous empty-object diagnosis is not substituted. Feedback and dependent validations are not dispatched, with **zero SAP calls and Campaigns** and no automatic retry.

All owned processes eventually exit and their records confirm cleanup. An SDK-free reproduction independently demonstrates a platform defect: the client mixes expired cleanup deadlines from completed turns into the current turn's deadline, reducing its wait to zero and masking the original stage timeout. With a completed turn's expired deadline it raises after **0.0001 seconds**; with only the current turn it completes cleanup in **0.0203 seconds** without error. This proves the cleanup algorithm defect, not the cause of the model's consolidation timeout. Original reports remain immutable. No production fix, Runtime enablement, business publication, commit or push occurs in this pass; current qualification remains **0/18**.

## 清理修复与原生空流定位 2026 10 08 / Cleanup fix and native empty-stream diagnosis

本轮修复 `WorkBuddyClient` 的清理截止时间选择：只用仍待完成回合所属作业的截止时间，不让已完成作业的过期时间压缩当前窗口；当前作业自身已经过期时仍阻断，不给它重新签发预算。另外，将岗位原生对象按原生对象保存为后续上下文，避免把已经验证的对象再次编码成转义 JSON 字符串；对外规范结果仍保持旧字符串信封。没有增减业务字段、补造缺失输出或修改 Codex 路径、模型、SDK、CLI 及原 300 秒阶段预算。不可变 worker 内容不变，没有新建环境。

正常 Windows 账户的 Python 全量回归为 **1609 passed、1 skipped、1 warning**，耗时 **992.01 秒**。跳过项是现有 `test_import_rejects_external_attachment`，警告为既有 Starlette 弃用提示；没有通过新增跳过或放宽旧断言消除失败。三个补充清理用例覆盖完成作业的过期截止时间、保留当前过期截止时间及归属隔离；原生上下文用例核对双引号、换行、业务对象及原 API 信封均无损。完整报告为忽略目录中的 `full-regression-20261008.xml`。

一次隔离汇总重放前，出站安全检查因历史请求可能含内部资料而拒绝。随后只读来源核对证明：请求精确摘要绑定至失败作业，只有 **1 段合成岗位描述、0 上传文档**；规范记录来自该描述的前序模型输出，全部引用指向它；**5 个候选 Agent 契约**与干净 `origin/main` 的公开目录投影完全一致。重新提交同一请求的安全审查后才执行，不通过其它入口绕过拒绝。重放仅验证汇总阶段，不是完整岗位分析，也不授予操作资格。

修复后的重放仍未在 **300 秒**内取得结构化终态，但 **300.53 秒**后准确报告 `runtime_deadline_exceeded` 且清理完成，不再被错误的 `runtime_cleanup_incomplete` 覆盖。输入为 **42,734 字节**原生上下文；没有观察到结构化候选或 Schema 字段错误。通过请求摘要精确匹配官方 CLI 追踪及日志，确认本次原生生成在约 **174.88 秒**收到 `finish_reason="error"`，CLI 将其分类为 **reasoning-only / EmptyStreamError**：收到 **364 个流片段、551,764 字节**，但没有实质最终模型输出。日志中的字节数是原生流总量，不是可用业务结果。官方通用提示为 `Error streaming response`；底层网关为何结束该请求没有被报告，不能据此猜测网络、权限、限流或 token 上限。

另一个不同目的的对照探针使用同一 **43,222 字节**已核实参考文本，只要求最小结构化确认，不执行岗位任务；**hy4-preview 在 19.36 秒内返回有效终态，清理正常**。它证明模型和该上下文大小不是全局不可用，不证明复杂汇总或任何业务操作合格。配置回退告警也出现在相邻无空流的日志中，不能仅凭告警认定路由错误。两次探针均使用原锁定环境，SAP 调用为零，不调用邮件或发布服务、不复制旧 PASS、不重跑失败业务流程。

记录位于忽略的 `.local-data/workbuddy-baseline/role-final-probe-20261008/` 与 `large-context-control-20261008/`，包含安全来源审查、失败及对照报告、精确追踪绑定和仅保留白名单遥测的结论。当前实际复现的阻碍是复杂岗位汇总的上游响应流终止；普通小任务成功不能解除它。岗位反馈及 SAP 查询、选样、两种验收资格尚未在本轮取得；旧自由查询原生终态故障也不能宣称已经修复。当前源码有效资格仍为 **0/18**，旧 14/18、13/18 保持历史绑定。WorkBuddy 保持停用、Codex 默认。若继续修改汇总阶段契约或更换模型/SDK，需先确认超出当前冻结边界的方案；本轮不直接改变业务编排追求 PASS，也不宣称该 SDK 或模型永久无法使用。

The client now derives its bounded cleanup drain only from still-pending owned turns. Expired deadlines of completed jobs cannot mask a current timeout, while an expired current deadline remains fail-closed. Validated native role objects are retained losslessly for subsequent context; the legacy public JSON-string envelope remains unchanged. No missing business field is invented, no Codex execution/request policy changes, and the pinned worker, SDK/CLI/model and 300-second stage budget remain intact.

The complete logged-in Windows Python suite passes **1609 cases**, with **one existing skip and one Starlette warning**, in **992.01 seconds**. New regressions cover owned deadline isolation and native context round-tripping without weakening prior assertions. Before a single final-stage diagnostic replay, an outbound review rejects potentially sensitive historical input. A read-only exact-fingerprint audit establishes one synthetic description, no uploaded documents, references solely to that description and five Agent contracts identical to the clean public origin snapshot. The same execution is then resubmitted for review rather than bypassing the rejection.

The corrected replay still times out after **300.53 seconds**, but cleanup completes and the original deadline error is preserved. No native candidate or Schema-field error is observed. Exact input binding to the official CLI trace/log establishes an upstream **finish_reason=error**, classified as **reasoning-only / EmptyStreamError**, after approximately **174.88 seconds**, **364 chunks** and **551,764 raw streaming bytes** without substantive final output. This is not usable business data, and the gateway's underlying reason is not reported. A separate minimal-output control acknowledges the same **43,222-byte** verified reference in **19.36 seconds** with a valid terminal and clean shutdown. It rules out blanket model/input-size unavailability, not the complex consolidation failure. Configuration fallback warnings also occur in an adjacent log without empty-stream errors and are not proof of a routing fault.

Both probes qualify no operation and perform no SAP/business write. Current qualification remains **0/18** after source changes; previous 14/18 and 13/18 reports remain valid only for their historical binding. Role feedback and dependent SAP validations remain unqualified, and the historical live-query capture fault is not claimed repaired. WorkBuddy stays disabled and Codex default. Altering the consolidation contract or pinned model/SDK requires a separately confirmed scope; no permanent SDK/model impossibility is inferred from these diagnostics. All safe probe and provenance artifacts remain under ignored local directories, without raw log/prompt copies in published documentation.

两次探针及所有回归结束后，重新确认正式数据库无活动任务、无待清理 worker，受控重启 API 和前台加载本轮驱动修复。启动器先验证已有静态构建再停止旧进程，复用原构建而非宣称新增生产构建。重启后 API、中英文页面均为 HTTP 200，运行中 API 的环境、停用和启用门禁与当前管理状态一致；Codex 配置、默认选择、依赖、解释器及不可变环境的脱敏对照仍完全一致。文档检查 **55 项、0 过期块、0 错误**，目录检查 **34 包、6 模块**，Git 差异格式检查无错误。没有提交、推送或业务启用。

After both probes and regression finish, a fresh no-active-task/no-pending-worker check permits one controlled API/frontend restart. The launcher verifies and reuses the existing static build before stopping old processes; no new production build is claimed. API and bilingual pages return HTTP 200, live Runtime projections agree with current disabled/eligibility state, and protected Codex/default/dependency/interpreter/immutable-release controls still match. Documentation checks **55** documents with no stale blocks/errors; catalogue validation covers **34** packages in **six** modules and Git whitespace checks pass. No commit, push or business activation occurs.

## 独立非 SAP 操作重新验证 2026 10 08 / Independent non-SAP requalification

在同一冻结源码、不可变环境 `93ac6d54d49d990338d172f14ac511dda9886004bcc0873022241c1b93a03375`、SDK **0.3.247**、CLI **2.141.0** 和 **hy4-preview** 下，继续执行与岗位汇总无依赖的已批准非 SAP 验证。生产启用门禁没有改变；每个操作族只执行一次，沿用原验证输入、业务检查和权限模式。没有更换模型、重跑岗位失败任务或把历史 PASS 复制到当前绑定。

当前源码的完整有效资格为 **12/18**：

| 操作 | 本轮真实结果与耗时（秒） |
| --- | --- |
| `plan`、`ground_plan`、`summarize` | 通过；39.91、32.00、25.98。精确过滤、字段及 `top=1` 保留；总结如实披露合成证据不完整，没有调用 SAP。 |
| `author_draft` | 通过；107.29。通过真实创建服务保存到隔离数据库，Manifest、双语、只读定义及幂等检查有效。 |
| `review_free_query_feedback`、`revise_free_query_presentation` | 通过；四类反馈合计 161.35，仅重新解释 23.70。事实、证据别名、来源及完整性保持不变，重新解释零次 SAP 读取。 |
| `compose_workflow`、`review_workflow`、`repair_workflow`、`review_workflow_feedback` | 通过；44.75、48.48、29.52、57.95。真实编译、端口修复范围、双语及固定 Agent 版本/Digest 检查有效，反馈落入隔离修订。 |
| `review_agent_feedback` | **两种模式均通过**；有界解释 80.13，不改修订；可信本地修改 144.48，只精确追加 README、一个修订和 `/readme` Diff，执行、规则与验收定义不变。 |
| `workflow_authoring.v2` | **可信本地解释和修改均通过**；80.81、111.31。解释不改定义，修改仅改变 `/title/zh`、`/title/en`，单修订、逐轮 Diff、稳定引用、请求幂等及固定绑定均有效。 |
| `analyze_role_matching` | 当前绑定未取得资格；已有本轮独立汇总重放确认上游 reasoning-only / `EmptyStreamError`，见上一节。不重复运行。 |
| `review_role_matching_feedback` | 未派发；岗位分析资格尚未通过，不标为本轮实际运行失败。 |
| `free_query`、`sample_discovery`、`acceptance_baseline`、`acceptance_free_query` | 未派发；完整非 SAP 前置门禁尚未满足，SAP 调用及新 Campaign 为零，不宣称旧查询终态故障已经解决。 |

所有通过项均同时取得有效原生结构化终态、通过平台业务检查并确认所属进程清理完成，才由原验证入口登记资格；原生候选有效不单独授予资格。草稿及工作流编写继续使用 **3600 秒**原预算，岗位模型阶段仍为 **300 秒**，没有因验证方便放宽断言或提高时限。

最终独立审计核对 18 项当前编排摘要、冻结模型与环境、每项报告及所属作业绑定，并确认运行中 API 显示 **12/18、can_enable=false**。仅剩资格门禁和用户保留的 `runtime_disabled`；这不是 6 项都执行失败。无活动业务任务、活动 worker 或待清理进程，API 与中英文首页均 HTTP 200。Codex 配置、默认选择、依赖、解释器、受保护源码及不可变 worker/环境对照仍一致；WorkBuddy 仍停用、Codex 仍默认。本轮平台执行源码没有修改，不重启服务、不切换页面构建，也不宣称重新执行了上一节全量回归。

逐族报告、冻结快照、安全事件和 18 项审计矩阵位于忽略的 `.local-data/workbuddy-baseline/independent-nonsap-20261008/`，最终入口为 `audit-report.json`。历史 14/18、13/18、0/18 的不同绑定和失败报告保持原样。下一步如需改变共享岗位汇总契约或锁定模型/SDK，先另行确认范围并重新执行相关 Codex 对照；当前没有通过更换入口、延长预算、重复运行或补字段解除上游阻碍。没有正式草稿修改、业务发布/启用、邮件发送、提交或推送。

The frozen source, immutable environment, SDK **0.3.247**, CLI **2.141.0** and **hy4-preview** are retained. Seven independent, previously approved non-SAP families run once with their existing fixtures, permissions and platform assertions. Production enablement still requires all eighteen operations; no failed role task is retried and no historical qualification is copied.

**Twelve operations now hold current complete qualification.** Planning/grounding preserve exact scope; summary honestly reports incomplete synthetic evidence. Isolated Agent creation, all free-query feedback decisions, zero-read reinterpretation, the four legacy workflow operations, both draft-feedback permission modes and trusted-local workflow v2 pass native termination, real platform business checks and owned-process cleanup. Explanation does not mutate definitions; draft modification creates one README-only revision; workflow v2 creates one bilingual-title-only revision with stable references, fixed bindings, per-round Diff and exact-request idempotency. Existing 3600-second authoring and 300-second role-stage budgets remain unchanged.

The remaining six are **not six newly failed executions**: role analysis remains unqualified after the already documented upstream reasoning-only empty stream; role feedback is not dispatched; four SAP/acceptance operations remain gated. This pass performs **zero SAP calls and creates zero Campaigns**, and does not claim the historical real-query terminal problem repaired. A final independent audit checks every current digest, frozen binding, report and owned job, confirms the live API's **12/18 and can_enable=false**, healthy API/bilingual pages, no active business/worker/cleanup jobs and identical protected Codex/default/dependency/interpreter/immutable-release controls. WorkBuddy stays disabled and Codex default. Source execution code and services are unchanged in this pass; no new full-suite/build result, Runtime enablement, production-object mutation, email, commit or push is claimed. Dated historical evidence remains untouched. Safe reports and the complete matrix are retained locally under the ignored independent-pass directory.

### 剩余阻碍边界复核 / Remaining-blocker boundary review

随后只读核对当前锁定 SDK、精确绑定的失败追踪及[官方 Python SDK 参考](https://www.codebuddy.cn/docs/cli/sdk-python)、[故障排查指南](https://www.codebuddy.cn/docs/cli/troubleshooting)和[模型配置指南](https://www.codebuddy.cn/docs/cli/models)。锁定客户端在错误消息后结束响应，或遇到带错误的结果时抛出异常；它不会生成缺失的业务终态。这个源码行为不证明失败作业实际收到过哪种消息。失败追踪只保留通用生成/流错误，没有可核对的 token 上限、HTTP 错误码或更底层网关原因；未检索到针对该锁定组合、本次空流的确定官方修复说明。当前官方文档中的新选项或升级建议不能直接作为旧版本的兼容证明，也没有据此修改模型、推理配置、SDK 或 CLI。

实时 API 再次确认 **12/18、can_enable=false、WorkBuddy 停用、Codex 默认、0 待清理 worker**。同一剩余阻碍已经持续三个连续目标回合；前两个回合的驱动修复和独立资格进展保留，不把它们当作岗位问题已经解除。当前授权边界内没有已证实可执行的进一步修复：继续拆分共享汇总或更换锁定模型/SDK，需要明确范围选择；外部厂商修复则需要新的证据。目标仍未完成，不能宣称 SDK 或模型永久不可用。边界审计保存在忽略的 `.local-data/workbuddy-baseline/blocked-audit-20261008.json`；本次只读核对没有模型、SAP、服务切换、启用、提交或推送。

A subsequent read-only review checks the pinned client, exactly bound failure trace and the official Python SDK/troubleshooting/model guides. The pinned client terminates on an error message or raises for an error result; it cannot invent a missing business terminal. This does not establish which SDK message the failed job actually emitted. The trace supplies only generic generation/stream errors, not a verified token ceiling, HTTP status or underlying gateway cause. The reviewed current documentation does not establish a remedy for this pinned failure, and new documented options do not prove old-version compatibility. No configuration, model or SDK change follows from this research.

The live API reconfirms **12/18, disabled WorkBuddy, Codex default and no pending cleanup**. The same remaining blocker persists across three consecutive goal turns despite real progress on other operations. The complete objective remains unmet. Further shared-contract changes, a different pinned runtime or an external vendor remedy require a scope decision or new evidence; no permanent model/SDK impossibility is claimed. The ignored boundary audit preserves the original scope, affected operations and evidence without qualifying unexecuted stages or repeating model/SAP tasks.

## 获批的共享汇总修复 2026 10 08 / Approved shared-consolidation repair

本轮按用户确认的完整修复计划实施，而非继续受上一节未确认的范围限制。新不可变 worker 环境为 `7f50527cc86bc14b162caef67332b136444c870e15753b0cbf4d1967e4b6d650`；旧 release 保留，SDK **0.3.247**、CLI **2.141.0**、依赖与 **hy4-preview** 不升级。认证和实际模型身份检查通过，WorkBuddy 仍停用。模型阶段仍为 **300 秒**，工作流编写仍为 **3600 秒**。

首先按精确输入、前序上下文和原 Schema 摘要完成一次无 SAP 的诊断复现。原输入 **43,222 字节**；新 worker 在 **134.23 秒**内取得有效结构化终态，清理确认，无资格登记。本次未复现历史空流，不能认定 SDK／网关缺陷已修复，也不能将其代替完整岗位分析。原先上游错误的底层原因仍未知。证据位于忽略目录 `.local-data/workbuddy-baseline/unblock-20261008/original-contract-report.json`，只有安全遥测与指纹，没有复制原生推理日志。

共享末轮改用 `sapba.role-consolidation/1` 四字段 Schema，平台保留理解、分页匹配和拒绝记录，执行逐操作对账并保存建议、缺口及逐轮 Diff。明确错误在 SDK 关闭前上报；缺失终态、中间候选、关闭挂起和清理失败分别检查，原始错误不会被清理异常替代。无确切 SDK 错误的静默调用仍按原预算超时。完整操作资格必须绑定新环境、模型、编排摘要及业务检查，旧 **12/18** 仅保留为历史证据。

With the user's explicit approval, this pass implements the shared-contract repair previously outside scope. A new immutable worker release retains the exact SDK **0.3.247**, CLI **2.141.0**, dependencies and **hy4-preview**, preserving the old release. Authentication and concrete model identity pass; WorkBuddy remains disabled. An exact, provenance-checked original-contract diagnostic replay receives a valid terminal after **134.23 seconds** with confirmed cleanup and zero SAP reads. It qualifies no business operation and does not establish that the intermittent gateway fault is fixed.

The strict four-field final contract retains canonical/page records under platform control. Per-operation reconciliation, saved suggestions/gaps and revision Diff, immediate safe native-error signaling and separate cleanup outcomes are covered offline. Intermediate candidates cannot substitute for terminals. Silent unfinished calls retain their original deadline. Historical qualifications remain immutable and cannot certify the changed source/environment; fresh complete qualification is evaluated separately below.

### Codex 真实对照与新增门禁 / Live Codex comparison and newly exposed gate

首次完整 Python 回归发现一个旧测试替身仍实现 `receive_response`，与新 worker 使用的官方 `receive_messages` 不一致。只修正替身接口，全部旧断言保留；最终全量为 **1629 passed、1 个既有 skipped、1 个 Starlette warning**，耗时 **1104.77 秒**。前端 **119 项**、Astro **50 文件零错误/警告/提示**、目录 **34 包/6 模块**、文档 **55 项**和隔离 **82 页**生产构建通过。合成组件在中英文及 **1440/550/390px** 下键盘操作成功，无横向溢出。Codex 配置、默认选择、依赖、解释器和既有受保护源码的脱敏指纹一致。

Codex 验证夹具最初漏传应用的管理探针，在模型请求前被拒绝；该次单独保存为 `not_run / model_jobs=0`。修复夹具初始化后只启动一次真实分析，使用现有 **gpt-5.6-sol** 及原有权限、推理和预算。分析 **254.66 秒**后保存可查看的部分结果：**31 个候选、8 页、31 个业务操作/Agent配对**均完成，唯一操作由 `mm-po-gr-status` 完整覆盖。但末轮在 **4.792 秒**失败，`consolidation_complete=false`，没有派发反馈。不能把会话 `completed`、对账完整或分页结果当作完整资格。

原输入摘要精确绑定到官方 Codex 追踪，终态错误明确拒绝新原生 Schema 中的 `uniqueItems`，涉及 `workflow_suggestions.operation_ids`；该末轮没有助手消息。只保存类型计数、字段与安全错误分类，不复制追踪、推理或异常原文。这是本轮新增的 **Codex 原生 Schema 适配问题**，不是 SAP、业务比较、300 秒超时或已证明的 WorkBuddy 上游空流。原保存报告保持原诊断，不追溯重写；附加定位保存在 `codex-trace-diagnostic.json`。

修复仅在 Codex 新末轮的原生编码中移除不支持的关键词，平台严格 Schema 仍保留唯一性，重复引用仍阻断。新增离线测试验证这个边界、无原生 Schema 错误原文及所有其它操作原请求轨迹；没有重跑失败的真实作业。WorkBuddy 岗位分析/反馈、其余12项非SAP重验及4项SAP/验收验证全部等待前置门禁，**SAP调用和新Campaign为零**。需要对新冻结绑定另行启动真实 Codex 对照，通过后才能继续完整 WorkBuddy 验证；当前不宣称18项通过或可启用。

The complete offline suite passes **1629 cases** with one existing skip and one Starlette warning after correcting an outdated fake message method without weakening assertions. Frontend **119** tests, Astro, catalogue/documentation checks, an isolated **82-page** build and bilingual keyboard/narrow-screen checks pass. Protected Codex/default/dependency/interpreter controls match. A verifier-only initialization fault is retained as a preflight `not_run` with zero model jobs.

The first actual Codex analysis takes **254.66 seconds** and completes all **31** candidate pairs across **eight** pages. Its sole operation is covered by the fixed PO receipt Agent, but final consolidation fails after **4.792 seconds**. Exact request binding to the official terminal establishes rejection of `uniqueItems` in the new native Schema, with no final assistant output. This is a concrete Codex wire-Schema compatibility defect, not SAP, a business difference, timeout or proof of WorkBuddy's historical empty stream. Original partial results remain unchanged; supplemental diagnosis contains only safe metadata.

The narrowly scoped encoder omits that keyword only for the new Codex final contract. Platform uniqueness, evidence, compiler and business checks remain strict, and unrelated Codex requests are unchanged. Offline regressions cover duplicate rejection and safe errors. No failed live job is automatically rerun: Codex feedback and all dependent WorkBuddy business/SAP/acceptance validation remain undispatched. Fresh validation against the repaired binding is still required; WorkBuddy remains disabled, Codex default, and no SAP reads, Campaign, business publication, commit or push occurs.

### 最终离线回归与交付状态 / Final offline regression and handoff

原生 Schema 编码修正后的 Python 全量报告为 **1632 passed、1 existing skipped、0 failures/errors**，耗时 **3957.15 秒**，保存于忽略目录中的 `full-regression-native-schema.xml`。全量运行后补充的错误关键词引号变体由最新定向用例单独核对：**5 passed**；此前共享编排与编码定向检查 **113 passed**。既有跳过和 Starlette 弃用警告没有被用来掩盖失败。前端 **119 项通过**，Astro **50 文件、0 错误/警告/提示**，隔离构建 **82 页**；中英文、1440px/550px/390px 合成页面的键盘操作及溢出检查通过。目录 **34 包、6 模块**及文档 **55 项**检查通过；这不是远端 CI 或新的真实模型资格。

最终安全审计确认 Codex 配置、默认选择、依赖及受保护执行文件的前后对照一致；已发布 Agent 包不变，没有活动业务任务或待清理 worker。无活动任务时受控重启后，API 和中英文前台均返回 HTTP 200。运行中 API 与本地状态一致：新绑定有效资格 **0/18**、`can_enable=false`、WorkBuddy 停用、Codex 默认。原资格和真实失败报告保留原绑定，未被覆盖或复制。

本轮交付平台修复与离线验证，但尚未解除 WorkBuddy 启用门禁。因首次真实 Codex 对照发现原生 Schema 不兼容，修正后须重新冻结绑定并获准执行新的 Codex 分析与反馈，再继续 WorkBuddy 的 18 项验证；没有自动重跑失败作业。安全原因矩阵、原契约复现、独立审计和逐项未派发状态保存在 `.local-data/workbuddy-baseline/unblock-20261008/`。本轮 SAP 调用、Campaign 创建均为零，未发送邮件、发布、启用、提交或推送。

The final Python report after native Schema encoding repair records **1632 passes, one existing skip and zero failures/errors** in **3957.15 seconds**. Subsequent quoted-keyword diagnostic variants pass a separate **five-case** focused run; the earlier shared-orchestration/encoding suite passes **113** cases. All **119** frontend tests pass, Astro checks **50 files** with no errors/warnings/hints, and the isolated build produces **82 pages**. Bilingual keyboard/overflow checks at **1440/550/390px**, **34-package/six-module** catalogue validation and **55-document** checks pass. Existing skips and deprecation warnings are disclosed, not used to suppress failures; no remote CI or live-model qualification is inferred.

The final audit confirms identical protected Codex configuration/default/dependency/execution controls, unchanged published Agent packages and no active business or pending-cleanup worker. A controlled restart after those checks restores HTTP 200 API and bilingual pages. Live and saved projections agree on **0/18 current-binding qualifications, can_enable=false, disabled WorkBuddy and Codex default**, with historical records preserved. Platform repair is delivered but enablement is not cleared. A newly frozen, explicitly authorized Codex comparison must precede dependent WorkBuddy validation; no failed live job is automatically retried. Safe replay, causes, audit and undispatched-stage records remain in the ignored local delivery directory. This pass performs zero SAP calls and creates no Campaign, sends no mail and does not publish, enable, commit or push.

## Codex 续验与单 Agent 建议对账修正 / Codex continuation and single-Agent suggestion reconciliation

收到用户继续授权后，冻结新的隔离验证绑定并执行一次 Codex 岗位分析。耗时 **254.33 秒**，完整核对 **31 个候选、8 页**，取得有效的新四字段汇总终态；没有再出现 `uniqueItems` 原生 Schema 拒绝。唯一操作由可执行且 PASS 的 `mm-po-gr-status` 完整覆盖。末轮同时提出单 Agent 工作流建议 `workflow_001`，已经通过现有编译器，但新增对账器将其误报为 `role_matching_combination_unsupported`，因而整体汇总未通过。该会话的 `completed` 状态不代表业务资格通过。反馈以及 WorkBuddy 后续验证未派发，没有 SAP 调用或 Campaign。

原因已由保存结果和代码共同确认：现有工作流契约允许至少一个步骤，新对账却额外要求至少两个不同 Agent。修正仅允许已由同一可执行 PASS Agent 完整覆盖的合法单 Agent 建议；它不作为组合证据，不让部分覆盖升级为完整覆盖，不放宽编译器、引用、只读或证据检查。原建议的离线回放通过，原数据库摘要保持不变；原失败报告仍保持失败，不复制或重新绑定资格。相关回归 **115 passed、1 既有 Starlette 警告**，随后全量 Python **1639 passed、1 既有 skipped、1 既有 warning**，耗时 **918.84 秒**。报告保存为 `full-regression-single-agent.xml`。没有修改旧断言或跳过失败；离线通过不等同于修正后的真实对照。

所有安全记录保存在忽略目录 `.local-data/workbuddy-baseline/resume-20261008-schema/`。源摘要再次变化后，原冻结绑定不能为新代码背书；须重新冻结并取得新的真实验证记录。SDK **0.3.247**、CLI **2.141.0**、**hy4-preview**、Codex 配置及现有预算不变。没有自动重跑失败作业；WorkBuddy 保持停用、Codex 默认，未发送邮件、发布、启用、提交或推送。

Following explicit continuation, one newly frozen Codex analysis takes **254.33 seconds** and checks **31 candidates across eight pages**, with a valid four-field final terminal and no native `uniqueItems` rejection. The sole operation is fully covered by executable PASS `mm-po-gr-status`. A proposed single-Agent workflow also passes the existing compiler, but the new reconciliation incorrectly rejects it for having fewer than two distinct Agents. Session completion is not business qualification; feedback and dependent WorkBuddy verification are not dispatched, with zero SAP calls and Campaigns.

The focused correction permits a compiled single-Agent suggestion only where that same executable PASS Agent already provides full coverage. It cannot turn a partial match into a validated combination and does not weaken references, compiler, read-only or evidence checks. Offline replay passes with an unchanged original database; the original failed result is neither rewritten nor qualified. **115** focused regressions pass, followed by a full Python suite of **1639 passes, one existing skip and one existing warning in 918.84 seconds**. Prior assertions are not weakened and no failed test is newly skipped. Current-source qualification still requires new frozen live records, not these offline checks. Pinned versions, models, protected Codex controls and budgets remain unchanged; no automatic failed-job retry or enablement occurs. Safe artifacts remain in the ignored continuation directory.

本续验未修改前端源码，未重新宣称前端或生产构建结果；前一轮的 **119 项前端测试、Astro 零错误及 82 页隔离构建**保留原验证记录。全量 Python 结束后再次确认无活动业务任务、无待清理 worker，才受控重启服务加载对账修正。当前源摘要有效资格仍为 **0/18**；这表示待重新验证，不表示十八项均运行失败。WorkBuddy 停用、Codex 默认、SAP 和 Campaign 为零，保护对照一致。新的真实尝试需要使用新的冻结绑定，原失败作业不自动重跑。

This continuation does not modify frontend source or claim a new frontend/production-build run; the earlier **119 frontend passes, clean Astro check and 82-page isolated build** retain their original records. A new idle/cleanup check after the full Python suite precedes the controlled restart. Current-source qualification remains **0/18 pending revalidation**, not eighteen failed executions, with disabled WorkBuddy, Codex default, zero SAP calls/Campaigns and identical protected controls. A fresh live attempt requires a new frozen binding rather than automatic retry of the failed job.

## 新冻结绑定完整续验 2026 10 08 / Fresh-binding live continuation

本轮在 `.local-data/workbuddy-baseline/resume-20261008-single/` 保存新的冻结编排、保护对照和隔离记录。SDK **0.3.247**、CLI **2.141.0**、**hy4-preview**、环境 `7f50527cc86bc14b162caef67332b136444c870e15753b0cbf4d1967e4b6d650` 不变；没有修改平台执行源码或沿用历史资格。Codex 真实岗位分析及反馈分别 **304.55 秒、305.04 秒**通过。WorkBuddy 完整岗位分析及反馈分别 **871.38 秒、1067.10 秒**通过；每轮核对 **31 个候选、8 页、31 个操作/Agent配对**，汇总、引用、编译、逐操作对账、历史修订及所属进程清理均通过。整体岗位时长不是单模型阶段时限：每个阶段仍为 **300 秒**，工作流编写仍为 **3600 秒**。本轮没有再观察到岗位空流，但不能据此认定历史 SDK／网关故障永久消失。

以下 **14 项**取得当前环境、模型和编排摘要绑定的新真实资格，未复制旧通过记录：

| 操作组 / Operation group | 当前结果 / Current result |
| --- | --- |
| `plan`、`ground_plan`、`summarize` | 通过；合法计划、范围保护、双语总结及证据不足披露 / Passed plan, scope and summary checks |
| `author_draft` | 通过；隔离创建、持久化及幂等 / Passed isolated authoring, persistence and idempotency |
| `review_free_query_feedback`、`revise_free_query_presentation` | 通过；四条反馈路径、原始结果不变、仅重解释零次 SAP 读取 / Passed feedback paths and immutable, zero-read reinterpretation |
| `compose_workflow`、`review_workflow`、`repair_workflow`、`review_workflow_feedback` | 通过；真实编译、固定绑定、连接修复和反馈保存 / Passed compiler, fixed bindings, repair and persisted feedback |
| `analyze_role_matching`、`review_role_matching_feedback` | 通过；完整目录及业务对账 / Passed complete catalogue and operation reconciliation |
| `review_agent_feedback` | `bounded` 解释及 `trusted_local` 修改均通过；解释无 Diff，修改只产生一个 `/readme` 修订，精确文本与换行一致，执行及规则摘要不变 / Both modes passed with immutable explanation and an exact README-only revision |
| `workflow_authoring.v2` | `trusted_local` 通过；解释不改修订，修改仅涉及双语标题，引用、固定绑定、逐轮 Diff 和历史结果核对通过 / Passed explanation and title-only revision with checked references, bindings and history |

非 SAP 门禁全部通过后，准备一次采购订单 `4500001466` 抬头、三个字段、`top=1` 的有限样本查询。新夹具最初把资格登记产生的管理修订 **119 → 134**误判为执行环境变化，尚未派发模型、未读取 SAP，单独保存为 `not_run`。代码核对确认管理修订及完整配置摘要包含资格状态；夹具因此逐项核对执行身份、模型检查、环境、源码摘要和所需权限模式，而非忽略这些保护。历史记录和执行源码没有改写。

随后唯一一次有限样本验证已执行 **1 次批准的业务读取工具调用**，目录、实时 Schema、查询校验、查询执行、证据读取及 `sap_final_report_validate` 均成功。然而四次原生格式捕获没有产生有效终态：候选先在 `/input_kind`、`/input_field` 违反枚举，后来又缺少这两个必填字段；SDK 返回 `success` 但 `structured_output` 为 `null`，安全错误为 **`workbuddy_structured_output_missing`**，耗时约 **246.62 秒**，不是预算超时。共享契约要求在不需要安全业务引用澄清时明确输出 JSON `null`；这些字段不是采购订单筛选字段。当前证据只证明模型/原生格式捕获未满足该契约，不能确认 SDK 或网关底层缺陷，也不补造缺失字段或从中间候选合成成功终态。

运行继续保留真实的 `inconclusive` 及 `harness_runtime_unavailable`，验证结果为 `failed / verification_report_or_evidence_invalid`，未登记 `free_query` 资格，没有覆盖平台运行状态。失败及安全遥测保存于 `.local-data/workbuddy-validation/resume-20261008-single/limited/`。完整唯一键查询、自动选样、独立基线和验收模式自由查询均暂停，未创建 Campaign、没有自动重跑或扩大读取。这里的 **1 次**指审计中的业务读取工具调用，不代表全部 HTTP／元数据请求数量。

最终保存状态与在线 API 一致：**14/18、can_enable=false、WorkBuddy 停用、Codex 默认、0 活动业务任务、0 待清理 worker**。Codex 配置、依赖、解释器和保护对照保持一致。下一直接阻碍为自由查询原生终态；后三项能力等待其前置门禁，不应表述为三项均已真实运行失败。当前只更新验证资料与忽略目录中的夹具，未重新宣称全量测试、前端或生产构建结果，未重启服务、发送邮件、发布、启用、提交或推送。

This explicitly authorized fresh pass records successful Codex analysis/feedback (**304.55/305.04 seconds**) and WorkBuddy analysis/feedback (**871.38/1067.10 seconds**). Both role rounds cover all **31 candidates across eight pages**, with valid final contracts, checked references and compilation, operation reconciliation, persisted history and confirmed owned cleanup. Individual role model stages remain **300 seconds**; WorkBuddy workflow authoring remains **3600 seconds**. All **14 non-SAP operations**, including both required Agent-feedback modes and trusted-local workflow v2, receive new current-binding qualifications. No historical qualification is copied, and no execution source, pinned installation or protected Codex control changes. The successful role run does not prove that an intermittent upstream fault can never recur.

A fixture-only preflight first confuses qualification-registration revisions with runtime identity and stops before dispatch, preserving a separate zero-model/zero-read `not_run` record. After correcting that check using the actual manager implementation, the single approved limited-sample attempt succeeds in one audited business-read tool call and final-report validation, but native output violates the nullable clarification enums and later omits the two required fields. The SDK returns a success result without `structured_output`; the worker rejects it as **`workbuddy_structured_output_missing`** after approximately **246.62 seconds**, not a deadline failure. A normal non-clarification response must provide explicit JSON nulls. The safe trace proves contract failure, not an identified SDK/gateway defect. Candidates are not promoted to terminals and missing fields are not fabricated.

The persisted run remains honestly inconclusive with its runtime-unavailable diagnostic, separately from failed qualification. Complete-key query, sample discovery and the two acceptance operations are not dispatched; no Campaign is created and no failed job is automatically rerun. The live API and saved audit agree on **14/18**, disabled WorkBuddy, default Codex, unchanged protected controls and no active or pending-cleanup tasks. No automatic enablement, service restart, mail, publication, commit or push occurs. New verification documents do not claim a new full-test or frontend build run.

## 可空终态字段离线修复 2026 10 08 / Offline nullable-terminal repair

本轮先离线核对失败记录、共享提示、输出 Schema 和锁定 CLI 的原生格式器。共享提示原本已经要求在无需安全引用澄清时将 `input_kind`、`input_field` 设为 JSON `null`；平台 JSON Schema 校验接受这两个空值。锁定 CLI 将原 Schema 交给 AJV，未发现转换时删除 `null` 的证据。已保存的失败仅提供枚举、必填问题和缺失终态，没有保存无效字段的原始值，因此不推断模型实际输出了哪个错误字符串，也不将问题认定为已证实的 SDK／网关缺陷。

最小修复仅为 WorkBuddy Harness 增加原生输出指导：说明两个字段用于安全引用澄清，而非 SAP 查询参数；提供显式 JSON 空值示例，禁止省略必填字段，并区分报告工具检查与完整 SDK 终态。仅当冻结 Schema 已声明这两个必填可空枚举时追加指导。共享业务提示、权威 Schema、平台校验器、工具与读取范围不变；不从候选生成终态、不补字段、不重跑失败模型任务。Codex 请求路径不受这段 Provider 专属指导影响。

新增 **21 项**离线用例覆盖空值原样传递、错误字符串及缺失字段诊断、合法安全引用澄清、原生 Schema 编码、缺失终态拒绝，以及相同工具、预算和绑定。结合现有 Harness、共享编排、Runtime Router 与原生输出测试，定向回归 **172 passed、1 个既有 Starlette warning**。完整 Python 回归结果另行记录于本节后续，不以定向通过代替全量结果；本轮无前端源码修改，不重新宣称此前的前端或生产构建结果。

只读审计确认修复前后的 Codex 配置、默认选择、依赖和解释器一致；SDK **0.3.247**、CLI **2.141.0**、**hy4-preview** 及不可变 worker 环境 `7f50527cc86bc14b162caef67332b136444c870e15753b0cbf4d1967e4b6d650` 保持原样。没有新建 release 或原地修改 worker。API 与中英文首页健康，无活动业务或待清理 worker，WorkBuddy 停用、Codex 默认。

现有摘要机制覆盖共享文件及驱动，本轮两个 WorkBuddy 源文件变化后，当前投影为 **0/18、can_enable=false**；历史 **14/18** 原样保留，未删除、重绑或变成十八次失败。本轮不调整摘要粒度。离线修复不能证明真实 `hy4-preview` 已能返回合法终态；需按新冻结绑定重新取得业务验证资格，再继续完整唯一键查询、选样和隔离验收。脱敏基线、测试及只读审计保存在忽略目录 `.local-data/workbuddy-baseline/nullable-20261008/`。本轮零模型任务、零 SAP 调用、零 Campaign；未重启服务、启用、发送邮件、发布、提交或推送。

The existing shared prompt already requires literal JSON nulls outside the secure-reference clarification path. Offline checks confirm that the authoritative Schema accepts them and the pinned native formatter passes the original Schema to AJV; no observed conversion removes null. The saved failure retains enum/required constraints and a missing terminal, not the invalid raw values or an established SDK/gateway root cause.

The narrowly scoped WorkBuddy guidance clarifies field purpose, explicit null encoding, required members and report-versus-terminal validation. It activates only for the existing required nullable enums. Shared business prompts, Schema, checkers, tools, scope, Codex calls and immutable worker remain unchanged. No candidate is promoted to a terminal and no field is fabricated. **Twenty-one** new offline cases cover lossless nulls and clarification values, safe rejection, exact native Schema and mandatory terminal collection; the focused suite passes **172 cases** with one existing warning. Full-suite results are recorded separately rather than inferred; no new frontend or build result is claimed.

A read-only before/after audit confirms unchanged protected Codex/default/dependency/interpreter controls and the same SDK **0.3.247**, CLI **2.141.0**, model and worker release. Healthy local services have no active business or pending-cleanup worker. The existing broad source-digest rule now projects **0/18 pending revalidation** while retaining historical **14/18** records unchanged. This is not eighteen failed executions or deleted history. Real model effectiveness and current-binding qualifications remain to be established; no live retry, SAP read, Campaign, release replacement, service restart, enablement, email, publication, commit or push occurs in this offline pass.

本轮最终全量 Python 为 **1660 passed、1 个既有 skipped、1 个既有 Starlette warning**，耗时 **1083.37 秒**，报告为 `nullable-20261008/full.xml`。正式目录 **34 包／6 模块**、文档索引 **55 项**及两份 WorkBuddy 文档本地链接检查通过；后者只调整历史错误链接示例的引用写法，保留其原失败结论。最后再次执行只读状态审计，未更新任何真实资格。修复及离线兼容门禁通过，模型效果和启用门禁仍待新的真实验证；两者分开报告。

The final full Python suite passes **1660 cases**, with **one existing skip and one existing Starlette warning**, in **1083.37 seconds** (`nullable-20261008/full.xml`). The **34-package/six-module** catalogue, **55-document** index and both WorkBuddy documents' local links pass. A historical broken-link example is quoted without being treated as actual navigation; its original failure conclusion is retained. A final read-only audit changes no real qualification. Offline compatibility passes while live effectiveness and enablement remain pending, reported separately.

## 新绑定真实验证 2026 10 09 / Fresh-binding live verification

用户继续授权后，本轮使用新的 `.local-data/workbuddy-baseline/resume-20261009-nullable/`，保存脱敏基线并核对无活动业务或待清理 worker，随后受控重启加载输出指导修复。先校验可用静态构建再停止服务，复用既有构建，没有重新构建前台。锁定 SDK **0.3.247**、CLI **2.141.0**、**hy4-preview**、worker release 及依赖不变。认证 **4.25 秒**通过；无工具模型探针 **35.64 秒**通过，实际模型身份确认后冻结新源码和环境绑定。

一次新的真实 Codex 岗位分析／反馈对照分别 **245.16 秒、238.69 秒**通过；两轮均完整覆盖 **31 个候选、8 页、31 个操作／Agent配对**，引用、工作流编译、单操作对账、持久化及历史修订检查通过。未修改 Codex 模型、推理、权限、配置或原有预算。

随后的唯一一次 WorkBuddy 岗位分析在 **654.43 秒**后形成可查看但不完整的业务汇总：**10 个有效原生结构化终态、31 个候选、8 页**均通过，没有空流、缺失终态、超时或分页遗漏。失败为 **`role_business_validation_incomplete`**，具体问题 **`role_matching_combination_unsupported`**；运行会话的 `completed` 不等于资格通过。

保存修订的只读离线重放精确复现对账结果，原数据库摘要不变。模型生成两个通过编译器的建议：一个只用已完整覆盖操作的 `mm-po-gr-status`，另一个追加 `supplier-performance-risk`。后者在本轮分页记录中是 **partial／low**；当前建议支持规则要求 **full 或 partial、medium 或 high、可执行且 PASS**，所以仅有 `mm-po-gr-status` 在支持集合内。合法单 Agent 建议被接受，追加低置信度 Agent 的建议仍须拒绝，不能把阶段中声明的 `confidence=high` 当成已核对的分页置信度。

这不是此前的“合法单 Agent 被强制要求两个 Agent”误判。门禁拒绝正确，但共享末轮提示只明示 full／partial 和可执行 PASS，**没有明确说明分页匹配至少 medium 的门槛**。这是可核对的通用提示与检查不一致；本轮不能证明它是模型输出偏离的唯一原因，也不能据此推断 SDK／网关故障。建议下一步从同一权威判定生成按操作的建议支持集合、拒绝原因和提示，明确阶段置信度不能升级前序匹配，保留业务门禁，不删除不合格建议来追求资格。

按“一次失败停止依赖项”处理：WorkBuddy 岗位反馈、其余非 SAP 操作、有界查询、选样和隔离验收均未派发，**本轮零 SAP 调用、零 Campaign**。昨天可空字段指导的真实 SAP 查询效果仍未验证，不能宣称已经解决。本轮不修改平台执行源码、worker 或前端，不重新宣称全量测试及生产构建。当前矩阵仍 **0/18、can_enable=false**，历史 **14/18** 保留；最终 API 与保存状态一致，Codex 保护对照不变，无活动业务或待清理 worker，API 与中英文首页健康。WorkBuddy 保持停用、Codex 默认，未发送邮件、发布、启用、提交或推送。

Safe reports are retained in a new independent directory for **2026-10-09**, not copied or rebound from historical qualifications. An idle check precedes a controlled restart that validates and reuses the available static build. Authentication and the no-tools model probe pass in **4.25/35.64 seconds**, with the same pinned SDK, CLI, concrete model, immutable worker and dependencies. New Codex analysis/feedback pass in **245.16/238.69 seconds** with complete catalogue coverage, valid references and compilation, operation reconciliation and preserved revision history.

The one WorkBuddy analysis takes **654.43 seconds** and has **ten valid native terminals, all 31 candidates and eight pages**, but fails business consolidation. A compiled single-Agent suggestion is valid; a second proposal adds executable PASS `supplier-performance-risk`, whose authoritative page match is **partial with low confidence**. The existing support gate requires at least medium confidence and correctly rejects this extra stage. Native formatting, empty streams and timeouts are not the observed failure. The final shared prompt omits that explicit confidence threshold, exposing a general prompt/check inconsistency rather than proving an SDK fault or the sole causal explanation. A saved-only replay reproduces the rejection without changing the database or qualifying the failed result. Future repair should derive per-operation support and guidance from the same rule, not relax confidence, overwrite page records or discard failures to obtain PASS.

All dependent non-SAP and SAP stages stop before dispatch; no SAP read or Campaign occurs. The nullable-guidance effectiveness on live SAP queries remains untested. This pass changes no execution source or immutable release and claims no fresh full-test/frontend build result. Final saved/live projections agree on **0/18 current qualification, can_enable=false**, preserved historical **14/18**, disabled WorkBuddy, default Codex, healthy services and no active/pending-cleanup jobs. No enablement, publication, mail, commit or push follows.

失败后的离线对账、诊断、原生 Schema 和可空字段回归为 **63 passed、1 个既有 Starlette warning**，耗时 **17.32 秒**；两份 WorkBuddy 文档链接及补丁格式检查通过。原失败不重分类、不授予资格。这是定向离线验证，不代替新真实业务通过记录或昨日全量回归。

Post-failure targeted offline reconciliation, diagnostics, native Schema and nullable-envelope tests pass **63 cases** with one existing Starlette warning in **17.32 seconds**. WorkBuddy document links and patch formatting pass. The original failure is not reclassified or qualified; this targeted replay is neither live business qualification nor a new full-suite result.

## 候选支持与提示统一 2026 10 09 / Unified candidate support and guidance

本轮在共享业务层完成 `sapba.role-candidate-support/1`：冻结目录提供执行资格和 PASS，已核对分页记录提供匹配覆盖与置信度。末轮提示接收每个操作的可用步骤 Agent、可完整独立覆盖 Agent 和安全拒绝原因；生成后的对账使用同一候选判定。匹配必须 full/partial、medium/high、可执行且 PASS，步骤自行填写 high 不能提升低置信度匹配，也不能把不适用候选作为“可选背景”步骤加入。原平台目录能力校正同时在生成前和保存前使用，未新增或放宽这些校正。

所有分页匹配、包括低置信度候选仍保留供解释和审计。覆盖与可执行性保持区分；多个声明操作逐一核对，单 Agent 仍须完整覆盖，原端口、固定绑定、引用与工作流编译门禁不变。保存失败案例的只读重放得到完全相同的原对账：`mm-po-gr-status` 可用，低置信度 `supplier-performance-risk` 不可作为本次操作的步骤。原数据库摘要与历史结论保持不变，没有丢弃错误建议、重分类或授予资格。

新增 **20 项**离线测试覆盖同一规则的生成／检查一致性、未知资格的拒绝、模型伪造 PASS、操作间越界、部分覆盖与单 Agent 区别、现有能力校正和两种 Provider 的相同提示。定向 **135 passed**（**100.28 秒**）；完整 Python **1680 passed、1 个既有 skipped、1 个既有 Starlette warning**（**931.79 秒**）。原 Codex 对照中的材料理解、分页、请求选项、权限、模型及推理参数保持一致，末轮业务提示是本轮明确修改；权威输出 Schema 和其他操作的对照未放宽。前端 **119 项**通过，Astro **50 个文件零错误／零警告／零提示**，隔离生产目录生成 **82 页**；正式目录 **34 包／6 模块**、文档索引 **55 项**及 WorkBuddy 文档本地链接检查通过。前端源码未在本轮修改，现有站点制品未覆盖或切换。

脱敏基线、保存案例回放和测试报告在 `.local-data/workbuddy-baseline/candidate-support-20261009/`。Codex 配置、依赖、解释器、默认选择、安装 release、worker 摘要及 WorkBuddy 保存状态保持一致。共享提示变化形成新编排摘要；当前 **0/18** 仍需新绑定真实验证，不复制历史 **14/18**。SDK **0.3.247**、CLI **2.141.0**、**hy4-preview** 和不可变 worker 环境不变，无需新安装 release。本轮零真实模型任务、零 SAP 调用、零 Campaign，未重启服务、启用、发布、提交或推送。

下一步是受控加载新编排后执行新的真实 Codex 岗位分析／反馈对照，再依门禁验证 WorkBuddy 岗位和后续操作。离线规则修复通过不证明模型一定遵守提示，不证明此前可空终态修复的真实 SAP 效果，也不解除启用门禁。

The shared projection and final validator now use one predicate from frozen catalogue eligibility and checked page matches. Per-operation eligible stages, full single-Agent candidates and safe rejection reasons are supplied before generation. A stage's high self-rating cannot promote a low-confidence match or justify an optional context stage. Existing catalogue corrections are shared unchanged; audit records, distinct coverage/executability, compiler, references and bindings remain authoritative. Read-only replay preserves the exact historical rejection and database digest without qualification.

**Twenty** new offline cases exercise predicate parity, forged/missing eligibility, cross-operation scope, partial/full coverage, existing normalization and both Providers' guidance. Focused tests pass **135 cases in 100.28 seconds**; the full suite passes **1680**, with **one existing skip and one existing Starlette warning**, in **931.79 seconds**. Codex's understanding/page traces and native options remain protected; final business guidance is the intentional change, not a Schema or validation relaxation. **119** frontend tests pass, Astro reports no issues across **50 files**, and an isolated production build generates **82 pages** without overwriting the active build. Catalogue, documentation index and local links also pass.

Protected configuration, dependencies, interpreter, default selection, installed release/worker and saved WorkBuddy state remain unchanged. New source binding requires fresh live qualification; **0/18 current** and historical **14/18** are not rebound. No worker replacement, live model/SAP task, Campaign, restart, enablement, publication, commit or push occurs. Fresh Codex comparison and gated WorkBuddy validation remain next; offline correctness is not proof of model compliance or release readiness.

## 候选支持修复后的真实验证 2026 10 09 / Live validation after candidate-support repair

用户继续授权后，本轮使用新的 `.local-data/workbuddy-baseline/resume-20261009-support/` 保存基线和冻结报告。确认无活动业务及待清理 worker 后受控重启，先校验并复用可用静态构建，再停止旧服务；本轮不重新构建前台。认证 **9.72 秒**、无工具模型探针 **24.90 秒**通过。SDK **0.3.247**、CLI **2.141.0**、**hy4-preview** 和不可变环境 `7f50527cc86bc14b162caef67332b136444c870e15753b0cbf4d1967e4b6d650` 保持不变，未复制历史资格。

新的真实 Codex 岗位分析／反馈分别 **1023.89 秒、2793.21 秒**通过，均完整核对 **31 个候选、8 页、31 个操作／Agent 配对**，引用、编译、业务对账、保存修订及首轮不可变性通过。反馈偏慢的底层原因未确认；不能从粗粒度 `understanding` 会话阶段推断具体阻塞。每个模型阶段仍是原 **300 秒**，没有把单次模型时限改为整轮耗时。已绑定首轮线程的安全事件确认反馈材料理解调用有完成终态，但不暴露原生推理或将其当作整轮完成。

一次 WorkBuddy 岗位分析／反馈分别 **706.18 秒、695.08 秒**通过现有检查，两轮共 **20 个有效原生终态**，完整目录、引用、业务对账和修订检查通过，无原生空流、缺失终态或分页失败。首轮完整匹配可执行 PASS `mm-po-gr-status`；反馈保留相同操作，但将“差异上报与跟催路径未定义端口”列为未覆盖项，匹配置信度变为 low，末轮记录明确能力缺口而非不合格工作流建议。`coverage=full` 标签附带未覆盖项不构成平台定义的完整覆盖。该缺口通过现有对账，登记两项操作资格，但**不证明模型两轮业务判断一致**；上报／跟催说明是否应作为原用户需求的必需能力仍需独立核对，不改写保存结果追求一致。

后续计划族第一次在夹具导入阶段报 `ModuleNotFoundError: live_guard`，未调用模型。原 `sequence-report.json` 保留。仅修复忽略目录内的当前绑定监控和加载入口，原始夹具源码、平台执行摘要和 worker 不变；**七组离线导入检查、四项绑定／操作／模式／目录越权拒绝检查**通过，均零模型／SAP 调用且不授予资格。新的独立续接记录只首次派发未执行的操作，不重复岗位任务，也不重传失败模型请求。

计划 `plan` 在 **122.80 秒**、独立 `summarize` 在 **122.58 秒**后均保存 **`workbuddy_deadline_exceeded`**。请求元数据确认各自既定预算 **120000 ms**，输入字节数 **4965／1468**及 Schema 摘要有记录。没有有效终态或明确原生错误诊断，不能猜测为之前的 `EmptyStreamError`、确定的 Schema 错误、输入过大或 SAP 环境问题；握手、SDK 初始化与模型等待的实际阻塞位置不能由现有记录确认。两个作业均为 `timed_out`，清理成功，原始超时未被文件清理异常覆盖。计划失败后 `ground_plan` 未派发；已启动的独立汇总完成收尾后，整个族停止，随后编写、自由查询反馈、工作流、草稿解释／修改、工作流 v2 均未派发。不得将这些未验证操作描述为全部执行失败。

最终独立审计核对全部当前摘要、模型／环境身份、资格模式、Codex 配置／依赖／解释器／默认选择和受保护源码。保存状态与运行中 API 一致：**2/18、can_enable=false、WorkBuddy 停用、Codex 默认**，无活动业务、运行 worker 或待清理进程，API、中英文首页均 HTTP 200。受控重启后的 API 在本轮验证期间保持运行，没有再次重启。SAP 查询、选样及隔离验收均未派发，**零 SAP 调用、零 Campaign**；可空终态修复的真实 SAP 效果仍不能宣称通过。历史资格和失败不重分类；本轮不升级环境、不启用、发布、发送邮件、提交或推送。只更新验证夹具和双语记录，不宣称重新执行上一节的全量测试与构建。

Fresh safe evidence is retained in the new ignored pass directory. An idle-controlled restart validates and reuses the static build before stopping old services. Authentication and the no-tools model probe pass in **9.72/24.90 seconds** without changing the pinned SDK, CLI, concrete model or immutable release. The new Codex analysis/feedback pass in **1023.89/2793.21 seconds**, with complete catalogue, references, compilation, reconciliation and immutable revision checks. The longer latency has no confirmed underlying explanation; the original **300-second per-model-stage** budget is unchanged.

WorkBuddy analysis/feedback take **706.18/695.08 seconds**, with **twenty valid native terminals**, all 31 candidates/eight pages and valid persisted checks. Initial single-Agent coverage succeeds; feedback explicitly reports an escalation/follow-up capability gap from low-confidence matching. The current reconciliation accepts that cited gap, not identical semantic conclusions. A full-coverage label with nonempty uncovered capabilities is not platform-full coverage; the saved result is neither rewritten nor treated as a universally correct match.

The following import-only fixture failure occurs before model dispatch and is preserved. Seven fixture imports and four negative guard probes pass offline. A task-local current-binding monitor fixes loading without changing locked source, worker, runtime binding or qualification rules; only never-dispatched operations continue in a new record. Plan and the independent summary then time out after **122.80/122.58 seconds**, on their existing **120-second** budgets. Input lengths and Schema digests are saved, but neither a valid terminal nor a definitive native error is available. Startup, handshake and model-wait locations remain unconfirmed; these are not evidence of another empty-stream, Schema, SAP or prompt-size failure. Both owned jobs clean up successfully. Dependent grounding and every subsequent family/SAP stage stop before dispatch, without model retry or budget extension.

The final independent audit confirms **2/18, can_enable=false**, disabled WorkBuddy, default Codex, identical protected/frozen controls, healthy API/bilingual pages and no active or cleanup-pending jobs. There are **zero SAP reads and zero Campaigns**. Historical results stay intact, real-query nullable guidance remains untested, and no new full-suite/build, upgrade, enablement, publication, mail, commit or push is claimed.
