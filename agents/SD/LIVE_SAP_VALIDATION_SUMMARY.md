# SAP SD 11个Agent真机验证总览

测试日期：2026-08-10。Thin Runtime健康、启用且只读；11组实体查询全部成功，每组取得5条有界脱敏样本，单次耗时约2.4–10.9秒。由于使用 `$top=5`，所有报告均明确标记未证明全量分页。

| Agent | 真机结论 | LLM-first | Issue |
|---|---|---|---|
| Delivered-not-Billed | 通过（有界样本） | 成功，约85.0秒 | — |
| Billing Block Diagnosis | 通过（有界样本） | 成功，约55.2秒 | — |
| Billing Completeness | 部分通过 | 成功/失败波动 | SAPClaw #16 |
| Billing Output Monitor | 阻塞 | 无可行数据面 | SAPSkillhub #12 |
| Delivery Delay Prediction | 部分通过 | 语义警告误阻断 | SAPClaw #14 |
| Due Delivery Prioritization | 部分通过 | 语义警告误阻断 | SAPClaw #14 |
| Shortage Allocation | 部分通过 | 成功/修复超时波动 | — |
| Billing Dispute Classification | 阻塞 | 合理请求澄清 | SAPSkillhub #13 |
| Returns and Credit Anomaly | 部分通过 | 宽泛问题请求澄清 | — |
| O2C Anomaly Monitor | 部分通过 | 宽泛问题请求澄清 | — |
| O2C Status | 部分通过 | 精确订单仍无可行跨API计划 | SAPClaw #15 |

## 已创建issue

- [SAPClaw #14](https://github.com/yin-wen-bin/SAPClaw/issues/14)：修复中文交货查询的DeliveryDate误告警。
- [SAPClaw #15](https://github.com/yin-wen-bin/SAPClaw/issues/15)：增加可执行的跨API O2C状态规划。
- [SAPClaw #16](https://github.com/yin-wen-bin/SAPClaw/issues/16)：稳定发票完整性跨实体规划。
- [SAPSkillhub #12](https://github.com/yin-wen-bin/SAPSkillhub/issues/12)：增加发票输出状态只读取证skill。
- [SAPSkillhub #13](https://github.com/yin-wen-bin/SAPSkillhub/issues/13)：增加发票争议只读取证skill。

## 总体结论

实现和真机有界样本验证已经完成；输出监控与争议分类受外部取证能力阻塞，其余部分通过项需在对应issue修复后复测。当前结果不代表生产全量数据验收。
