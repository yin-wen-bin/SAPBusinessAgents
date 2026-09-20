# free-query-e5e65aec

Generated from a validated free SAP query.

- Query: 如果采购订单4500000114不能按时到货，会影响哪些销售订单？
- Prior correction: Remove the separate PurchasingDocument, MRPPlant, and ProductionPlant filter inputs; reuse purchase-order values and derive plant scope from purchase-order item evidence.
- Current input revision: Purchase order is required and accepts multiple values; material and sales order are optional single-value scope refinements, and the sales-order control uses the same one-line height as material.
- Boundary: selected SAP Provider GET-only execution

Review the business semantics, input schema, output contract, completeness requirements, and deterministic rules before publishing.

## 候选筛查与验收 / Candidate screening and acceptance

按采购订单实际物料与工厂配对、销售订单类MRP需求及订单项目筛查潜在关联候选，不计算实际短缺或交期推迟。

Screen potential-association candidates only, not causal delay impact, shortage quantity or delivery changes. Start from the confirmed purchase_order's items, narrowed by optional material. Preserve each actual Material/Plant pair; never form a Cartesian product. For those exact pairs use sales-order demand (MRPElementCategory VC); optional sales_order restricts MRPElement. Include a sales order only when at least one sales-order item has that same order, material and production plant in the corresponding MRP demand. Deduplicate verified items by SalesOrder/SalesOrderItem and schedule lines by SalesOrder/SalesOrderItem/ScheduleLine; count only schedule lines of verified items. Read complete evidence before claiming exhaustive results. Complete empty scope is zero; missing, bounded or conflicting required evidence is inconclusive. No stock allocation, FIFO, date cutoff, unit conversion, ATP or pegging is performed. Counts in incomplete output describe observed verified records only.

规范记录和业务状态见 execution.acceptance；三级比较仅检验本次案例，不证明实际延期因果关系。
Canonical records and business states are defined by execution.acceptance; case acceptance does not establish causal delay impact.

<!-- generated:facts:start -->
版本 / Version: **0.1.0** · 使用中 / Active · 验收通过 / Passed

网页 / Web: [zh](http://127.0.0.1:4321/zh/agents/MM/mm-listsofsoaffectedbythepo/) · [en](http://127.0.0.1:4321/en/agents/MM/mm-listsofsoaffectedbythepo/)

验收模式 / Acceptance mode: `three_stage` · 原记录日期 / Recorded date: 2026-09-20T11:16:18.573995+00:00
证据范围 / Evidence scope: `complete`
独立SAP基线、自由查询和固定Agent的业务语义一致。
The independent SAP baseline, free query, and fixed Agent are semantically consistent.

### 输入 / Inputs

| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |
|---|---|---|---|---|
| `purchase_order` | 采购订单 / Purchase Order | 必填 / Required | `array` | {"minItems": 1, "maxItems": 100} |
| `material` | 物料 / Material | 可选 / Optional | `string` | {"minLength": 1} |
| `sales_order` | 销售订单 / Sales Order | 可选 / Optional | `string` | {"minLength": 1} |

约束中的 default 是默认值；示例不是 SAP 测试样本。条件必填规则以网页提示和完整 Schema 为准。 / `default` denotes a default, not live test data. Conditional requirements are defined by the form and full Schema.

### 结果字段 / Result fields

- 执行状态 / Execution status
- 查询源完整性 / Query-source completeness
- 成功数据源数量 / Successful source count
- 待复核候选销售订单 / Candidate sales orders for review
- 待复核候选销售订单数量 / Candidate sales order count
- 业务报告 / Business report
- 候选销售订单明细 / Candidate sales-order records
- 已核实项目数 / Verified items
- 已核实计划行数 / Verified schedule lines
- 业务状态 / Business status
- 业务证据完整性 / Evidence completeness
- 业务检查完整性 / Business completeness
- 证据缺口 / Evidence gaps

### 数据与开发资料 / Contracts and development

- [Agent 定义与完整输入输出契约 / Manifest and complete I/O contract](agent.json)
- [原始验收记录（适用范围以原报告为准） / Original acceptance record (original scope applies)](docs/formal-acceptance.md)
- [rules.py](rules.py)
- [tests](tests)
- [开发指南 / Developer guide](../../../docs/developer-guide.md)
<!-- generated:facts:end -->
