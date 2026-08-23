# 库存健康与库存平衡助手：FIFO 真机测试报告

## 结论 / Verdict

- **PASS**（测试时间：2026-08-23；证据范围：complete；`executable=true`）
- 测试条件：物料 `FG129`、工厂 `1710`、库存地点 `171A`，慢动/呆滞/临期阈值分别为 90/180/90 天。
- Codex App 独立直连基线、SAPBusinessAgents 自由查询和固定 Agent 均得到相同业务结果。
- 所有 SAP 请求均为 GET；未执行库存调整、物料凭证过账、MB5B 或其他写操作。

## 已确认业务结果

| 指标 | 结果 |
|---|---:|
| 当前非限制使用库存 | `4,500 PC` |
| 未达到慢动阈值（0–89 天） | `100 PC` |
| 慢动但未呆滞（90–179 天） | `0 PC` |
| 呆滞库存（≥180 天） | `4,400 PC` |
| 最早剩余 FIFO 层 | `2019-10-31`（2,488 天） |
| 最后库存活动日期 | `2026-08-23`（0 天） |
| 临期批次 | `0` |

完整历史包含 12 条库存类型 `01`、非特殊库存的物料移动。FIFO 剩余层与两次一致的 `4,500 PC` 当前库存快照完全对平。当天新增 `100 PC` 只形成新的 FIFO 层，不再把其余 `4,400 PC` 历史存量的账龄重置为零。

旧运行 `run_98ccc9deec0048b4` 使用“最后一次移动日期代表全部库存账龄”的算法，其“未发现呆滞料”结论已被本版本取代，旧运行记录保持不变以供审计。

## 三级验收

- 独立直连基线：合格；4 个来源均分页完成、稳定键唯一、GET-only。
- 自由查询：`run_ec4e743e524b4e23`，`MATCH`；受控 FIFO 工具完成，最终引用校验通过。
- 固定 Agent：`acceptance_5ff4e5c674924d5f`，`MATCH`。
- 阻塞限制：无。

详细的来源覆盖和证据哈希见 `three-stage-live-acceptance.md`。原始 SAP 行、URL、凭据和物料凭证键仅保存在忽略目录。

## English summary

The full-history FIFO revalidation passed. Current unrestricted-use stock is
`4,500 PC`: `100 PC` is below 90 days, `0 PC` is 90–179 days old, and
`4,400 PC` is at least 180 days old. Twelve complete movement items reconcile
exactly to two identical stock snapshots. A small same-day receipt no longer
resets the age of the historical stock. Direct SAP, free query, and fixed Agent
all match, and every SAP request was GET-only.
