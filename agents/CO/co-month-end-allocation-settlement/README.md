# CO Month-End Allocation and Settlement Assistant

Read-only assessment of period postings, allocation cycle, object status, and settlement rules for one CO object.

The Agent is deterministic and strictly read-only. Embedded SAP OData is primary; `sap-adt-table-export` is conditional only for verified API capability gaps. automatic Provider fallback and SE16N are not part of this workflow.

Run the local fixture with `co-close-readiness --format markdown`. See [SAP data contract](docs/sap-data-contract.md) and [live SAP test report](docs/live-sap-test-report.md).
