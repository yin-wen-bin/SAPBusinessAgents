# 真实 SAP 测试报告：需求预测与 PIR 计划

- 结论：**PARTIAL PASS**。真实销售需求、PIR 抬头和计划订单可读；本次未训练或验收预测模型，也未执行 PIR 写回。
- 历史只读基线：`API_SALES_ORDER_SRV/A_SalesOrderItem` 返回 5 条真实订单需求（case `0e65da43-06ee-40ce-b549-28e8b48bccd2`），包括工厂 `1710` 的物料 `MZ-TG-Y120`；`API_PLANNED_ORDERS/A_PlannedOrder` 返回 5 条计划订单（case `659eec18-a3f5-40f4-ae08-1b39ed958548`）。
- SAPSkillhub：SE16N 成功导出 `VBAP` 10 行和 `PBIM` 10 行；PBIM 样本包含活动版本 `00` 的 PIR 记录。
- 对照：PLAF 的计划订单 `542`、`543`、物料 `SG21`、工厂 `1010` 与当时的只读 API 返回一致。
- 限制：销售需求样本与 PIR 样本未按同一物料和期间收敛；公开演示系统数据跨多年，不能据此评价预测准确率。
- 安全：全程 GET/SE16N 只读，未创建或修改 PIR。
