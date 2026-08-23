# billing-block-diagnosis 真机测试报告

- 增量复测日期：2026-08-23
- 代码基线：`9887912+working-tree`
- 系统与客户端：已脱敏；连接配置和凭据不落库
- Embedded Provider：`embedded-sap-odata` `2.0.0`，严格GET-only
- ADT Skill：`sap-adt-table-export`，平台插件版本 `1.0.0`
- 安全边界：未执行任何SAP写操作。
- 技术状态：`completed`
- 业务结论：`通过`

## 真机证据

- 测试输入：一个已脱敏销售订单标识；原值仅保存在被忽略的本地工件中。
- Embedded执行：1个确定性计划，覆盖6个精确查询分支：销售订单、订单项目、交货项目、交货抬头、开票项目和开票抬头。
- 独立直连基线：6个来源各返回1行，均为1页，稳定排序、分页完整和查询源完整。
- 固定Agent耗时：Embedded计划约47.0秒；ADT预检约9.2秒；ADT正式查询约9.7秒。
- 固定Agent结果：1条项目记录，`source_complete=true`、`business_complete=true`、`missing_evidence=[]`。
- 本样本未发现冻结或不完整字段，业务状态为 `normal`；异常冻结样本本轮未观测，不能由此推断目标系统不存在冻结业务。

## ADT缺口证据

- Embedded实时Schema未提供项目级不完整状态，因此按OData优先门控条件调用ADT。
- 目标对象：`VBUV`；字段：`VBELN`、`POSNR`、`ETENR`、`TBNAM`、`FDNAM`、`FEHGR`、`STATG`。
- 预检：精确订单+项目，`max_rows=2`；正式查询：精确订单，`max_rows=200`。
- 两次ADT执行均为 `complete`，返回0行，`source_complete=true`、`paging_complete=true`、Hash验证成功。
- VBUV是缺失字段的稀疏日志；精确完整的零行结果只证明该订单范围未记录缺失字段，不要求每个项目各返回一行。
- `partial`、`failed`、超限、Hash不一致或返回键越出Embedded订单项目范围时，Agent仍保持 `inconclusive`。

## 三阶段验收

- 独立直连基线：`MATCH`
- 自然语言自由查询：`MATCH`；成功按OData优先顺序调用VBUV，未猜测ADT稳定键。
- 固定Agent：`MATCH`
- 总体：`PASS` / `executable=true`
- 原始证据与业务标识保存在被忽略的 `.local-data/live-tests/billing-block-unblock/`；仓库仅提交脱敏摘要和Hash。

## Fixture、边界与Issue

- Fixture覆盖：完整零行、存在缺失字段、ADT部分返回、Hash未验证、订单或项目越界、Embedded不完整。
- 首次Embedded Schema校验在默认超时预算内失败；按计划提高到180秒后成功，未将超时解释为零数据。
- 早期VBUP方案在真实系统上完整返回零行，但其逐项目状态语义不能证明所有项目完整；已改用SAP标准不完整日志VBUV。
- 自然语言链路首次因猜测ADT `order_by` 失败；已修复平台提示契约，要求未取得实时稳定键时省略该参数，由Skill解析真实DDIC键。
- 上述问题均在SAPBusinessAgents仓库内修复并通过复测；未发现需要提交到SAPSkillhub的新通用缺陷，因此未创建外部Issue。
- 当前仅完成1个真实正常样本，尚未达到每流程5个样本及正常、异常、取消、部分处理全覆盖；本次“通过”指已解除原能力阻塞并通过该真机验收用例，不等同于生产验收全部完成。
