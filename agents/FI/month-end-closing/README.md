# 月结只读业务助理 v0.2.0

`month-end-closing` 是 SAPBusinessAgents 面向财务月结用户的唯一入口。固定 Agent 使用平台内置 `EmbeddedODataProvider`，通过 `sap_read.v2` 发起 GET-only OData 查询，并将共享证据交给 12 个确定性检查模块。

运行链路：

```text
agent.json sap_read step
  -> sap_read.v2
  -> embedded-sap-odata
  -> EmbeddedODataProvider
  -> SAP OData GET
  -> shared EvidenceBundle
  -> 12 checks and one aggregator
```

已废弃的外部 SAP 查询运行时、任何 SAPClaw MCP 和自动 Provider fallback 都不是运行依赖。Released OData 确认缺少权威状态时，只有经过审核、只读并已验证的 SAPSkillhub adapter 才能条件补证；补证失败时结论保持 `inconclusive`。

## 输入与结论

必填输入为 `company_code`、`fiscal_year`、`period` 和 `as_of`；`ledger` 与 `profile_id` 可选。`as_of` 不得晚于运行日。非 K4 会计年度和特殊期间 13–16 必须由公司配置提供日期边界。

业务状态只有：

- `inconclusive`：必需证据、配置、分页或检查不完整；
- `action_required`：证据完整，但有异常或人工确认候选；
- `in_progress`：12 项已通过且证据完整，但尚未到期间结束日；
- `ready_for_review`：12 项已通过、证据完整且已到期间结束日，仅表示可提交人工复核。

`source_complete`、`checklist_complete` 与 `evidence_complete` 分开输出。空结果只有在查询范围和来源完整、且该检查允许“零记录即通过”时才会通过。

## 公司配置

运行配置位于 `.local-data/config/month-end-closing/profiles.json`，该路径保持 Git 忽略。仓库只跟踪：

- [`config/month-end-closing-profiles.schema.json`](../../../config/month-end-closing-profiles.schema.json)
- [`config/profiles.example.json`](config/profiles.example.json)

配置只允许引用审核过的 evidence source ID，不允许传入 SAP URL、client、凭据、任意表名或字段。每次运行记录 profile ID、版本和规范化 SHA-256。

## 12 项准备度检查

检查范围包含 AP 逾期、AR 未分配收款、GL 未清、GR/IR 长账龄、GR/IR 调整候选、AA 折旧状态、外币估值状态、GL 自动清账候选、FI 期间控制、MM 期间状态、CO 未分配成本和 SD 开票传输错误。目标 SAP 的 `API_GLACCOUNTLINEITEM` 不提供 `NetDueDate`，因此 AP 到期证据由审核目录中的 `API_OPLACCTGDOCITEMCUBE_SRV` 提供；查询覆盖截至基准日的历史过账项目，以保留以前期间结转的未清项，Agent 不自行推算到期日。

检查模块只消费同一运行期 EvidenceBundle，不直接访问 SAP。任何一项 `not_assessed` 或 `error` 都会使整体保持 `inconclusive`。

## CLI 与 fixture

固定 Agent 和 CLI 的 `--platform-evidence` 模式调用同一个生产规则入口。evidence 文件应包含平台规则所需的 `run_input`、`scope`、`evidence` 与 `fallbacks`：

```powershell
month-end-closing --platform-evidence .local-data/runs/month-end/evidence.json
```

原有 `fixture` 和人工 SE16N manifest 模式仅保留为历史离线回归和诊断工具，不是固定 Agent 的 SAP Provider，也不能作为 v0.2.0 真实 SAP 验收。

## 安全边界与验收

Agent 永不执行或批准 OB52、MMPV、AFAB、F.05、F.13、MR11、结算、过账或清账。所有 SAP 请求必须是 GET。

当前版本在完成 12 项真实 SAP 验收前保持 `validation.verdict=NOT_TESTED` 和 `executable=false`。实施后的验收门槛见 [`docs/embedded-odata-live-acceptance.md`](docs/embedded-odata-live-acceptance.md)；旧验收记录只保留为历史证据。
