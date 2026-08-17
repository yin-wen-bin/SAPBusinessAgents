# 真机SAP测试报告：Delivery Delay Prediction

- 流程：交货延期预测
- 测试日期：2026-08-10
- Runtime：健康、启用、严格只读；53个服务已索引，51个可执行
- 样本策略：每个实体使用服务端 `$top=5` 有界脱敏样本
- 全量分页：未验证；`source_complete=false`，不得视为生产全量验收
- 最终结论：**部分通过**

## 自然语言用例

参见Agent README中的业务问题；问题不包含API、实体、字段或OData语法。

## Thin SAPClaw基线

- API_OUTBOUND_DELIVERY_SRV.A_OutbDeliveryHeader（5条）
- API_SALES_ORDER_SRV.A_SalesOrder（5条）

11组实体查询均成功、无validation issue，单次耗时约2.4–10.9秒。真实凭证号、客户和金额未写入本报告。

## Fixture与Agent结果

通过：评分80分，并逐项解释40+20+10+10。

5条交货样本完成评分，最高10分，5条均存在可解释风险因子。

## LLM-first结果

路由到正确交货实体，但被错误的 DeliveryDate/发货日期语义警告阻断。

## SAPSkillhub

仅在标准OData缺少证据时使用。本流程的缺口与issue见下节；仓库内不存在可冒充真机证据的Fixture。

## 问题与复测

SAPClaw [#14](https://github.com/yin-wen-bin/SAPClaw/issues/14)。

修复关联issue后，应复用相同自然语言问题、确定性Thin基线和脱敏比较规则复测。

## 证据边界

- 真机证据：上述Thin Runtime和LLM-first执行结果。
- Fixture证据：仅用于规则分支和输出契约测试。
- 推断结论：评分、分类和建议动作，均不执行SAP写操作。
