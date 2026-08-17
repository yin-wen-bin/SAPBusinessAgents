# co-month-end-allocation-settlement 真机测试报告

- 测试时间：2026-08-17T11:41:06.839334+00:00
- 代码版本：`b650df2+working-tree`
- 系统、客户端、凭据和业务标识均已脱敏
- 主 Provider：`embedded-sap-odata`，严格 GET-only
- 条件补证：`sap-adt-table-export`
- SAPClaw 调用：`0`；SE16N 调用：`0`
- Verdict：**PARTIAL**

## Embedded evidence

- 自动发现输入（脱敏）：`{"controlling_area": "sha256:27ea6673d96e", "company_code": "sha256:6cc83ed544a5", "fiscal_year": "2026", "period": "8", "internal_order": "sha256:353fd555c457", "allocation_cycle": "sha256:e099d84cbf2d"}`
- 服务/实体：`API_OPLACCTGDOCITEMCUBE_SRV/A_OperationalAcctgDocItemCube`
- SAP GET：1；证据行计数：0；耗时：11760.613 ms
- 查询源完整：`false`；业务完整：`false`
- 错误代码：run_failed
- 证据 SHA-256：`9efcaba4d806bf7b42b3a1da3169193a88e0f2a345d8d2231d94883b8ce4cb0d`

## Step status

- Embedded API：`read_posting`=`complete`
- ADT：`adt_posting`=`skipped/condition_false`, `adt_cycle`=`failed/run_failed`, `adt_object`=`complete`, `adt_settlement`=`skipped/condition_false`
- Deterministic rules：`assess`=`complete`, `object_numbers`=`complete`, `evaluate`=`complete`

## ADT evidence

- 技术预检：`complete`；read_only=`true`；validated=`true`
- source_complete=`true`；paging_complete=`true`；manifest_hash_verified=`true`
- 本流程白名单候选：`ACDOCA`, `AUFK`, `COBRA`, `T811C`

## Boundary and conclusion

- Missing evidence：allocation_cycle, allocation_cycle_evidence, object_status, settlement_rule
- 结果摘要：分配或结算前置证据不足；业务状态：`capability_blocked`
- `source_complete=true` 仅描述实际执行的查询源，不等于业务流程完成或风险已消除。
- API 操作故障、权限、超时、截断和完整空结果均不会触发 ADT。
- 未发布原始行、金额、真实标识、URL、账号或凭据。

## Issue decision

Profile/白名单/权限或业务样本缺口不是代码缺陷，不自动建 Issue。只有最小有界复现确认平台或 Skill 通用缺陷后才去重提交。
