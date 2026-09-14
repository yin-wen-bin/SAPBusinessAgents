# 采购订单入库状态检查 / Purchase order receipt status check

按计划行交货日期执行严格只读的采购订单入库核对。计划行交货日期为唯一必填项；采购组织、采购组、工厂、供应商和采购订单号均为可选筛选，未填写时会省略整条对应过滤。

The Agent performs a strictly read-only purchase-order receipt check by schedule-line delivery date. The date is the sole required input; purchasing organization, purchasing group, plant, supplier, and purchase order are optional, and each corresponding filter is omitted in full when empty.

结果按采购订单、项目和计划行展示计划数量、净收货数量、未收货数量、最新收货过账日、SAP交货完成标识及入库状态。收货与冲销按物料凭证方向计算，且仅在单位可直接比较时形成数量结论。

Results show scheduled, net received and open quantities, the latest receipt posting date, SAP delivery-complete flag, and receipt status by purchase order, item, and schedule line. Receipts and reversals use material-document direction and quantities are concluded only when units are directly comparable.

<!-- generated:facts:start -->
版本 / Version: **0.1.0** · 使用中 / Active · 验收通过 / Passed

网页 / Web: [zh](http://127.0.0.1:4321/zh/agents/MM/mm-po-gr-status/) · [en](http://127.0.0.1:4321/en/agents/MM/mm-po-gr-status/)

验收模式 / Acceptance mode: `three_stage` · 原记录日期 / Recorded date: 2026-09-14T13:41:07.511697+00:00
证据范围 / Evidence scope: `complete`
独立SAP基线、自由查询和固定Agent的业务语义一致。
The independent SAP baseline, free query, and fixed Agent are semantically consistent.

### 输入 / Inputs

| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |
|---|---|---|---|---|
| `schedule_line_delivery_date` | 计划行交货日期 / Schedule-line delivery date | 必填 / Required | `string` | {"format": "date", "minLength": 1} |
| `purchasing_organization` | 采购组织 / Purchasing organization | 可选 / Optional | `string` | {"minLength": 1} |
| `purchasing_group` | 采购组 / Purchasing group | 可选 / Optional | `string` | {"minLength": 1} |
| `plant` | 工厂 / Plant | 可选 / Optional | `string` | {"minLength": 1} |
| `supplier` | 供应商 / Supplier | 可选 / Optional | `string` | {"minLength": 1} |
| `purchase_order` | 采购订单号 / Purchase order | 可选 / Optional | `string` | {"minLength": 1} |

约束中的 default 是默认值；示例不是 SAP 测试样本。条件必填规则以网页提示和完整 Schema 为准。 / `default` denotes a default, not live test data. Conditional requirements are defined by the form and full Schema.

### 结果字段 / Result fields

- 业务状态 / Business status
- 查询源完整性 / Query-source completeness
- 业务证据完整性 / Business-evidence completeness
- 目标计划行数量 / Target schedule-line count
- 全部入库数量 / Fully received count
- 部分入库数量 / Partially received count
- 未入库数量 / Not received count
- 无法确认数量 / Inconclusive count
- 逐计划行明细 / Schedule-line details
- 业务报告 / Business report

### 数据与开发资料 / Contracts and development

- [Agent 定义与完整输入输出契约 / Manifest and complete I/O contract](agent.json)
- [原始验收记录（适用范围以原报告为准） / Original acceptance record (original scope applies)](docs/formal-acceptance.md)
- [rules.py](rules.py)
- [tests](tests)
- [开发指南 / Developer guide](../../../docs/developer-guide.md)
<!-- generated:facts:end -->
