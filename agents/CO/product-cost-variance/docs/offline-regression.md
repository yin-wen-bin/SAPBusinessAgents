# 离线回归资料 / Offline regression reference

这是文档更新前、版本 0.2.0 的用法记录，供开发者回归使用，不代表当前网页运行入口或新的真机验收。命令的工作目录仍是 Agent 根目录；真实 SAP 调用只能在明确授权时执行。

These are pre-update instructions for version 0.2.0: developer regression material, not the current web workflow or new live acceptance. Commands still use the Agent root; live SAP access requires explicit authorization.

# Production Order Cost Variance Analysis Assistant

Compares plan, target, and actual production-order costs by cost element, period, ledger, and currency. Public input is the manufacturing order plus optional fiscal year and period; company code, controlling area, material, and plant are derived from SAP evidence.

The Agent is deterministic and strictly read-only. Embedded SAP OData resolves the order and posting-period scope. The dedicated `sap-production-order-cost-analysis` Skill proves AUFK attribution and attempts the released plan/target/actual cost CDS. Missing target cost is never treated as zero, and standard material price is never substituted for order target cost.

Version 0.2.0 remains blocked on the current target because bounded ADT Data Preview returns HTTP 400 for both the released consumption and interface parameterized cost views. This is an explicit evidence gap, not a zero-cost conclusion.

Run the local fixture with `product-cost-variance --format markdown`. See [SAP data contract](../docs/sap-data-contract.md) and [live SAP test report](../docs/live-sap-test-report.md).
