# CO Agents

面向管理会计的五个确定性、严格只读 Agent：

- `cost-center-expense-anomaly`：成本中心实际/计划费用异常
- `co-month-end-allocation-settlement`：月结分配与结算准备度
- `product-cost-variance`：产品成本与物料分类账差异
- `budget-rolling-forecast`：预算滚动预测
- `internal-order-project-control`：内部订单与项目预算控制

统一数据链为 Embedded GET-only OData → API 能力缺口评估 → 条件执行
`sap-adt-table-export` → 显式 `DATA_GAP`。本模块不使用 SAPClaw 或 SE16N。

真机结论见 [CO 五类 Agent 校验总览](LIVE_SAP_VALIDATION_SUMMARY.md)，每个 Agent
页面同时链接自己的脱敏测试报告。
