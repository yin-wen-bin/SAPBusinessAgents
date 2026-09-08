# 离线回归资料 / Offline regression reference

这是文档更新前、版本 0.4.0 的用法记录，供开发者回归使用，不代表当前网页运行入口或新的真机验收。命令的工作目录仍是 Agent 根目录；真实 SAP 调用只能在明确授权时执行。

These are pre-update instructions for version 0.4.0: developer regression material, not the current web workflow or new live acceptance. Commands still use the Agent root; live SAP access requires explicit authorization.

# Internal Order and Project Control Assistant

Combines order/WBS actual, plan, budget, and commitments into a strictly read-only EAC and budget-risk assessment.

Version 0.4.0 uses separate authoritative object-resolution paths: internal orders are resolved through bounded AUFK evidence, while WBS external IDs are resolved by the live-validated `sap-wbs-object-resolver`. Both paths produce the same `resolved_object` contract. Budget remains bounded BPJA evidence with explicit ledger and currency-role gates. Commitments are accepted only from `sap-control-object-commitment-evidence` and must prove all requested value types 21/22/24/26.

The commitment Skill and registry entry intentionally remain `validated=false` because the target system exposes no verified period-bearing WBS SOAP binding and its internal-order COSP/COSS amount projection is not stable enough to reconcile. Missing or non-comparable amounts remain `null`; no currency conversion, source rediscovery, automatic Provider fallback, or SE16N is used.

The Agent remains `BLOCKED/executable=false`. WBS external-ID conversion is resolved; independent `wbs_mode_acceptance` and `internal_order_mode_acceptance` gates now track the two full business paths. See the [SAP data contract](../docs/sap-data-contract.md), [live SAP test report](../docs/live-sap-test-report.md), and [acceptance report](../docs/three-stage-live-acceptance.md).

Run the local structured fixture with `order-project-control --format markdown`.
