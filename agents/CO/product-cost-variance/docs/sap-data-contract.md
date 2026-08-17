# Product Cost Variance Assistant: SAP data contract

## Evidence order

1. Embedded GET-only OData with live metadata and complete paging.
2. `assess_api_evidence` separates schema capability gaps from operational failures.
3. Protected `sap-adt-table-export` only for a confirmed capability gap.
4. `DATA_GAP` when ADT is unavailable, partial, failed, truncated, or unverifiable.

A complete empty API result is valid bounded evidence and does not trigger ADT. Network, authentication, authorization, timeout, malformed response, explicit top, and paging limits remain inconclusive. There is no SAPClaw or SE16N fallback.

ADT evidence is accepted only with `status=complete`, `read_only=true`, `validated=true`, `source_complete=true`, `paging_complete=true`, no validation issue, and a matching adjacent SHA-256 manifest.
