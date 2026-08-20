# budget-rolling-forecast 真机测试报告

- 测试时间：2026-08-19T16:25:21.378132+00:00
- 代码版本：`1b8cc4a+working-tree`
- 系统、客户端、凭据和业务标识均已脱敏
- 主 Provider：`embedded-sap-odata`，严格 GET-only
- 条件补证：`sap-adt-table-export`
- 自动 Provider 回退调用：`0`；SE16N 调用：`0`
- Verdict：**PASS**

## Embedded evidence

- 自动发现输入（脱敏）：`{"company_code": "sha256:7a5df5ffa0de", "cost_center": "sha256:332b10fcbae8", "fiscal_year": "2016", "current_period": "sha256:7a3e6b16cb75", "planning_category": "sha256:0ff15403106f", "risk_threshold_pct": "sha256:4a44dc153642"}`
- 服务/实体：`API_FINPLANNINGENTRYITEM_SRV/A_FinPlanningEntryItem`, `API_OPLACCTGDOCITEMCUBE_SRV/A_OperationalAcctgDocItemCube`
- SAP GET：2；证据行计数：4；耗时：11454.554 ms
- 查询源完整：`true`；业务完整：`true`
- 错误代码：none
- 证据 SHA-256：`1f1b4b5f1db12afc6b3001bac9f7b0b719be59e5537bcc23137196bf4fc7e5ab`

## Step status

- Embedded API：`read_actual`=`complete`, `read_plan`=`complete`
- ADT：`adt_actual`=`skipped/condition_false`, `adt_plan`=`skipped/condition_false`
- Deterministic rules：`assess`=`complete`, `evaluate`=`complete`

## ADT evidence

- 技术预检：`complete`；read_only=`true`；validated=`true`
- source_complete=`true`；paging_complete=`true`；manifest_hash_verified=`true`
- 本流程白名单候选：`ACDOCA`, `ACDOCP`

## Boundary and conclusion

- Missing evidence：none
- 结果摘要：滚动预测因证据不可比而被抑制；业务状态：`attention`
- `source_complete=true` 仅描述实际执行的查询源，不等于业务流程完成或风险已消除。
- API 操作故障、权限、超时、截断和完整空结果均不会触发 ADT。
- 未发布原始行、金额、真实标识、URL、账号或凭据。

## Issue decision

Profile/白名单/权限或业务样本缺口不是代码缺陷，不自动建 Issue。只有最小有界复现确认平台或 Skill 通用缺陷后才去重提交。
