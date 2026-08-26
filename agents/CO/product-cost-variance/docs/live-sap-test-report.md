# 生产订单成本差异分析 0.2.0 真机测试报告

- 结论：**BLOCKED**，`executable=false`。
- 样本：生产订单 `1001233`，期间 `2020/011`，目标成本版本 `1`。
- 订单关系：AUFK 完整证明 `AUFNR/OBJNR/KOKRS/BUKRS`，公司代码 `1710`、控制范围 `A000`。
- 成本来源：只读接口视图 `I_MfgOrderActlPlanTgtLdgrCost`。
- 直接 ADT：多行 POST Data Preview 返回 HTTP 200，`totalRows=21`、实际返回 21 行，源读取完整。
- 独立 Skill：`status=complete`，21 条原始记录汇总为 8 个成本要素；计划 `-164.26 USD`、目标 `-140.08 USD`、实际 `211.96 USD`、实际减目标 `352.04 USD`。
- 固定 Agent：运行 `acceptance_b0d862aca7c64733` 与直接基线和独立 Skill 完全一致，`fixedAgentComparison=MATCH`。
- 自由查询：运行 `run_dadcfcabc3da4c93` 因错误字段绑定失败；修正输入后的 `run_17d7a4dd62814b11` 又被 Harness 的单次 gap token 门禁拒绝，未形成可比较的 Skill 证据。
- 安全：全部 SAP 操作为 GET 或已批准的只读 ADT Data Preview POST；未运行成本计算、差异计算、结算、重估或任何写操作。

## 验收决定

成本 CDS 的查询、完整性、关系和金额证据已经闭环。由于计划要求固定 Agent 与自由查询均为 `MATCH` 后才能解除阻塞，本次仍保持 `BLOCKED/executable=false`，唯一阻塞项为 `free_query_skill_execution`。

解除阻塞需要修复 Harness 的 Skill gap token 执行链路，并使用相同输入完成一次自由查询；其 21 条原始记录、8 个成本要素、四项金额、账本、币种、期间及完整性必须与上述基线完全一致。
