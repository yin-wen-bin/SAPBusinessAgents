# Internal Order and Project Control Assistant

Combines order/WBS actual, plan, budget, and commitments into a read-only EAC and budget-risk assessment.

The Agent is deterministic and strictly read-only. Embedded SAP OData is primary; `sap-adt-table-export` is conditional only for verified API capability gaps. automatic Provider fallback and SE16N are not part of this workflow.

Run the local fixture with `order-project-control --format markdown`. See [SAP data contract](docs/sap-data-contract.md) and [live SAP test report](docs/live-sap-test-report.md).
