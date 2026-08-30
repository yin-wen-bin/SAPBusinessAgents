# P2P→AP 工作流真机验收

验收时间：2026-08-30（Asia/Shanghai）

## Compiler v3 契约

- 当前草稿：`workflow_draft_11d01994334a`，revision `11`。
- `composition.compiler_version=3`。
- 工作流共有15个必需输出；AP终端阶段的9个输出全部列入`outputSchema.required`。
- 空上游分组由`onSkip.reasonCode=no_ap_payment_scopes`生成类型安全的显式不确定终态。
- Agent Runtime结构化复核结果为`pass`。

## 非空证据分组

- 运行：`run_dab96ef9b5934dbf`
- P2P 节点：`completed`
- AP 节点：实际执行，子运行`run_d712668d66534695`，输入 `query_mode=p2p_evidence`
- 工作流状态：`inconclusive`
- 完整性：`source_complete=false`、`business_complete=false`
- 输出：15个必需字段全部存在。
- 结论：映射和节点执行链通过；最终不完整来自真实业务证据限制，不是工作流映射或终态缺失。

## 空证据分组

- 运行：`run_b0c4ab96de6f478d`
- P2P 节点：`completed`
- AP 节点：`skipped`，代码 `node_skipped_empty_input`，原因`no_ap_payment_scopes`。
- 未创建 AP 子运行。
- 工作流仍返回 P2P 结果，以及完整的AP终态：`scope_results=[]`、`business_status=inconclusive`、全部证据完整性为`false`、`bank_settlement_status=not_assessed`。
- 工作流状态：`inconclusive`
- 完整性：`source_complete=false`、`business_complete=false`
- SSE包含`node_skipped_empty_input`和`no_ap_payment_scopes`。

## 安全与门禁

- Agent Runtime 的结构化复核必须为 `pass` 才创建验证运行。
- `review_policy_version=2` 的权威预审契约只要求9项AP终态输出；`query_mode`和`as_of`既不是工作流必需终态输出，也没有被下游消费，因此不属于AP的`onSkip`契约。
- Runtime若误报这两个字段缺失，原始`raw_verdict=block`和问题会保留在审计记录中，但有效问题会移入`dismissed_issues`，有效`verdict`调整为`pass`；真实缺少`scope_results`或完整性输出仍严格阻塞。
- 验证顺序固定为“结构与Schema校验 → Runtime设计预审 → 自动发现样本 → 创建验证运行 → SAP读取”。Runtime 阻塞、超时和无效结构保持失败关闭，不执行样本发现、不创建 SAP 运行。
- 所有 SAP 访问继续由已注册的 `sap_read.v2` GET-only Provider 执行；AP 的 `p2p_evidence` 路径只消费 P2P 已取得的结构化证据。
- 旧运行和旧草稿修订保持不可变；当前草稿由 compiler v3 生成新修订。

## 已发布工作流 1.2.0

- 非空分组：`run_2c05536290a34b76`，AP子运行`run_a01204362a0946fc`，11个必需输出全部存在。
- 空分组：`run_c800d2d5519d4121`，AP未创建子运行，11个必需输出全部存在，`ap_status=inconclusive`且所有专项证据完整性为`false`。
- 两条路径均为`inconclusive`；非空路径的业务证据限制和空路径的显式跳过均未被提升为完整付款准备结论。

## Review policy v2 回归

- 草稿：`workflow_draft_f870dcee71a5`，revision `3`。
- 权威契约：`review_policy_version=2`；AP 的 `required_on_skip_outputs_by_node` 只包含9项终态输出，不含 `query_mode/as_of`。
- Runtime原始结论和有效结论均为`pass`，随后才执行自动样本发现并创建运行`run_58c2190f19c945f8`。
- P2P节点完成；AP节点实际执行并因付款运行、银行主数据和银行扣款证据限制返回`inconclusive`。
- 最终验证结论为`inconclusive`，自动检查中的结构、Runtime预审、GET-only审计、必需输出和业务报告均通过；完整性限制没有被提升为通过。
- 验证报告制品保存在草稿隔离目录，并在向导中显示为“真机验证完成 · 存在完整性缺口”。

## Compiler v4 回归

- Runtime即使把`query_mode/as_of`加入`requested_outputs`，编译器也只把它们作为AP执行上下文，不生成工作流终态输出或`onSkip`值。
- 被剔除字段保存在`composition.output_normalization.dismissed_requested_outputs`；Runtime原始建议保存在`proposal_snapshot`。
- AP的`query_mode=p2p_evidence`常量连接和`as_of`工作流输入保持不变，九项付款复核业务终态仍全部为必需输出。
- 只有未被消费的输入回显字段可以自动剔除；真实业务输出或下游消费字段缺少安全跳过值时继续失败关闭。
- 旧失败草稿`workflow_draft_738dc4d787e3`已在原ID上从revision 1恢复为revision 2、compiler v4的两节点草稿；新版Runtime没有再次请求输入回显字段。
- 恢复后真机运行`run_84e2993249fc4eaf`先通过Runtime预审，再自动发现样本并执行P2P与AP。GET-only审计和必需输出检查通过；真实付款运行、银行主数据和银行扣款证据缺口使最终结论保持`inconclusive`。
