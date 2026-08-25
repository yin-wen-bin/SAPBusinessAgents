# 生产订单成本差异分析 0.2.0 真机测试报告

- 结论：**BLOCKED**。
- 样本：生产订单 `1001233`。
- 订单关系：AUFK 已完整取得 `AUFNR/OBJNR/KOKRS/BUKRS`，关系证据通过。
- 实际成本期间：OData 完整返回 6 行，自动解析为 `2020/011`。
- 发布成本视图：消费视图和接口视图的实时 DDL 均存在，参数和计划/目标/实际字段均通过元数据检查。
- 执行缺口：ADT Data Preview 对两个参数化视图均返回 HTTP 400，无法取得按成本要素的计划和目标成本。
- 固定 Agent：`acceptance_fe28cce43bbd431e` 正确返回 `inconclusive`；订单关系和分析期间已确认，计划/目标成本保持未知，没有把缺失值解释为 0。
- 安全：全部 SAP 操作为 GET 或已批准的只读 ADT Data Preview；未运行成本计算、差异计算、结算、重估或任何写操作。

## 后续解除阻塞条件

满足以下任一条件并完成真机对账后才能设置 `PASS/executable=true`：

1. 修复/适配参数化 CDS 的 ADT Data Preview 调用，使 `C_MfgOrdActlPlnTgtLdgrCost` 能完整分页返回。
2. 当前系统开放并验证 `COSP/COSS` 等回退证据，明确值类型、期间、币种和稳定键，并与 SAP 标准成本分析按成本要素对账。

不得使用物料标准单价乘订单数量替代目标成本。
