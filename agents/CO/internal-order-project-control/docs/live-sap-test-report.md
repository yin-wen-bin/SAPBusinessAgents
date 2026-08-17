# internal-order-project-control 真机测试报告

- 测试时间：2026-08-17T11:41:06.839334+00:00
- 代码版本：`b650df2+working-tree`
- 系统、客户端、凭据和业务标识均已脱敏
- 主 Provider：`embedded-sap-odata`，严格 GET-only
- 条件补证：`sap-adt-table-export`
- SAPClaw 调用：`0`；SE16N 调用：`0`
- Verdict：**PARTIAL**

## Embedded evidence

- 自动发现输入（脱敏）：`{"object_type": "sha256:3f8c513746d7", "object_id": "sha256:525c1d88cde5", "company_code": "sha256:405101758459", "fiscal_year": "2026", "planning_category": "sha256:0ff15403106f"}`
- 服务/实体：`API_FINPLANNINGENTRYITEM_SRV/A_FinPlanningEntryItem`, `API_OPLACCTGDOCITEMCUBE_SRV/A_OperationalAcctgDocItemCube`
- SAP GET：4；证据行计数：1；耗时：31085.93 ms
- 查询源完整：`true`；业务完整：`false`
- 错误代码：none
- 证据 SHA-256：`93dd9943748c12b4085779dcb7608a9a3942b7bb579939f94ccb50d739d5d52d`

## Step status

- Embedded API：`read_order_actual`=`complete`, `read_wbs_actual`=`complete`, `read_order_plan`=`complete`, `read_wbs_plan`=`complete`
- ADT：`adt_order_master`=`complete`, `adt_wbs_master`=`skipped/condition_false`, `adt_budget`=`skipped/condition_false`, `adt_commitment`=`skipped/condition_false`
- Deterministic rules：`assess`=`complete`, `object_kind`=`complete`, `object_numbers`=`complete`, `evaluate`=`complete`

## ADT evidence

- 技术预检：`complete`；read_only=`true`；validated=`true`
- source_complete=`true`；paging_complete=`true`；manifest_hash_verified=`true`
- 本流程白名单候选：`AUFK`, `BPJA`, `COOI`, `PRPS`

## Boundary and conclusion

- Missing evidence：budget, commitment, control_object_not_found, master
- 结果摘要：订单/项目预算控制证据不可比较；业务状态：`capability_blocked`
- `source_complete=true` 仅描述实际执行的查询源，不等于业务流程完成或风险已消除。
- API 操作故障、权限、超时、截断和完整空结果均不会触发 ADT。
- 未发布原始行、金额、真实标识、URL、账号或凭据。

## Issue decision

Profile/白名单/权限或业务样本缺口不是代码缺陷，不自动建 Issue。只有最小有界复现确认平台或 Skill 通用缺陷后才去重提交。
