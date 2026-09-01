# Planned Order & PIR Coverage Assistant

严格只读的多物料计划覆盖 Agent。版本 `0.3.0` 在同一工厂、MRP 范围和日期区间内，一次分析 1–50 个物料：

- 按物料和原生 PIR 期间比较销售订单计划行与有效 PIR。
- 以用户阈值（默认 20%）识别 PIR 覆盖不足、覆盖过度或缺失。
- 使用 SAP `SupplyDemandItems.MRPAvailableQuantity` 判断净供需覆盖；不会把 PIR 当作供给，也不会重复累加库存与计划订单。
- 建议顺序固定为先复核 PIR，PIR 基准确认后再处理计划订单。
- 单个查询分块失败时保留其他物料结果，并把整批状态标记为 `inconclusive`。

手工新增销售需求模拟已经迁移到 SD `new-sales-demand-coverage` Agent。本版本已通过多物料直接 SAP、自由查询、逐物料固定查询和批量固定查询验收，可以执行。

验收状态见 [docs/three-stage-live-acceptance.md](docs/three-stage-live-acceptance.md)。
