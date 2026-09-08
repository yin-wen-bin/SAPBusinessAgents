# 生产订单成本差异分析助手 / Production Order Cost Variance Analysis Assistant

## 用途与使用场景 / Purpose and scenario

复核生产订单的计划、目标与实际成本；按过账期间和成本项目定位金额差异。

Review planned, target and actual production-order costs, tracing monetary variances by posting period and cost item.

## 如何开始 / Getting started

从下方本地网页入口进入；先确认生命周期、验收状态和本机连接配置。填写公开输入后执行，技术字段名仅用于对照契约。示例场景（非真实SAP样本）：用本系统中具有查看权限的业务对象及适用日期运行一次，核对输入范围，再展开一条异常明细；请勿将示例当作测试数据或承诺的结果。

Open the local page below and confirm lifecycle, acceptance and connection readiness. Supply the public inputs; technical IDs help cross-reference the contract. Example scenario (not live SAP data): use a permitted business object or batch with applicable dates, verify scope, then inspect one exception. This is not a test dataset or a promised result.

## 结果解读与下一步 / Reading results and next steps

这是生产订单成本分析，不是通用物料价格或完整物料分类账分析。若关注耗料、报废和收货数量，请另用生产偏差分析助手。

This analyzes production-order costs, not general material pricing or a complete Material Ledger. Use Production Variance Analysis for consumption, scrap and receipt quantities.

查询执行结束不等于业务完成。先看业务结论和证据缺口，再看数量、金额、币种与明细。缺证应补齐后复核，不以零值代替未知；不同币种不合计。建议由业务人员确认后在授权流程中处理，Agent不执行SAP写操作。

A completed query is not a completed business process. Review conclusions and gaps, then counts, amounts, currencies and detail. Resolve gaps before acting; unknown is not zero and currencies are not aggregated. Business staff act through authorized processes; the Agent performs no SAP writes.

## 当前范围与输入输出 / Current scope and I/O

<!-- generated:facts:start -->
版本 / Version: **0.2.0** · 使用中 / Active · 验收通过 / Passed

网页 / Web: [zh](http://127.0.0.1:4321/zh/agents/CO/product-cost-variance/) · [en](http://127.0.0.1:4321/en/agents/CO/product-cost-variance/)

验收模式 / Acceptance mode: `not_recorded` · 原记录日期 / Recorded date: 2026-08-26T19:22:42+08:00
证据范围 / Evidence scope: `complete`
直接ADT、独立Skill、自由查询和固定Agent已对账一致：21条原始记录汇总为8个成本要素，计划、目标、实际和差异金额完全匹配。
Direct ADT, the standalone Skill, the free query, and the fixed Agent reconcile: 21 raw rows aggregate to eight cost elements, with matching plan, target, actual, and variance totals.

### 输入 / Inputs

| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |
|---|---|---|---|---|
| `manufacturing_order` | 生产订单 / Manufacturing order | 必填 / Required | `string` | {"minLength": 1, "maxLength": 12, "pattern": "^[0-9A-Za-z_-]+$"} |
| `fiscal_year` | 会计年度 / Fiscal year | 可选 / Optional | `string` | {"pattern": "^[0-9]{4}$"} |
| `period` | 期间 / Period | 可选 / Optional | `integer` | {"minimum": 1, "maximum": 16} |

约束中的 default 是默认值；示例不是 SAP 测试样本。条件必填规则以网页提示和完整 Schema 为准。 / `default` denotes a default, not live test data. Conditional requirements are defined by the form and full Schema.

### 结果字段 / Result fields

- 生产订单 / Manufacturing order
- 公司代码 / Company code
- 控制范围 / Controlling area
- 物料 / Material
- 工厂 / Plant
- 分析期间起始 / Analysis period from
- 分析期间截止 / Analysis period to
- 账本 / Ledger
- 币种角色 / Currency role
- 目标成本版本 / Target cost variant
- 计划成本总额 / Plan cost total
- 目标成本总额 / Target cost total
- 实际成本总额 / Actual cost total
- 实际减目标差异 / Actual minus target variance
- 实际减目标差异百分比 / Actual minus target variance percent
- 成本状态 / Cost status
- 成本要素明细 / Cost element details
- 订单与成本对象关系证据 / Order-to-cost-object relationship evidence
- 查询源完整性 / Query-source completeness
- 业务证据完整性 / Business evidence completeness
- 业务状态 / Business status
- 结构化业务报告 / Structured business report

### 数据与开发资料 / Contracts and development

- [Agent 定义与完整输入输出契约 / Manifest and complete I/O contract](agent.json)
- [原始验收记录（适用范围以原报告为准） / Original acceptance record (original scope applies)](docs/three-stage-live-acceptance.md)
- [docs/sap-data-contract.md](docs/sap-data-contract.md)
- [docs/offline-regression.md](docs/offline-regression.md)
- [tests](tests)
- [开发指南 / Developer guide](../../../docs/developer-guide.md)
<!-- generated:facts:end -->
