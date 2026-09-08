# AR催收与银行来款核对边界

[应收催收](../agents/FI/ar-collection/README.md)与[银行来款核对](../agents/FI/ar-cash-application/README.md)采用两个独立的严格只读职责。当前版本和生命周期见各自生成的状态区，不从历史候选报告推断当前可用性。

- `ar-collection`按客户批量重建应收账龄、催收冻结和催收状态，并生成异常优先的工作清单。它不读取银行来款。
- `ar-cash-application`核对银行来款、客户子分类账、清账凭证和发票关系。它不执行清账、过账或自动销账。

FI清账只证明SAP应收项目的清账关系，不能替代独立银行到账证据。银行付款人名称和原始参考号只进入Windows用户绑定的加密受限制品；公开结果只保留业务键、金额、币种、状态、掩码账户和带域的HMAC。

应收催收现有生产功能包含逐凭证原因、建议动作、优先级和CSV待办；未到期项目仅监控，不计入催收待办。银行来款核对已有验收记录，不再按历史候选说明标为尚未验收。Skill仍是独立前置门禁，不作为Agent三级比较中的第四方。本轮说明更新不重新进行SAP验收。

历史催收路径使用已验证的Skill重建截止日前已执行的催收事件；当前客户催收主数据不能冒充历史快照。平台独立解析唯一Leading Ledger并校验FI凭证关系；不同或冲突台账失败关闭。

## Skill执行与受限数据

固定Agent、组合工作流、自由查询和验收共用同一份已批准Skill目录。Skill必须同时满足只读、已验证、可用、Schema有效、输出策略已声明以及包和Profile指纹匹配。

受限制品Reveal使用本机Origin、CSRF和一次性短时Token。明细默认保留30天，可由用户提前永久删除；删除后仅保留不含业务值的审计墓碑。

## 操作提示

先看公开结论与计数。需要核对明细时点击揭示，再使用本次操作的受保护查看或下载入口；不要将Token放入URL或把参考号写进问题正文。候选匹配需人工复核，不显示为已清账；已删除/到期的证据不等于“未找到来款”。

## English

Use [AR Collection](../agents/FI/ar-collection/README.md) for customer aging/dunning and document-level reasons, actions, priority and CSV worklists. Monitor-only items are excluded from collection tasks. Use [Cash Application](../agents/FI/ar-cash-application/README.md) for bank receipts, customer subledger, clearing and invoice relationships; its existing acceptance is no longer a pending-candidate claim. Current versions and lifecycle come from the generated Agent guides.

FI clearing is not independent bank-settlement evidence. Historical dunning events do not recreate historical customer master data. Leading-ledger and FI relationship conflicts fail closed. Skills remain independent prerequisite gates; documentation updates do not rerun live acceptance.

Approved read-only Skills use one catalog with schema, output-policy and package/Profile fingerprint checks. Raw payer/reference/account fields are protected; public output contains safe keys, amounts, currency, status, masked accounts and domain-bound HMACs. Review public results first, explicitly reveal details when necessary and use operation-bound protected downloads. Local Origin, CSRF and short-lived single-use tokens apply. Default retention is 30 days; deletion retains a business-value-free audit tombstone. Deleted/expired evidence is not a no-receipt conclusion, and a candidate is not a confirmed cleared invoice.
