# P2P→AP 工作流真机验收

验收时间：2026-08-29（UTC）

## 非空证据分组

- 运行：`run_292a7f6bcb9b4019`
- P2P 节点：`completed`
- AP 节点：实际执行，输入 `query_mode=p2p_evidence`
- 工作流状态：`inconclusive`
- 完整性：`source_complete=false`、`business_complete=false`
- 结论：映射和节点执行链通过；最终不完整来自 AP 付款运行、银行主数据或银行扣款等证据限制，不是工作流映射失败。

## 空证据分组

- 运行：`run_34e05adaa4414240`
- P2P 节点：`completed`
- AP 节点：`skipped`，代码 `node_skipped_empty_input`
- 未创建 AP 子运行。
- 工作流仍返回 P2P 的逐采购订单结果、业务报告和完整性字段。
- 工作流状态：`inconclusive`
- 完整性：`source_complete=false`、`business_complete=false`

## 安全与门禁

- Agent Runtime 的结构化复核必须为 `pass` 才创建验证运行。
- Runtime 超时和无效结构已验证为失败关闭，且不会创建 SAP 运行。
- 所有 SAP 访问继续由已注册的 `sap_read.v2` GET-only Provider 执行；AP 的 `p2p_evidence` 路径只消费 P2P 已取得的结构化证据。
- 旧运行和旧草稿修订保持不可变；当前草稿由 compiler v2 生成新修订。
