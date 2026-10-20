# WorkBuddy 草稿认证复发修复记录

日期：2026-10-19。目标草稿：`agent_draft_b947ebc1029ae8f81b28512c`。

## 本次失败与上一轮成功的区别

上一轮确实保存了修订 2，但它只证明那一次调用成功，未覆盖后续认证缓存变化后的初始化。

本次失败是新的第 8 条存储回合（页面第 7 轮）：在约 6.4 秒时返回 `runtime_authentication_check_required`，没有进入草稿修改。其实际认证原因是 `initialize_account_absent` / `workbuddy_authentication_confirmation_required`。账号修订仍为 8，站点、模型、worker 和已确认身份未变；变化的是官方缓存的文件元信息。

没有取得确定的 401 或退出证据。同环境的无提示诊断随后连续四次返回同一原账号，不能把先前的账号缺失解释成确定退出。锁定 CLI 的 initialize handler 读取 `currentSessionSubject` 的瞬时值，而认证恢复另有异步完成状态；平台此前只取一次初始化结果，缺少有界的就绪与稳定性确认。SDK 没有提供当时缺少账号的完整底层诊断，因此不把所有此类情况都归为同一个供应商错误。

## 已上线的修复

- 仅 WorkBuddy 只读认证检查增加同一 CLI 会话内最多四次初始化观察。沿用原 30 秒检查预算及最多 10 秒清理，不发送模型提示、不启动 OAuth、不自动登录或换账号。
- 完整官方账号响应前后都核对缓存观察。缓存或运行上下文变化时，不用旧响应证明新缓存；必须取得稳定的完整响应。辅助进程关闭后，父进程和认证服务再次核对观察。既有“外部并发修改缓存不能放行”测试保持成立。
- 持续缺少账号、401、续期失败、上下文变化或清理不确定仍阻断。原身份和历史成功记录保留，不以历史登录替代本轮检查。
- `workbuddy_authentication_confirmation_required` 不再被该路径替换成笼统的认证错误。
- 页面明确区分“恢复草稿原账号引用”和“本轮认证已通过”；认证失败对话框显示“返回核对认证”，不从该按钮准备盲目重试。

这些是平台宿主侧修复。SDK 0.3.247、CLI 2.141.0、当前 immutable worker、国内站及 `deepseek-v4.1-flash` 未改变。

## 验证和真实前台结果

- 380 项相关 Python 回归通过，包含初始化延迟、持续缺号、缓存竞态、同账号续期、账号变化、认证失败、进程清理和 Codex 对照。
- 56 项前端测试通过；Astro 54 个文件无错误、警告或提示；34 个 Manifest、32 条目录记录及隔离 84 页生产构建通过。
- 候选的一次官方检查在并发构建期间超时，记录保留、清理完成，未登记成功。构建结束后的带计时定向检查在约 17.6 秒内确认原账号及稳定观察，没有写正式认证状态。
- 维护锁下确认无活动任务或旧写入者后，应用冻结源码及经审核的 SDK 基础设施认证。API 源码绑定验活通过；前台独立切换保留新 API PID 25104，旧构建保留。
- 真正从草稿页面点击“重新核对 WorkBuddy 认证”，成功确认原账号，认证修订仍为 8，未自动提交修改。
- 随后从页面仅重试本次失败的原话：“参照已有固定Agent的技术ID命名，给这个草稿生成一个技术ID”。新回合 9、作业 `agent_op_ada81d003ee04806a54d` 在 **150.2 秒**完成，取得原生结构化终态、实际模型身份一致、平台校验通过并完成清理。原失败回合保留。
- 助手返回建议 `supplier-historical-open-items`，没有绕过技术身份保护；内容修订仍为 2，技术 ID 尚未确认，没有发布。
- 本次 SDK 调用后，官方缓存元信息再次变化。另做一次**无模型、无 SAP**的后续认证检查，约 9.4 秒返回 authenticated、current，修订仍为 8。这覆盖了“成功一次后下一轮又需核对”的实际续接情形，而不是只检查调用前状态。

## 保留与边界

配置、实际默认选择、Codex 账号元数据、模型检查、业务质量记录及历史 PASS 摘要保持相同。仅追加本次基础设施接入认证，不将离线回归或认证检查登记成新的业务 PASS。

本轮只重试了一次上述原请求，没有重跑 15 项业务验证、SAP 业务查询或 Campaign；没有自动登录、退出、升级 SDK／CLI／worker、确认技术 ID、启停 Runtime、切换默认、发布、提交或推送。

本次复测证明已上线修复在当前绑定下通过；不保证 SDK、认证服务器或模型未来永不出现独立故障。真正的未登录、401、持续账号缺失和清理失败仍会安全阻断。可信本地模式仍不是操作系统级沙箱。

## 本地证据

`.local-data/draft-auth-followup-20261019/`：

- `initial-inspection.json`、`confirmed-inspection.json`：原失败安全元数据。
- `initialize-observations.json`：同版本 CLI 无提示诊断，仅保存形状、身份摘要和时序。
- `candidate-official-check.json`、`candidate-official-check-idle.json`：失败及成功的候选认证检查。
- `offline-tests.xml`、`test-input.json`、`test-outcome.json`：380 项源码绑定回归。
- `freeze.json`、`source-manifest.json`、`activation.json`、`api-startup-binding.json`：冻结和受控上线。
- `final-receipt.json`、`ui-progress-04.json`、`frontend-success.png`：相同请求的真实前台成功、结构化终态与清理。
- `preservation-after-frontend.json`、`preservation-closure.json`、`post-run-authentication.json`：配置、身份、历史记录及调用后认证续接核对。

没有复制认证缓存、凭据、原始推理或受限 SAP 原始行。
