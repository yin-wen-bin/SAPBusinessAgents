# Product Cost Variance Assistant

Combines production-order actual postings with Material Ledger standard and periodic prices to analyze cost variance.

The Agent is deterministic and strictly read-only. Embedded SAP OData is primary; `sap-adt-table-export` is conditional only for verified API capability gaps. automatic Provider fallback and SE16N are not part of this workflow.

Run the local fixture with `product-cost-variance --format markdown`. See [SAP data contract](docs/sap-data-contract.md) and [live SAP test report](docs/live-sap-test-report.md).
