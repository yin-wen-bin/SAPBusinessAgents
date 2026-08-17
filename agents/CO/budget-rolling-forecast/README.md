# Budget Rolling Forecast Assistant

Builds a transparent monthly-average rolling forecast from cost-center year-to-date actuals and full-year plan.

The Agent is deterministic and strictly read-only. Embedded SAP OData is primary; `sap-adt-table-export` is conditional only for verified API capability gaps. SAPClaw and SE16N are not part of this workflow.

Run the local fixture with `budget-rolling-forecast --format markdown`. See [SAP data contract](docs/sap-data-contract.md) and [live SAP test report](docs/live-sap-test-report.md).
