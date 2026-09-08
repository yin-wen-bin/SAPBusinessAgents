# 生产订单执行监控助手 / Production Order Execution Monitoring Assistant

## 用途与使用场景 / Purpose and scenario

查看生产订单执行情况，结合工序、确认、组件及物料凭证定位停滞和缺证环节。

Review production execution using operations, confirmations, components and material documents to locate delays and missing evidence.

## 如何开始 / Getting started

从下方本地网页入口进入；先确认生命周期、验收状态和本机连接配置。填写公开输入后执行，技术字段名仅用于对照契约。示例场景（非真实SAP样本）：用本系统中具有查看权限的业务对象及适用日期运行一次，核对输入范围，再展开一条异常明细；请勿将示例当作测试数据或承诺的结果。

Open the local page below and confirm lifecycle, acceptance and connection readiness. Supply the public inputs; technical IDs help cross-reference the contract. Example scenario (not live SAP data): use a permitted business object or batch with applicable dates, verify scope, then inspect one exception. This is not a test dataset or a promised result.

## 结果解读与下一步 / Reading results and next steps

区分订单状态、工序实际执行与物料移动。未确认不自动表示未生产；需要回到具体证据和异常清单核实。

Distinguish order status, actual operation execution and material movement. A missing confirmation does not automatically mean no production; inspect the supporting evidence.

查询执行结束不等于业务完成。先看业务结论和证据缺口，再看数量、金额、币种与明细。缺证应补齐后复核，不以零值代替未知；不同币种不合计。建议由业务人员确认后在授权流程中处理，Agent不执行SAP写操作。

A completed query is not a completed business process. Review conclusions and gaps, then counts, amounts, currencies and detail. Resolve gaps before acting; unknown is not zero and currencies are not aggregated. Business staff act through authorized processes; the Agent performs no SAP writes.

## 当前范围与输入输出 / Current scope and I/O

<!-- generated:facts:start -->
版本 / Version: **0.2.0** · 使用中 / Active · 验收通过 / Passed

网页 / Web: [zh](http://127.0.0.1:4321/zh/agents/PP/production-order-monitoring/) · [en](http://127.0.0.1:4321/en/agents/PP/production-order-monitoring/)

验收模式 / Acceptance mode: `three_stage` · 原记录日期 / Recorded date: 2026-09-08T07:18:50.040305+08:00
证据范围 / Evidence scope: `complete`
0.2.0 已通过独立 SAP、Codex 自由查询和固定 Agent 三阶段实机验收；2 道工序、9 个组件和完整空物料移动结果的业务语义一致，全部 SAP 请求均为 GET。
Version 0.2.0 passed three-stage live acceptance across independent SAP, Codex free query, and the fixed Agent; the business semantics for 2 operations, 9 components, and the complete empty material-movement result matched, and every SAP request was GET-only.

### 输入 / Inputs

| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |
|---|---|---|---|---|
| `manufacturing_order` | 生产订单号 / Manufacturing order | 必填 / Required | `string` | {"minLength": 1, "maxLength": 12, "pattern": "^[0-9]+$"} |

约束中的 default 是默认值；示例不是 SAP 测试样本。条件必填规则以网页提示和完整 Schema 为准。 / `default` denotes a default, not live test data. Conditional requirements are defined by the form and full Schema.

### 结果字段 / Result fields

- 生产订单号 / Manufacturing order
- 订单抬头 / Order header
- 订单行项目 / Order items
- 订单状态明细 / Order status details
- 释放状态观察 / Release-status observation
- 工序执行明细 / Operation execution details
- 组件领料明细 / Component withdrawal details
- 物料移动明细 / Material movement details
- 计划数量 / Planned quantity
- 确认产量 / Confirmed yield
- 工序数 / Operation count
- 未完全确认工序 / Operations not fully confirmed
- 组件数 / Component count
- 待领料组件 / Components pending withdrawal
- 物料移动行项目 / Material movement items
- 业务状态 / Business status
- 查询源完整性 / Query-source completeness
- 业务证据完整性 / Business-evidence completeness
- 结构化业务报告 / Structured business report

### 数据与开发资料 / Contracts and development

- [Agent 定义与完整输入输出契约 / Manifest and complete I/O contract](agent.json)
- [原始验收记录（适用范围以原报告为准） / Original acceptance record (original scope applies)](docs/three-stage-live-acceptance.md)
- [开发指南 / Developer guide](../../../docs/developer-guide.md)
<!-- generated:facts:end -->
