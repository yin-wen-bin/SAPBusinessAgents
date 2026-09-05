# AR Collection 1.1.0 three-stage live acceptance

Status: `NOT_TESTED`

The batch input, FI business-key checks, customer-level status, special-GL separation, dunning worklist, historical dunning Skill path, and completeness invariants have automated coverage. Version 1.1.0 remains an unpublished inactive candidate while version 0.1.0 remains active.

Activation requires the independent Skill gate plus sanitized comparisons between an independent SAP/ADT baseline, Codex free query, and the fixed Agent for current and historical dates. Historical runs must use executed dunning events from `sap-ar-dunning-history-evidence`; the current customer dunning master is never treated as a historical snapshot. A known clearing reversal without a trustworthy posting date must remain `inconclusive`.

No clearing, posting, or collection-message sending is allowed.
