# WorkBuddy 资格、历史与增量验证 / Qualification and incremental validation

## 案例协议与历史边界 / Case protocol and historical scope

共用方法与新协议以 [AI Runtime 验证](runtime-validation.md) 为准。协议 2 提供逐案例汇总、原始检查制品、冻结进程绑定及有限承接。本机后续授权中已受控激活；其他安装须核对正式状态，不自动迁移。旧 PASS 不改写。以下内容记录协议 1 的实际行为，不应套用于协议 2 的尝试级失败处理。

The shared validation guide is authoritative for the case protocol. Activation is controlled per installation and must be checked in saved state. Original qualification records and Codex admission remain unchanged. The sections below describe protocol 1.

资格协议为 `sapba.workbuddy.qualification/1`。此机制仅管理 WorkBuddy；Codex 的原摘要、配置、SDK、请求、时限和历史绑定不变。完整安装清单仍用于不可变环境校验，不能删除文件、改写安装摘要或用显示缓存绕过真实派发门禁。

The qualification ledger applies only to WorkBuddy. Codex's legacy fingerprints and execution stay unchanged. Full immutable-release inventories remain mandatory; display caches never grant execution rights.

## 模型与操作绑定 / Model and operation binding

- 按请求模型、已确认实际身份、站点及认证修订隔离资格。兼容探针不是业务资格；不同模型不共享 PASS。
- 每个操作、权限模式分别保存。草稿反馈要求 `bounded` 和 `trusted_local`，工作流 v2 要求 `trusted_local`。单个模式通过不代表全部模式通过。
- 保存为追加式 `qualification_records`。真实通过、审计复用及失败分别记录；旧 `capabilities` 不重写。新失败或未完成的验证阻止回退旧 PASS。
- 同身份检查刷新不清空业务资格。续接的旧检查摘要必须能追溯到保存的原检查及其作业，任意 stale 摘要仍被拒绝。

Records are scoped to requested/reported model, site, authentication revision, operation and permission mode. A failed or unfinished newer attempt blocks fallback. Check refreshes preserve business qualification only when the original check and owned job remain provable.

## 依赖与环境 / Dependencies and environments

`workbuddy_qualification.py` 定义版本化操作根及组件传递依赖。选样专属文件变化不影响无依赖的岗位操作；公共授权、协议、终态和清理组件变化影响所有使用者。按整个模块计算摘要，不以 AST 截取、模型判断或“仅文案修改”忽略业务代码。混合公共文件仍采用保守失效；新增运行依赖必须纳入注册清单并测试闭包，不宣称已自动推导任意 Python 调用关系。

环境完整性与执行身份分开：实际 Python、SDK、CLI、依赖、入口及 worker 文件仍参与执行身份。仅 CPython 已知 `pyvenv.cfg` 的安装历史 `command` 项作规范化；其 home、executable、system-site-packages、版本及其他执行配置不能忽略，未知配置拒绝等价判定。完整清单仍校验该文件原始字节。

安装目录或生成元数据不同不自动使业务结果失效，也不自动授予资格。跨 release 复用要求执行组件完全匹配、模型身份明确、站点/认证/权限/业务依赖一致，并具有目标环境完成认证探针的启动、握手及清理制品。刷新认证不会发模型调用。模型检查复用还要求原模型探针作业可核对；业务复用要求原真实作业、平台验证和审计来源完整。不能用同版本号、离线测试或一个简单模型探针替代业务验证。

The component graph is explicit and conservative, not an automatic arbitrary-call-graph inference. All relevant executable bytes remain fingerprinted. Only the known venv installation-history command is normalized; full inventory verification is unchanged. Cross-release reuse needs identical execution and business bindings, proven source jobs and a completed target startup/authentication/cleanup receipt. Missing evidence fails closed.

## 离线查看与显式迁移 / Offline inspection and explicit migration

使用平台 Python；脚本不安装环境、不登录、不调用模型、不访问 SAP：

```powershell
cd D:\SAPBusinessAgents
.\.venv\Scripts\python.exe -B scripts\inspect-workbuddy-qualification.py --model deepseek-v4.1-flash
```

输出每个操作及权限模式的 `retain`、`auditable_reuse` 或 `revalidate`，以及原因与变化文件。15 项操作共包含16个必需权限案例，不能用案例数量冒充操作通过数。

以下是显式管理动作，不会自动运行：

- `--migrate-history`：追加历史索引，不授予资格、不改写旧 PASS。
- `--legacy-snapshot <path>`：结合迁移使用精确保存的 Runtime 快照。只有其摘要与原作业匹配才能归属模型，不以当前默认值推断；旧依赖依据缺失时仍只能作历史。
- `--reuse-model-check`：追加满足全部依据的模型检查复用凭据；不发送新探针。
- `--reuse-record <record-id>`：追加满足全部条件的业务复用记录。拒绝不明确绑定、跨模型、更新失败和未确认清理，不自动扩大验证范围。
- `--output <path>`：仅允许在 `.local-data/workbuddy-qualification-reports/` 写报告，不允许覆盖管理状态或安装文件。

默认查询不修改本地状态。迁移可重复执行，历史索引与原始制品保留。复用追溯原始真实 PASS，不无限串联派生记录。任务关闭、超时、配置变化或过期结果不能生成新资格。

Default inspection is read-only. Explicit migration appends history only. Exact original Runtime snapshots may attribute historical models but cannot reconstruct absent dependency proof. Reuse receipts are independently auditable and never relabel a historical PASS as a new live run.

## 已知边界 / Limits

- 首次迁移缺少可靠模型、原依赖或作业制品的记录仍须核查，不能承诺恢复原计数。
- 历史记录跨环境累计数、当前环境旧格式操作数和当前模型有效操作数分别显示，不混加。
- Runtime 操作资格不是本次 SAP 数据正确性证明，也不能替代固定 Agent 三级验收或来源锚点检查。
- 认证沿用现有平台站点和认证修订规则，模型未知身份不用于跨环境复用。可信本地模式仍非操作系统沙盒。
- 本轮离线实现不自动切换正在运行的 API，不创建环境、发起真实验证、启用 Provider、发布、提交或推送。

Qualification is not fresh SAP evidence or fixed-Agent acceptance. Historical results lacking trustworthy bindings stay historical. Trusted local mode is not an OS sandbox. Real validation and live service rollout are separate controlled actions.
