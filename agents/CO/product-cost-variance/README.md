# Production Order Cost Variance Analysis Assistant

Compares plan, target, and actual production-order costs by cost element, period, ledger, and currency. Public input is the manufacturing order plus optional fiscal year and period; company code, controlling area, material, and plant are derived from SAP evidence.

The Agent is deterministic and strictly read-only. Embedded SAP OData resolves the order and posting-period scope. The dedicated `sap-production-order-cost-analysis` Skill proves AUFK attribution and attempts the released plan/target/actual cost CDS. Missing target cost is never treated as zero, and standard material price is never substituted for order target cost.

Version 0.2.0 remains blocked on the current target because bounded ADT Data Preview returns HTTP 400 for both the released consumption and interface parameterized cost views. This is an explicit evidence gap, not a zero-cost conclusion.

Run the local fixture with `product-cost-variance --format markdown`. See [SAP data contract](docs/sap-data-contract.md) and [live SAP test report](docs/live-sap-test-report.md).
