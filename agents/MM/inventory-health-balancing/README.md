# 库存健康检查 / Inventory Health Check

## 用途与使用场景 / Purpose and scenario

从当前库存出发，按需检查慢动、呆滞或批次效期；只启用本次需要的检查，逐物料查看风险与证据。

Start from current stock and optionally check slow movement, obsolescence or batch expiry; request only needed checks and review risks per material.

## 如何开始 / Getting started

从下方本地网页入口进入；先确认生命周期、验收状态和本机连接配置。填写公开输入后执行，技术字段名仅用于对照契约。示例场景（非真实SAP样本）：用本系统中具有查看权限的业务对象及适用日期运行一次，核对输入范围，再展开一条异常明细；请勿将示例当作测试数据或承诺的结果。

Open the local page below and confirm lifecycle, acceptance and connection readiness. Supply the public inputs; technical IDs help cross-reference the contract. Example scenario (not live SAP data): use a permitted business object or batch with applicable dates, verify scope, then inspect one exception. This is not a test dataset or a promised result.

## 结果解读与下一步 / Reading results and next steps

未选择的检查是未请求，不是失败。FIFO相关结论依赖完整移动历史；不能以零库存、查询空白或历史快照推断当前风险。

An unselected check is not requested, not failed. FIFO conclusions require complete movement history; zero stock, an empty query or an old snapshot cannot independently establish current risk.

查询执行结束不等于业务完成。先看业务结论和证据缺口，再看数量、金额、币种与明细。缺证应补齐后复核，不以零值代替未知；不同币种不合计。建议由业务人员确认后在授权流程中处理，Agent不执行SAP写操作。

A completed query is not a completed business process. Review conclusions and gaps, then counts, amounts, currencies and detail. Resolve gaps before acting; unknown is not zero and currencies are not aggregated. Business staff act through authorized processes; the Agent performs no SAP writes.

## 当前范围与输入输出 / Current scope and I/O

<!-- generated:facts:start -->
版本 / Version: **0.4.0** · 使用中 / Active · 验收通过 / Passed

网页 / Web: [zh](http://127.0.0.1:4321/zh/agents/MM/inventory-health-balancing/) · [en](http://127.0.0.1:4321/en/agents/MM/inventory-health-balancing/)

验收模式 / Acceptance mode: `not_recorded` · 原记录日期 / Recorded date: 2026-08-24T14:04:28.218015+00:00
证据范围 / Evidence scope: `complete`
独立直连基线、自由查询和固定 Agent 的业务语义一致。
The independent direct-SAP baseline, free query, and fixed Agent are semantically consistent.

### 输入 / Inputs

| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |
|---|---|---|---|---|
| `material` | 物料 / Material | 必填 / Required | `string` | {"minLength": 1, "maxLength": 40, "pattern": "^[0-9A-Za-z_-]+$"} |
| `plant` | 工厂 / Plant | 必填 / Required | `string` | {"minLength": 1, "maxLength": 4, "pattern": "^[0-9A-Za-z_-]+$"} |
| `storage_location` | 库存地点 / Storage Location | 必填 / Required | `string` | {"minLength": 1, "maxLength": 4, "pattern": "^[0-9A-Za-z_-]+$"} |
| `slow_moving_days` | 慢动天数（可选） / Slow-moving days (optional) | 可选 / Optional | `integer` | {"minimum": 1, "maximum": 365} |
| `obsolete_days` | 呆滞天数（可选） / Obsolete days (optional) | 可选 / Optional | `integer` | {"minimum": 1, "maximum": 365} |
| `expiry_days` | 临期天数（可选） / Expiry days (optional) | 可选 / Optional | `integer` | {"minimum": 1, "maximum": 365} |

约束中的 default 是默认值；示例不是 SAP 测试样本。条件必填规则以网页提示和完整 Schema 为准。 / `default` denotes a default, not live test data. Conditional requirements are defined by the form and full Schema.

### 结果字段 / Result fields

- 快照日期 / Snapshot date
- 物料 / Material
- 工厂 / Plant
- 库存地点 / Storage Location
- 当前非限制使用库存 / Current unrestricted-use stock
- 单位 / Unit
- 本次检查项目 / Selected checks
- 最后库存活动日期（兼容字段） / Last inventory activity date (compatibility field)
- 距最后库存活动天数（兼容字段） / Days since last inventory activity (compatibility field)
- 库存账龄下限（兼容字段） / Stock age lower bound (compatibility field)
- 账龄计算方法 / Aging method
- 账龄计算完整性 / Aging completeness
- 最后库存活动日期 / Last inventory activity date
- 距最后库存活动天数 / Days since last inventory activity
- 最早剩余库存层日期 / Oldest remaining inventory-layer date
- 最早剩余库存层账龄（天） / Oldest remaining inventory-layer age (days)
- 已分类库存数量 / Classified stock quantity
- 未分类库存数量 / Unclassified stock quantity
- 未达到最低已启用阈值数量 / Quantity below the lowest enabled threshold
- 慢动但未呆滞数量 / Slow-moving but not obsolete quantity
- 呆滞库存数量 / Obsolete stock quantity
- 库存账龄分布 / Inventory age distribution
- 慢动状态 / Slow-moving status
- 呆滞状态 / Obsolete status
- 临期状态 / Expiry status
- 临期批次数 / Expiry candidate count
- 已过期批次数 / Expired batch count
- 未来临期批次数 / Expiring batch count
- 效期无法确认批次数 / Batches with unresolved expiry
- 批次效期证据完整 / Batch expiry evidence complete
- 批次效期明细 / Batch expiry details
- 业务状态 / Business status
- 查询源完整性 / Query-source completeness
- 证据完整性 / Evidence completeness
- 结构化业务报告 / Structured business report

### 数据与开发资料 / Contracts and development

- [Agent 定义与完整输入输出契约 / Manifest and complete I/O contract](agent.json)
- [原始验收记录（适用范围以原报告为准） / Original acceptance record (original scope applies)](docs/three-stage-live-acceptance.md)
- [docs/sap-data-contract.md](docs/sap-data-contract.md)
- [tests](tests)
- [开发指南 / Developer guide](../../../docs/developer-guide.md)
<!-- generated:facts:end -->
