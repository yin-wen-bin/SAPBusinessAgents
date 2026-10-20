# 模型兼容结果复用 / Model compatibility result reuse

## 边界 / Boundaries

SDK 接入认证、模型兼容和业务质量评测仍是三层独立依据。业务评测数量不参与模型选择。
此修复不修改 Codex 的认证、适用性判断、探针参数、预算、旧客户端保存时的自动检查或历史存储。
WorkBuddy 的 worker、模型协议及90秒探针预算不变。GET不发起检查、不写承接记录。

SDK integration, model compatibility and business quality remain separate.
Codex admission and legacy save-time automatic checks are unchanged. Viewing a
model never signs in, probes, changes defaults, or runs business evaluations.

## WorkBuddy 同账号登录 / Same-account sign-in

只有普通登录，且登录前后均有明确的同账号官方认证、站点/来源/执行上下文一致、
无失效或退出事件、凭据观察被确认、清理完成时，才保留认证修订和原模型检查。
凭据续期不等于换账号。身份未知、换站点、退出、切换账号、401失效及清理未确认
不能通过这个快捷判断恢复授权。Codex继续采用原认证事件处理。

Only an ordinary login with positively matching official identities, site,
source and execution context can retain the revision. Revocation, account/site
changes, unknown identity and unconfirmed cleanup never take this path.

## 只读展示接口 / Read-only projection

现有模型列表兼容增加 `compatibility_applicability`：
`status`、安全 `reason`、`check_contract`、`checked_at`。旧字段与其准入语义不变。
新展示字段不进入 WorkBuddy 原目录摘要，也不能授予执行权限。

| status | 中文 / English |
|---|---|
| reusable | 有效结果可复用 / Applicable result reusable |
| authentication_required | 认证待核对，不代表模型失败 / Authentication needs review |
| recovery_pending | 历史结果待审核承接 / Historical binding needs audited recovery |
| check_required | 缺少适用结果 / Applicable check required |
| failed | 最近兼容检查失败 / Latest compatibility check failed |
| blocked | 安装或执行前置条件受阻 / Runtime prerequisites blocked |

`recovery_pending`只是提示有原始记录可供审计，不能表示承接已通过。
前端保存前GET复核选择和绑定，变化时拒绝写入；后端仍执行完整检查。
Codex旧保存接口保留自动探针，所以并发绑定变化下不能承诺旧入口永不探测。
新前台推理强度也先检查后保存，原请求正文不变。

The UI performs a read-only preflight before saving. This is not authorization:
the backend remains authoritative. Codex legacy APIs can still check on save.
Unknown check outcomes are followed by GET only; no automatic redispatch.
Codex's existing API cannot always identify an uncertain original check, so the
UI retains uncertainty rather than assuming a later compatible record is its result.

## 历史承接审计 / Historical recovery audit

私有入口 `scripts/audit-workbuddy-model-reuse.py --workspace <repo> --output <new-isolated-dir>`。
以候选源码的 `src` 作为 PYTHONPATH，从仓库虚拟环境运行；入口没有正式应用选项。

它只读正式状态/认证元数据，校验原始真实探针及身份制品，在新隔离目录生成：

- 受保护配置前后摘要与安全审计报告。
- 直接引用原始PASS的承接建议，不串联派生PASS。
- 隔离证据及状态副本，使用原凭据校验器演练。
- 源码变化清单及正式应用前需要重核对的条件。

不足的证据保持blocked，不补造身份、不自动调用模型。不要把隔离状态副本
覆盖正式状态；提案的证据路径属于隔离目录，不能直接安装为正式凭据。

The audit does not instantiate SDKs or managers, read authentication-cache
contents, apply production receipts, or call models/SAP. Its rehearsal is not
a new live PASS. Missing evidence stays blocked.

## 后续上线 / Subsequent controlled rollout

本轮只交付候选。`runtime_authentication.py`属于执行基础设施依赖，因此正式上线前
须审核目标基础设施认证；模型协议未变，不需要靠重跑15项业务验证补偿。
维护时重新冻结代码与状态，核对相关任务/写入者/清理，补齐目标认证，再成套切换。
历史承接须独立维护授权、重新核对当前身份和绑定、重新归档证据并追加记录。
不回退认证修订、不改写旧PASS、不自动切换模型或Runtime。

Deploy only after target infrastructure certification and maintenance checks.
Apply any historical receipt separately against the then-current binding.
Rollback must preserve appended evidence and never downgrade the history.
