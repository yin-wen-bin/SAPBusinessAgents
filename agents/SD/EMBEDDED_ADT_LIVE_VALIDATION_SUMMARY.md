# SD 11 Agent Embedded + ADT 真机校验总览

- 测试时间：2026-08-17T10:38:39.756133+00:00
- 代码版本：`f392e27`
- 主数据通道：Embedded SAP Read Provider `1.0.0`（GET-only）
- ADT Skill版本：`7d72576`
- 安全边界：未执行任何SAP写操作。
- ADT平台预检：`blocked`
- 原始证据：仅保存在被忽略的 `.local-data/live-tests/embedded-adt/`。
- 增量复测：`billing-block-diagnosis` 于2026-08-23使用Embedded Provider `2.0.0`和VBUV ADT补证通过三阶段验收；其原始证据位于被忽略的 `.local-data/live-tests/billing-block-unblock/`。
- 增量复测：`delivered-not-billed` 0.2.0于2026-09-06使用Embedded Provider `2.0.0`完成6次GET，18条项目记录均完整且与直接基线Hash一致；部分开票、取消和超量开票状态未观测。
- 定义回归：`order-to-cash-status` 0.1.1于2026-09-06仅以销售订单号完成9次GET，订单、交货、PGI和开票已确认，FI清账未确认，业务状态保持`partial`。
- 历史快照说明：`order-to-cash-anomaly-monitor` 后续已退役；原表行和“SD 11 Agent”标题仅保留用于审计，不代表当前 Catalog。

| Agent | 技术状态 | 业务结论 | SAP GET | 查询源完整 | 关键缺口 |
| --- | --- | --- | ---: | --- | --- |
| `delivered-not-billed` | failed | 阻塞 | 0 | false | embedded_provider_configuration |
| `billing-block-diagnosis` | completed | 通过 | 6 | true | none（VBUV精确零行、分页完整、Hash已验证） |
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
