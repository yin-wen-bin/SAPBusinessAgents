# billing-dispute-classification 真机测试报告

- 测试日期：2026-08-17T10:38:39.756133+00:00
- 代码版本：`f392e27`
- 系统与客户端：已脱敏；连接配置和凭据不落库
- Embedded Provider：`embedded` `1.0.0`，严格GET-only
- ADT Skill版本：`7d72576`
- SAPClaw调用数：`0`
- 技术状态：`failed`
- 业务结论：`阻塞`

## 真机证据

- 自然语言/结构化用例输入（脱敏）：`{}`
- Embedded服务与实体：`API_BILLING_DOCUMENT_SRV/A_BillingDocument`, `API_BILLING_DOCUMENT_SRV/A_BillingDocumentItem`, `API_OPLACCTGDOCITEMCUBE_SRV/A_OperationalAcctgDocItemCube`
- SAP GET次数：0；证据行计数：0；耗时：0 ms
- 查询源完整：`false`；业务完整：`false`
- 分页/错误代码：sap_base_url_missing, sap_credentials_missing

## ADT缺口证据

- Skill：`sap-adt-table-export`；平台预检：`blocked`
- 允许对象：`UDMCASEATTR00`
- Profile别名：`sapba-live-readonly`；URL、客户端、凭据和CA路径均位于仓库外。
- Hash验证：`false`；完整性：`false`

## Fixture与推断边界

Fixture仅覆盖规则分支，不替代真机通过。真实业务原始行、客户、金额和完整凭证号未写入本报告。
当前缺口：embedded_provider_configuration。
本轮样本执行状态以SAP GET次数为准；GET=0表示未执行真机样本。正常、异常、取消、部分处理和空结果覆盖尚未完成时，结论保持部分通过或阻塞。

## Issue与复测

复评已有Issue：https://github.com/yin-wen-bin/SAPSkillhub/issues/13；因当前未完成业务对象真机预检而保持Open，未创建重复Issue。
