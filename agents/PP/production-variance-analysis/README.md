# Production Quantity and Material Variance Analysis Assistant

比较生产订单计划数量、最终工序确认产量、成品入库、组件领料和物料移动/冲销，输出证据支持的候选原因。成本由独立的 `product-cost-variance` Agent 分析。

规则不会累加多道工序的确认产量，不把“入库6件”写成“只生产6件”，也不会执行确认、收发货、冲销、TECO、重估或结算。

真实 SAP 验证结果见 [docs/live-sap-test-report.md](docs/live-sap-test-report.md)。
