# 生产订单成本差异分析 0.2.0 真机测试报告

- 结论：**PASS**，`executable=true`。
- 样本：生产订单 `1001233`，期间 `2020/011`，目标成本版本 `1`。
- 订单关系：AUFK 完整证明 `AUFNR/OBJNR/KOKRS/BUKRS`，公司代码 `1710`、控制范围 `A000`。
- 成本来源：只读接口视图 `I_MfgOrderActlPlanTgtLdgrCost`。
- 直接 ADT：多行 POST Data Preview 返回 HTTP 200，`totalRows=21`、实际返回 21 行，源读取完整。
- 独立 Skill：`status=complete`，21 条原始记录汇总为 8 个成本要素；计划 `-164.26 USD`、目标 `-140.08 USD`、实际 `211.96 USD`、实际减目标 `352.04 USD`。
- 固定 Agent：运行 `acceptance_43e0f4902a42456e` 与直接基线和独立 Skill 完全一致，`fixedAgentComparison=MATCH`。
- 自由查询：运行 `run_2e948659365248b6` 通过通用 Skill 单次令牌门禁执行 `sap-production-order-cost-analysis`，返回相同的 21 条原始记录、8 个成本要素和四项金额，`freeQueryComparison=MATCH`。
- 安全：全部 SAP 操作为 GET 或已批准的只读 ADT Data Preview POST；未运行成本计算、差异计算、结算、重估或任何写操作。

## 验收决定

成本 CDS 的查询、完整性、关系和金额证据已经闭环。Harness 的 Skill gap token 已泛化为受批准 Skill 的运行号、Skill ID 和精确输入绑定，并保留过期、单次使用和 OData 优先约束。直接基线、独立 Skill、自由查询和固定 Agent 四条路径一致，因此解除 `free_query_skill_execution` 阻塞并启用固定 Agent。
