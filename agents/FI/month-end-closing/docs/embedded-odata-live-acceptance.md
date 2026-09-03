# Embedded OData live acceptance — month-end-closing v0.2.0

## Current verdict

`NOT_TESTED` / `executable=false`

The v0.2.0 implementation and offline contract tests are complete. This document does not claim that the 12 checks have been accepted against a real SAP system.

## 2026-09-04 bounded connectivity result

The SAPBusinessAgents platform was exercised directly through `EmbeddedODataProvider`; no external SAP query runtime or MCP was used.

- `/api/health`: Embedded provider configured, `read_only=true`, 52 registered services.
- `/api/providers/sap-read`: `selected_plugin_id=embedded-sap-odata`, `automatic_fallback=false`.
- Live metadata: company, ledger, operational accounting item, G/L line item, billing, purchase order, material document, and supplier invoice services were compatible.
- Live metadata showed that `API_GLACCOUNTLINEITEM/GLAccountLineItem` does not expose `NetDueDate`. The active plan therefore reads SAP-computed due dates from the reviewed `API_OPLACCTGDOCITEMCUBE_SRV/A_OperationalAcctgDocItemCube` source. Its adaptive `PostingDate` range starts at `1900-01-01` and ends at `as_of`, so prior-period carryforward items are not excluded by the requested fiscal period.
- Bounded GET for company `1710`: one company row and five ledger rows, complete and untruncated.
- Bounded GET for 2026 period 9 through 2026-09-04: the G/L and billing steps completed with zero rows and complete, untruncated sources.
- The revised operational due-item plan was separately validated and executed from `1900-01-01` through 2026-09-04 for company `1710` and ledger `0L`; it returned zero rows with `source_complete=true`, `source_truncated=false`, and no validation issue.

These zero-row probes establish connectivity and completeness only for their narrow scopes; they do not prove that the twelve closing checks pass.

Live acceptance remains blocked because `.local-data/config/month-end-closing/profiles.json` is absent. GR/IR account scope, company thresholds, conditional SAPSkillhub sources, all twelve check outcomes, and SAP GUI sample reconciliation have therefore not been accepted.

## Runtime gates

Before a live run:

1. `/api/health` reports `selected_provider=embedded` and `read_only=true`.
2. `/api/providers/sap-read` reports `selected_plugin_id=embedded-sap-odata` and `automatic_fallback=false`.
3. Every requested service is present in the reviewed OData registry.
4. Live metadata agrees with the registry protocol, entity sets and fields.
5. Every SAP request is GET-only.
6. A reviewed company profile matches system alias, SAP client, company code and effective date.

## Acceptance evidence required

The acceptance run must retain sanitized evidence for:

- company code, fiscal-year variant, company currency and unique leading ledger;
- period boundaries, selected profile ID/version/hash and all configuration gaps;
- SAP-computed `NetDueDate` from `API_OPLACCTGDOCITEMCUBE_SRV` for AP overdue checks, including historical postings still open at `as_of`;
- full paging, stable order and source limits for FI line items;
- the complete PO/GR/invoice/FI chain used by GR/IR checks;
- billing cancellation, posting and accounting-transfer status;
- each conditional SAPSkillhub call, including read-only/validated contract and completeness;
- one result for every required check, with evidence references and missing-evidence reasons;
- generated CSV/JSON/Markdown artifacts and an SAP GUI or standard-report sample reconciliation.

## Fail-closed rules

- Missing profiles do not prevent generic queries, but profile-dependent checks are `not_assessed`.
- Metadata drift, failed pages, truncation, row limits and source conflicts make the affected result incomplete.
- SAPSkillhub is never an automatic Provider fallback.
- A successful posting or a single document does not prove a complete closing program run.
- Empty results pass only where the check declares zero-result semantics and the bounded source is complete.

Only after all 12 checks, the report, UI and manual sample reconciliation pass may the manifest be changed to `validation.verdict=PASS` and `executable=true`.
