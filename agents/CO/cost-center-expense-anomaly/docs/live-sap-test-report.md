# cost-center-expense-anomaly 真机测试报告

- 测试时间：2026-08-17T11:41:06.839334+00:00
- 代码版本：`b650df2+working-tree`
- 系统、客户端、凭据和业务标识均已脱敏
- 主 Provider：`embedded-sap-odata`，严格 GET-only
- 条件补证：`sap-adt-table-export`
- SAPClaw 调用：`0`；SE16N 调用：`0`
- Verdict：**PASS**

## Embedded evidence

- 自动发现输入（脱敏）：`{"controlling_area": "sha256:27ea6673d96e", "company_code": "sha256:7a5df5ffa0de", "cost_center": "sha256:332b10fcbae8", "fiscal_year": "2016", "period_from": "sha256:6b86b273ff34", "period_to": "sha256:6b86b273ff34", "planning_category": "sha256:0ff15403106f", "variance_threshold_pct": "sha256:f5ca38f748a1"}`
- 服务/实体：`API_COSTCENTER_SRV/A_CostCenter`, `API_FINPLANNINGENTRYITEM_SRV/A_FinPlanningEntryItem`, `API_OPLACCTGDOCITEMCUBE_SRV/A_OperationalAcctgDocItemCube`
- SAP GET：3；证据行计数：5；耗时：17988.063 ms
- 查询源完整：`true`；业务完整：`true`
- 错误代码：none
- 证据 SHA-256：`68db3c8ee1c5c8f2f47024884fcc081c92265674de0ce71356bb317070b35277`

## Step status

- Embedded API：`read_master`=`complete`, `read_actual`=`complete`, `read_plan`=`complete`
- ADT：`adt_master`=`skipped/condition_false`, `adt_actual`=`skipped/condition_false`, `adt_plan`=`skipped/condition_false`
- Deterministic rules：`assess`=`complete`, `evaluate`=`complete`

## ADT evidence

- 技术预检：`complete`；read_only=`true`；validated=`true`
- source_complete=`true`；paging_complete=`true`；manifest_hash_verified=`true`
- 本流程白名单候选：`ACDOCA`, `ACDOCP`, `CSKS`

## Boundary and conclusion

- Missing evidence：none
- 结果摘要：成本中心费用证据不可形成可比偏差率；业务状态：`attention`
- `source_complete=true` 仅描述实际执行的查询源，不等于业务流程完成或风险已消除。
- API 操作故障、权限、超时、截断和完整空结果均不会触发 ADT。
- 未发布原始行、金额、真实标识、URL、账号或凭据。

## Issue decision

Profile/白名单/权限或业务样本缺口不是代码缺陷，不自动建 Issue。只有最小有界复现确认平台或 Skill 通用缺陷后才去重提交。
