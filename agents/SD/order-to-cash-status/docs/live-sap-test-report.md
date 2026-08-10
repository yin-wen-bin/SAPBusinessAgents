# 真机SAP测试报告：Order-to-Cash Status

- 流程：Order-to-Cash状态助手
- 测试日期：2026-08-10
- Runtime：健康、启用、严格只读；53个服务已索引，51个可执行
- 样本策略：每个实体使用服务端 `$top=5` 有界脱敏样本
- 全量分页：未验证；`source_complete=false`，不得视为生产全量验收
- 最终结论：**部分通过**

## 自然语言用例

参见Agent README中的业务问题；问题不包含API、实体、字段或OData语法。

## Thin SAPClaw基线

- API_SALES_ORDER_SRV（5+5条）
- API_OUTBOUND_DELIVERY_SRV（5+5条）
- API_BILLING_DOCUMENT_SRV（5+5条）
- API_OPLACCTGDOCITEMCUBE_SRV（5条）

11组实体查询均成功、无validation issue，单次耗时约2.4–10.9秒。真实凭证号、客户和金额未写入本报告。

## Fixture与Agent结果

通过：订单→交货→PGI→开票→FI→清账阶段语义已验证。

构建1条脱敏O2C链，但有界样本未形成完整到清账关联，因此仅能证明部分链路。

## LLM-first结果

使用真机实际存在的精确销售订单号仍在约148.5秒后返回 no feasible plan。

## SAPSkillhub

仅在标准OData缺少证据时使用。本流程的缺口与issue见下节；仓库内不存在可冒充真机证据的Fixture。

## 问题与复测

SAPClaw [#15](https://github.com/yin-wen-bin/SAPClaw/issues/15)。

修复关联issue后，应复用相同自然语言问题、确定性Thin基线和脱敏比较规则复测。

## 证据边界

- 真机证据：上述Thin Runtime和LLM-first执行结果。
- Fixture证据：仅用于规则分支和输出契约测试。
- 推断结论：评分、分类和建议动作，均不执行SAP写操作。
