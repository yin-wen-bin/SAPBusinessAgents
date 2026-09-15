# 采购订单入库状态检查 / Purchase order receipt status check

业务域为MM-PUR / MM-IM，SAP业务组件覆盖采购订单与库存管理。本Agent按计划行交货日期执行严格只读的采购订单入库核对。计划行交货日期为唯一必填项；采购组织、采购组、工厂、供应商和采购订单号均为可选筛选，未填写时省略整条对应过滤。

The business domain is MM-PUR / MM-IM, spanning purchasing and inventory management. This Agent performs a strictly read-only purchase-order receipt check by schedule-line delivery date. The date is the sole required input; purchasing organization, purchasing group, plant, supplier, and purchase order are optional, and each corresponding filter is omitted in full when empty.

执行定义通过API_PURCHASEORDER_PROCESS_SRV读取采购订单计划行、项目和抬头，通过API_MATERIAL_DOCUMENT_SRV读取物料凭证项目和抬头；它不直接读取SAP表。系统用采购订单与项目组合键关联证据，随后按稳定键去重、计算收货与冲销净额，并按交货日期和计划行号分配到计划行。

The execution reads purchase-order schedule lines, items and headers through API_PURCHASEORDER_PROCESS_SRV, and material-document items and headers through API_MATERIAL_DOCUMENT_SRV; it does not read SAP tables directly. Evidence is joined by purchase order and item, deduplicated by stable keys, netted for receipts and reversals, and allocated to schedule lines by delivery date and schedule-line number.

结果按采购订单、项目和计划行展示计划数量、净收货数量、未收货数量、最新收货过账日、SAP交货完成标识及入库状态。只有采购订单单位与收货单位可直接比较时才形成数量结论；系统不执行单位换算。零匹配返回明确空结果，同键冲突、来源不完整或单位不可比较会保留为证据缺口或不确定结果。

Results show scheduled, net received and open quantities, the latest receipt posting date, SAP delivery-complete flag, and receipt status by purchase order, item, and schedule line. Quantity conclusions require directly comparable purchase-order and receipt units; no unit conversion is performed. Zero matches produce an explicit empty result, while conflicting keys, incomplete sources or incomparable units remain evidence gaps or inconclusive results.

当前0.1.1为资料修订，执行定义和托管规则保持0.1.0不变。它复用来源版本的PASS验收，不改写原SAP验收日期或比较结论，也不会执行SAP写操作。

Version 0.1.1 is a documentation revision. Its execution and managed rule remain identical to 0.1.0. It reuses the source PASS acceptance without rewriting the original SAP acceptance date or comparisons, and never performs SAP writes.

## 当前范围与输入输出 / Current scope and I/O

<!-- generated:facts:start -->
版本 / Version: **0.1.1** · 使用中 / Active · 验收通过 / Passed

网页 / Web: [zh](http://127.0.0.1:4321/zh/agents/MM/mm-po-gr-status/) · [en](http://127.0.0.1:4321/en/agents/MM/mm-po-gr-status/)

验收模式 / Acceptance mode: `three_stage` · 原记录日期 / Recorded date: 2026-09-14T13:41:07.511697+00:00
证据范围 / Evidence scope: `complete`
复用 0.1.0 验收；文案复核不代表重新查询 SAP。 / Acceptance reused from 0.1.0; documentation review is not a new SAP test.
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
