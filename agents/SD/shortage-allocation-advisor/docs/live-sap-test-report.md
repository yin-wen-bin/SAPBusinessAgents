# 真机SAP测试报告：Shortage Allocation Advisor

- 流程：缺货分配建议
- 测试日期：2026-08-10
- Runtime：健康、启用、严格只读；53个服务已索引，51个可执行
- 样本策略：每个实体使用服务端 `$top=5` 有界脱敏样本
- 全量分页：未验证；`source_complete=false`，不得视为生产全量验收
- 最终结论：**部分通过**

## 自然语言用例

参见Agent README中的业务问题；问题不包含API、实体、字段或OData语法。

## Thin SAPClaw基线

- API_SALES_ORDER_SRV.A_SalesOrderItem（5条）
- API_MATERIAL_STOCK_SRV.A_MatlStkInAcctMod（5条）
- API_PRODUCT_AVAILY_INFO_BASIC（schema验证）

11组实体查询均成功、无validation issue，单次耗时约2.4–10.9秒。真实凭证号、客户和金额未写入本报告。

## Fixture与Agent结果

通过：按优先级稳定分配10和2，且不超过可用库存。

5条销售项目和5条库存样本完成只读建议；样本未观察到未分配短缺。

## LLM-first结果

一次成功读取100条库存记录；另一次SAP执行成功后语义修复LLM超时，存在性能波动。

## SAPSkillhub

仅在标准OData缺少证据时使用。本流程的缺口与issue见下节；仓库内不存在可冒充真机证据的Fixture。

## 问题与复测

未建issue：同一用例已有成功结果，暂不满足稳定复现门槛。

修复关联issue后，应复用相同自然语言问题、确定性Thin基线和脱敏比较规则复测。

## 证据边界

- 真机证据：上述Thin Runtime和LLM-first执行结果。
- Fixture证据：仅用于规则分支和输出契约测试。
- 推断结论：评分、分类和建议动作，均不执行SAP写操作。
