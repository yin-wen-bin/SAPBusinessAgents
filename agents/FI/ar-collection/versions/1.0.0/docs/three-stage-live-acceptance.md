# AR Collection 1.0.0 three-stage live acceptance

Status: `NOT_TESTED`

The batch input, FI business-key checks, customer-level status, special-GL separation, dunning worklist, and completeness invariants have automated coverage. Version 1.0.0 remains an unpublished inactive candidate while version 0.1.0 remains active.

Activation requires sanitized live comparisons between an independent SAP/ADT baseline, Codex free query, and the fixed Agent for current and historical dates. The evidence must include no-open-item, not-due, overdue, dunning-block, multi-currency, credit, special-GL, and clearing-reversal cases. A known reversal without a trustworthy reversal posting date must remain `inconclusive`.

No clearing, posting, or collection-message sending is allowed.
