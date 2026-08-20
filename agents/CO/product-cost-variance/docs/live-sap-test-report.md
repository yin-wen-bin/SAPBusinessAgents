# product-cost-variance 真机测试报告

- 测试时间：2026-08-19T16:25:21.378132+00:00
- 代码版本：`1b8cc4a+working-tree`
- 系统、客户端、凭据和业务标识均已脱敏
- 主 Provider：`embedded-sap-odata`，严格 GET-only
- 条件补证：`sap-adt-table-export`
- 自动 Provider 回退调用：`0`；SE16N 调用：`0`
- Verdict：**PARTIAL**

## Embedded evidence

- 自动发现输入（脱敏）：`{"company_code": "sha256:6cc83ed544a5", "fiscal_year": "2026", "period": "8", "manufacturing_order": "sha256:353fd555c457", "material": "sha256:215048e5a25b", "valuation_area": "sha256:dd7786fd66f8"}`
- 服务/实体：`API_OPLACCTGDOCITEMCUBE_SRV/A_OperationalAcctgDocItemCube`, `API_PRODUCTION_ORDER_2_SRV/A_ProductionOrder_2`
- SAP GET：2；证据行计数：1；耗时：11147.791 ms
- 查询源完整：`false`；业务完整：`false`
- 错误代码：run_failed
- 证据 SHA-256：`f48cb0e62a28c8bf2d0257f55373d5d84c562866716601842c0b6dfcd2ff247b`

## Step status

- Embedded API：`read_order`=`complete`, `read_actual`=`complete`
- ADT：`adt_order`=`skipped/condition_false`, `adt_actual`=`skipped/condition_false`, `adt_cost_header`=`failed/run_failed`, `adt_cost_period`=`skipped/condition_false`
- Deterministic rules：`assess`=`complete`, `cost_numbers`=`complete`, `evaluate`=`complete`

## ADT evidence

- 技术预检：`complete`；read_only=`true`；validated=`true`
- source_complete=`true`；paging_complete=`true`；manifest_hash_verified=`true`
- 本流程白名单候选：`ACDOCA`, `AUFK`, `CKMLCR`, `CKMLHD`

## Boundary and conclusion

- Missing evidence：standard_cost, standard_cost_evidence
- 结果摘要：产品成本差异证据不可比较；业务状态：`capability_blocked`
- `source_complete=true` 仅描述实际执行的查询源，不等于业务流程完成或风险已消除。
- API 操作故障、权限、超时、截断和完整空结果均不会触发 ADT。
- 未发布原始行、金额、真实标识、URL、账号或凭据。

## Issue decision

Profile/白名单/权限或业务样本缺口不是代码缺陷，不自动建 Issue。只有最小有界复现确认平台或 Skill 通用缺陷后才去重提交。
