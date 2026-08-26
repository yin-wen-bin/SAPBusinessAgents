# Product Cost Variance Assistant: SAP data contract

## Evidence order (0.2.0)

1. Embedded GET-only production-order OData derives company code, material, plant, costing variants, and status.
2. Embedded GET-only operational accounting OData resolves the order's actual posting-period range when the user omits year and period.
3. `sap-production-order-cost-analysis` reads AUFK through the protected default ADT connection and proves `AUFNR/OBJNR/KOKRS/BUKRS` attribution.
4. The Skill validates and executes only `I_MfgOrderActlPlanTgtLdgrCost` for ledger `0L`, currency role `10`, and target-cost variant `1`; the analytical consumption view is semantic documentation only.
5. `DATA_GAP` when the released CDS is unavailable, rejected, partial, truncated, or unverifiable.

A complete empty actual-cost query is valid bounded evidence but does not create a zero target cost. Network, authentication, authorization, timeout, malformed response, explicit top, and paging limits remain inconclusive. There is no automatic Provider fallback or SE16N fallback.

Cost evidence is accepted only with `status=complete`, `read_only=true`, `validated=true`, `source_complete=true`, `evidence_complete=true`, `paging_complete=true`, no validation issue, and a matching order relationship. Plan, target, and actual values are compared only within one ledger, currency role, currency, and period scope.
