# 离线回归资料 / Offline regression reference

这是文档更新前、版本 0.1.1 的用法记录，供开发者回归使用，不代表当前网页运行入口或新的真机验收。命令的工作目录仍是 Agent 根目录；真实 SAP 调用只能在明确授权时执行。

These are pre-update instructions for version 0.1.1: developer regression material, not the current web workflow or new live acceptance. Commands still use the Agent root; live SAP access requires explicit authorization.

# CO Month-End Allocation and Settlement Assistant

Read-only assessment of period postings, allocation cycle, object status, and settlement rules for one CO object.

The Agent is deterministic and strictly read-only. Embedded SAP OData is primary; `sap-adt-table-export` is conditional only for verified API capability gaps. automatic Provider fallback and SE16N are not part of this workflow.

Run the local fixture with `co-close-readiness --format markdown`. See [SAP data contract](../docs/sap-data-contract.md) and [live SAP test report](../docs/live-sap-test-report.md).
