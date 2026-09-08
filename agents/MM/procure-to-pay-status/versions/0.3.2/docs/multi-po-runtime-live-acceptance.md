# P2P 0.3.0 多 PO 真实 SAP 验收

## 范围

- 执行日期：2026-08-29（Asia/Shanghai）
- SAP 访问：`sapclaw_runtime` 与平台内置 OData Provider，全部为 GET-only
- 输入：3 张 PO，覆盖 2 个公司代码和 2 个供应商
- Runtime case：`899033a9-4183-45e0-8923-7133aece8f01`
- 固定批量工作流：`run_676139ae75814a04`
- foreach 工作流：`run_2e167ea3921448ae`

## Runtime 证据

9 个步骤均返回 `source_complete=true`、`source_truncated=false`、`has_next=false`：

| 步骤 | 行数 |
| --- | ---: |
| Purchase-order headers | 3 |
| Purchase-order items | 3 |
| Material-document items | 3 |
| Material-document headers | 3 |
| Supplier-invoice PO references | 1 |
| Supplier-invoice headers | 1 |
| PO-linked accounting items | 6 |
| Expanded accounting-document items | 44 |
| Clearing-document items | 2 |

## 业务与编排结果

- 三张 PO 均保留输入顺序并独立分区，没有跨 PO 串证据。
- 两张 PO 停在收货后未发票/GR-IR 未确认；一张 PO 的 GR/IR 已确认，但供应商行尚未清账。
- 批次最差状态为 `blocked`；`source_complete=true`、`evidence_complete=true`。
- P2P 输出形成 2 个“公司代码 + 供应商”AP 证据分组。
- 固定批量交接与 foreach（2 次独立 AP 迭代）得到相同的业务输出；foreach 没有迭代错误。

## 边界

- 本次样本没有完整付款链，因此没有把“未找到付款”解释为接口缺失。
- SAP 清账和允许类型的付款凭证与银行实际扣款严格分开。
- 付款运行、银行主数据和银行扣款证据完整性均为独立标志，不计入 PO 主链查询完整性。
- 本次验收没有使用 LLM-first 或 SAPSkillhub skill。
