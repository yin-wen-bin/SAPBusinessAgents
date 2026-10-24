# GLAccountLineItem source semantics

This is an analytical general-ledger view, not an interchangeable supplier entry-view source.
Use live metadata for actual fields and types. The reviewed machine-readable role is in
`config/sap-source-semantics.json`; it contains no tenant configuration or result values.

For detail preserve SourceLedger, CompanyCode, FiscalYear, AccountingDocument,
LedgerGLLineItem, Ledger and GLAccount. AccountingDocumentItem can be zero for
general-ledger-only records and is not a substitute for the real ledger line.
Dropping dimensions can aggregate measures before they reach the platform.
Keep amount and corresponding currency paired. Never add ledger copies together,
divide by ledger count, choose an arbitrary ledger or deduplicate to match a candidate.

RFBC, fiscal period 0/000 and document category C together establish balance-carryforward
classification. A B prefix, January 1 date or empty IsOpenItemManaged alone does not.
Carryforward remains in the evidence and ledger reconciliation; it is not a new supplier invoice.
Reconciliation accounts can have an empty GL open-item-management flag while the subledger
manages open items. Do not use that flag alone as a supplier exclusion.

This view cannot establish the complete population of noted requests which do not update
balances. Source-complete does not mean business-complete. Incomparable coverage/grain
blocks acceptance; it is not an SDK authentication or environment failure.

References: SAP KBA 2926952, KBA 3516798, and SAP balance-carryforward documentation.
