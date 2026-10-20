# 共享报告契约与有界纠错

本候选同时适用于 Codex 与 WorkBuddy。业务模型、报告工具、纠错反馈与收敛状态机只有一套；SDK 驱动仅转换原生事件、工具错误标志、终态交接和清理协议。

## 契约来源及交接

`RunPresentation` 与现有业务检查器是权威来源。共享构造器从业务模型生成工具 Schema 和严格终态 Schema：前者保留已声明的合法默认值，后者要求输出完整字段；非法结构不自动补值或转换。

工具入参是 `{"report": <presentation>}`，不是完整终态。成功时返回 `validated_report` 和有效 `validation_ref`，用返回的规范报告作为完整终态中的 `presentation`；失败时凭据为 null，保留字段路径、期望约束、实际类型及安全纠错说明。诊断不包含输入值、SAP 原始行或推理内容。

普通查询不注册验收投影；验收任务按平台冻结的具体契约注册。工具目录、授权摘要、提示和后台检查绑定同一版本、任务、Runtime及请求摘要。

WorkBuddy 通过现有 payload 获得工具定义；Codex MCP 通过作业目录内的契约文件和摘要加载同一工具定义。缺失、篡改、变更或迟到绑定不能回退到宽泛 Schema。锁定 WorkBuddy SDK 的工具包装仅保留外层 properties/required；嵌套报告 Schema 完整保留，后台仍检查外层额外参数，不把 SDK 的包装差异当成授权。

## 收敛策略

报告校验按作业串行，幂等重传不计新尝试，同编号换正文冲突。首次失败计为1次；连续3次没有权威检查结果的实质改善则停止所属作业，保存证据和安全诊断。修复必要约束或减少同类错误计为进展；纯文字、键顺序、无关字段、请求编号及无关证据变化不计。

记录历史最好检查结果，避免 A/B 错误交替或反复退步无限重置。验收已有的两次相同、未变失败门禁保留，用相同状态机按任务策略执行，不按 Provider 分叉。

该策略不限制正常调查、编辑或测试，不新增全局SDK轮次限制，不重置或延长时间预算，不因结构错误自动重读 SAP。停止后沿用现有最多10秒所属进程清理；失败和清理结果分别保存，未确认清理时阻断后续操作，迟到结果不可应用。

## Codex 预期变化及必要差异

- Codex 与 WorkBuddy 同时获得展开的工具契约、交接说明和精确纠错反馈。
- Codex 现在也会在报告持续不收敛时提前失败；模型、推理强度、权限、SDK依赖及预算不改变。
- Codex 保留已验证报告的提前收尾路径，清理确认后才能返回。
- WorkBuddy 必须取得有效 `StructuredOutput` 原生终态；候选或报告工具成功不能替代终态。
- 两种传输均以 `isError=true` 表达工具失败；共享业务正文相同。

## 验证、资格与上线

测试使用合成数据、模拟SDK和受哈希白名单控制的本地协议测试worker，不调用真实模型或SAP。离线通过不登记业务PASS。旧PASS、原作业和历史失败不改写；新增职责组件进入真实依赖清单，不修改摘要算法。

目标源码依赖、SDK基础设施及模型协议的变化应分别审计。本候选改变报告协议及worker错误标志，不能用“仅日志变化”的承接规则直接复用资格。后续若上线，须准备同SDK／CLI版本的新不可变worker release，再审核目标接入认证及兼容证据；不能原地修改旧环境或重跑15项业务验证来掩盖绑定问题。

本轮仅交付候选及离线证据，不安装新worker、不应用资格承接、不重启服务。真实模型轻量检查、原查询重试及生产上线均不在本轮执行范围内。

Shared authority, repair feedback and bounded convergence apply to both providers. Only native transport, accepted terminal handoff and owned cleanup differ. Offline tests are not live model/SAP qualification, and historical PASS records remain immutable.
