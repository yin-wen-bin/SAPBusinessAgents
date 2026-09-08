# AP 0.2.0 P2P 证据工作流真实 SAP 验收

## 范围

- 执行日期：2026-08-29（Asia/Shanghai）
- 上游 Runtime case：`899033a9-4183-45e0-8923-7133aece8f01`
- 固定批量工作流：`run_676139ae75814a04`
- foreach 工作流：`run_2e167ea3921448ae`
- 输入模式：`p2p_evidence`

## 结果

- AP 消费 2 个按公司代码和供应商分组的类型化 evidence scope，没有重复查询 PO → GR → Invoice → FI 主链。
- 一个 scope 尚无 FI 供应商行，保持 `in_progress` 并报告证据缺口；另一个 scope 有 1 条截止日未清项。
- 真实 FI 行使用 SAP 返回的 `NetDueDate` 和 `CashDiscount1DueDate`；本次识别 1 个可用现金折扣窗口。
- 未发现付款冻结或重复付款候选。
- 固定批量模式与 foreach 模式的 AP scope 结果一致；foreach 执行 2 次独立子迭代且错误数为 0。

## 完整性与付款边界

- 主链 `source_complete=true`、`evidence_complete=true`。
- `payment_run_evidence_complete=false`。
- `bank_master_evidence_complete=false`。
- `bank_settlement_evidence_complete=false`，`bank_settlement_status=not_assessed`。
- 上述缺失不会被表述成“无风险”，也不会把 SAP 清账或付款凭证等同于银行实际扣款。
- 本次验收没有使用 LLM-first 或 SAPSkillhub skill。
