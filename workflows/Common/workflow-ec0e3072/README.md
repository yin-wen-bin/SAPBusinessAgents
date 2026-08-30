# 采购订单到付款准备度工作流

第一阶段批量追踪最多 50 张采购订单的端到端 P2P 状态并生成应付证据分组；第二阶段复用这些证据，检查到期、逾期、现金折扣、付款冻结、重复候选及付款准备度。工作流不执行付款、清账或任何 SAP 写操作，也不把清账凭证等同于银行实际扣款。

- 固定、确定性、严格只读的组合工作流。
- Agent版本或摘要变化后必须停止运行并重新验证。
- 工作流执行完成不自动代表相关SAP业务流程已经完成。

## Purchase Order to Payment Readiness Workflow

The first stage traces the end-to-end P2P status of up to 50 purchase orders and produces grouped AP evidence. The second stage reuses that evidence to review due and overdue items, cash discounts, payment blocks, duplicate candidates, and payment readiness. The workflow never pays, clears, or performs SAP write operations, and does not treat a clearing document as proof of bank settlement.

- Fixed, deterministic, strictly read-only composite workflow.
- Agent version or digest drift requires revalidation.
- Successful execution does not by itself prove business-process completion.
