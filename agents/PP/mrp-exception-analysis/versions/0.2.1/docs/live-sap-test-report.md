# 真实 SAP 测试报告：MRP 异常分析

## 当前结论

`PASS`。`mrp-exception-analysis` 版本 `0.2.0` 已在 2026-08-25 完成直接 SAP 基线、自由查询和固定 Agent 三级真机验收；当前可执行状态以 [three-stage-live-acceptance.md](three-stage-live-acceptance.md) 为准。

- `TG10 / 1710 / 1710`：没有短缺，但存在重排/日期已过异常，结论为 `attention/high`。
- `MZ-RM-C900-01 / 1710 / 1710`：确认当前短缺 `188.000`，结论为 `critical`。
- `SG21 / 1010 / 1010`：没有短缺，但存在 06/07 日期已过异常，结论为 `attention/high`。
- 三个样本的自由查询和固定 Agent 均与独立直接 SAP 基线 `MATCH`。
- 所有 SAP 操作均为 GET；未运行 MRP，未修改计划订单、生产订单、采购凭证或 SAP 配置。

## 历史结果说明

2026-08-20 的旧版报告曾因 `MaterialCoverages` 和 `SupplyDemandItems` 超时而判定 `INCONCLUSIVE / PARTIAL PASS`。该结果仅适用于旧版查询与当时环境，现已被本次 v2 精确范围查询和完整三级验收取代，不再代表当前 Agent 状态。

## 持续有效的范围限制

即使 `source_complete=true`，结果也只覆盖指定 SAP 短缺参数文件、计数器和其时间范围，因此必须保留 `sap_shortage_time_horizon_applies`。本 Agent 的 `critical/high/medium/low` 是 SAPBusinessAgents 内置规则定义的业务处理优先级，不是 SAP 原生异常消息优先级。
