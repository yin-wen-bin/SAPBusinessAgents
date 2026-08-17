# SD 11 Agent Embedded + ADT 真机校验总览

- 测试时间：2026-08-17T10:10:51.837108+00:00
- 代码版本：`5ed8943+working-tree`
- 主数据通道：Embedded SAP Read Provider `1.0.0`（GET-only）
- ADT Skill版本：`7d72576`
- SAPClaw调用数：**0**
- ADT平台预检：`blocked`
- 原始证据：仅保存在被忽略的 `.local-data/live-tests/embedded-adt/`。

| Agent | 技术状态 | 业务结论 | SAP GET | 查询源完整 | 关键缺口 |
| --- | --- | --- | ---: | --- | --- |
| `delivered-not-billed` | failed | 阻塞 | 0 | false | embedded_provider_configuration |
| `billing-block-diagnosis` | failed | 阻塞 | 0 | false | embedded_provider_configuration |
| `billing-completeness-check` | failed | 阻塞 | 0 | false | embedded_provider_configuration |
| `billing-output-monitor` | failed | 阻塞 | 0 | false | embedded_provider_configuration |
| `delivery-delay-prediction` | failed | 阻塞 | 0 | false | embedded_provider_configuration |
| `due-delivery-prioritization` | failed | 阻塞 | 0 | false | embedded_provider_configuration |
| `shortage-allocation-advisor` | failed | 阻塞 | 0 | false | embedded_provider_configuration |
| `billing-dispute-classification` | failed | 阻塞 | 0 | false | embedded_provider_configuration |
| `returns-credit-anomaly` | failed | 阻塞 | 0 | false | embedded_provider_configuration |
| `order-to-cash-anomaly-monitor` | failed | 阻塞 | 0 | false | embedded_provider_configuration |
| `order-to-cash-status` | failed | 阻塞 | 0 | false | embedded_provider_configuration |

## 验收边界

本轮仅在外部连接可用时为每个Agent执行一个自动发现的真实样本；当前若显示GET=0，则表示未执行SAP请求。尚未达到每流程5个样本及全部状态覆盖，不能宣称生产验收完成。
ADT `partial`、`failed`、超限、Hash不一致或Profile不可用均保持 `inconclusive`；MDKP证据不替代ATP。
候选发现查询仅用于选样，不作为源数据完整性证据。
