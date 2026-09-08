# 新增销售需求覆盖度检查助手 / New Sales Demand Coverage Check Assistant

## 用途与使用场景 / Purpose and scenario

在录入SAP前逐物料填写新增需求数量、需求日期和可选单位，检查需求日及后续供需影响；每个物料仅一条需求。

Before SAP entry, supply a quantity, date and optional unit for each material to assess demand-date coverage and later effects; one demand row per material.

## 如何开始 / Getting started

从下方本地网页入口进入；先确认生命周期、验收状态和本机连接配置。填写公开输入后执行，技术字段名仅用于对照契约。示例场景（非真实SAP样本）：用本系统中具有查看权限的业务对象及适用日期运行一次，核对输入范围，再展开一条异常明细；请勿将示例当作测试数据或承诺的结果。

Open the local page below and confirm lifecycle, acceptance and connection readiness. Supply the public inputs; technical IDs help cross-reference the contract. Example scenario (not live SAP data): use a permitted business object or batch with applicable dates, verify scope, then inspect one exception. This is not a test dataset or a promised result.

## 结果解读与下一步 / Reading results and next steps

MRPAvailableQuantity是模拟余额依据；库存只作展示，不重复加入。不换算单位、不外推超出MRP范围的日期。结论是MRP供需模拟，正式ATP始终未核验。

MRPAvailableQuantity is the simulation balance; displayed stock is not added again. No unit conversion or date extrapolation beyond the MRP range is performed. This is an MRP simulation, never a formal ATP confirmation.

查询执行结束不等于业务完成。先看业务结论和证据缺口，再看数量、金额、币种与明细。缺证应补齐后复核，不以零值代替未知；不同币种不合计。建议由业务人员确认后在授权流程中处理，Agent不执行SAP写操作。

A completed query is not a completed business process. Review conclusions and gaps, then counts, amounts, currencies and detail. Resolve gaps before acting; unknown is not zero and currencies are not aggregated. Business staff act through authorized processes; the Agent performs no SAP writes.

## 当前范围与输入输出 / Current scope and I/O

<!-- generated:facts:start -->
版本 / Version: **0.1.0** · 使用中 / Active · 验收通过 / Passed

网页 / Web: [zh](http://127.0.0.1:4321/zh/agents/SD/new-sales-demand-coverage/) · [en](http://127.0.0.1:4321/en/agents/SD/new-sales-demand-coverage/)

验收模式 / Acceptance mode: `not_recorded` · 原记录日期 / Recorded date: 2026-09-01T14:24:20+08:00
证据范围 / Evidence scope: `complete`
0.1.0已通过直接SAP、Codex自由查询、逐物料固定查询和批量固定查询验收；三个需求模拟结果一致，全部请求均为GET。
Version 0.1.0 passed direct SAP, Codex free-query, individual fixed-Agent, and batch fixed-Agent acceptance; all three demand simulations matched and every request was GET-only.

### 输入 / Inputs

| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |
|---|---|---|---|---|
| `plant` | 工厂 / Plant | 必填 / Required | `string` | {"minLength": 1, "maxLength": 4, "pattern": "^[0-9A-Za-z_-]+$"} |
| `mrp_area` | MRP范围 / MRP area | 可选 / Optional | `string` | {"maxLength": 10, "pattern": "^[0-9A-Za-z_-]*$"} |
| `horizon_days` | 后续检查天数 / Subsequent horizon days | 可选 / Optional | `integer` | {"minimum": 1, "maximum": 366, "default": 90, "x-sapba-server-default": true} |
| `demand_items` | 逐物料新增需求 / Per-material new demand | 必填 / Required | `array` | {"minItems": 1, "maxItems": 50} |
| `demand_items[].material` | 物料 / Material | 必填 / Required | `string` | {"minLength": 1, "maxLength": 40, "pattern": "^[0-9A-Za-z_-]+$"} |
| `demand_items[].quantity` | 新增需求数量 / New demand quantity | 必填 / Required | `number` | — |
| `demand_items[].demand_date` | 需求日期 / Demand date | 必填 / Required | `string` | {"format": "date", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"} |
| `demand_items[].unit` | 单位（可选） / Unit (optional) | 可选 / Optional | `string` | {"maxLength": 3, "pattern": "^[0-9A-Za-z]*$"} |

约束中的 default 是默认值；示例不是 SAP 测试样本。条件必填规则以网页提示和完整 Schema 为准。 / `default` denotes a default, not live test data. Conditional requirements are defined by the form and full Schema.

### 结果字段 / Result fields

- 请求物料数 / Requested material count
- 已处理物料数 / Processed material count
- 正常物料数 / Normal material count
- 需关注物料数 / Attention material count
- 无法确认物料数 / Inconclusive material count
- 逐物料结果 / Per-material results
- 查询源完整性 / Query-source completeness
- 业务证据完整性 / Business-evidence completeness
- 证据缺口 / Evidence gaps
- 业务状态 / Business status
- 结构化业务报告 / Structured business report

### 数据与开发资料 / Contracts and development

- [Agent 定义与完整输入输出契约 / Manifest and complete I/O contract](agent.json)
- [原始验收记录（适用范围以原报告为准） / Original acceptance record (original scope applies)](docs/three-stage-live-acceptance.md)
- [开发指南 / Developer guide](../../../docs/developer-guide.md)
<!-- generated:facts:end -->
