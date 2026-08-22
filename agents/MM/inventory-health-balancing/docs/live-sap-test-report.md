# 库存健康检查：真机测试报告

## 结论 / Verdict

- **PASS**（测试时间：2026-08-23；证据范围：complete）
- 物料 `TG10`、工厂 `1710`、库存地点 `171A` 已完成三种输入组合的三级真机验收。
- 独立 SAP 直连基线、自由查询和固定 Agent 的业务语义均一致。
- 所有 SAP 请求均为 GET；没有执行 SAP 写操作，也没有调用 MB5B。

## 业务结果

当前符合库存类型 `01`、特殊库存为空的库存为 `7,805 PC`。

| 场景 | 结果 |
|---|---|
| 三项留空 | 仅返回当前库存，`snapshot_only`；三项均为 `not_requested` |
| 慢动留空、呆滞 365 天、临期 90 天 | 呆滞候选；临期未发现候选；慢动未执行 |
| 慢动 180 天、呆滞 365 天、临期 90 天 | 慢动和呆滞候选；临期未发现候选 |

完整的 365 天物料移动窗口返回 0 行。因此系统没有虚构最后移动日期，
而是返回库存账龄下限 365 天。当前正库存不带批次，所以没有可参与 90 天
临期检查的正库存批次。

运行编号、证据哈希和来源覆盖详见 `three-stage-live-acceptance.md`。

## English summary

All three optional-check combinations passed direct-SAP, free-query, and fixed-
Agent live acceptance. Current qualifying stock is `7,805 PC`. The complete
365-day movement window was empty, so the result reports a 365-day lower bound
without inventing a last movement date. No historical balance or transfer
quantity is calculated.
