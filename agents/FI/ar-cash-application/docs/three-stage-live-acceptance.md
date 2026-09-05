# AR Cash Application three-stage live acceptance

Status: `BLOCKED / secure_reference_live_test_data_gap`

The independent `sap-bank-receipt-evidence` gate passed complete-zero, non-zero, lifecycle `M`, error-status `0`, forced partition, read-only, and privacy checks. Three live cases passed deterministic recomputation:

- a complete-zero receipt period;
- a posted receipt with customer-subledger and confirmed-clearing evidence plus bilingual restricted-artifact frontend checks;
- a posted receipt pending cash application.

The independent baseline, GPT-5.6 Sol acceptance projection, and candidate fixed-Agent snapshot matched in all three cases. Reveal, download, token replay, expiry, cross-run/cross-operation, cursor, CSRF, Origin, deletion, and tombstone checks passed in isolated frontend validation. Raw payer and bank-reference values were not exposed to public output, SSE, logs, Runtime context, browser storage, or public artifacts.

Activation remains blocked for one reason: the target SAP system contains no qualifying non-empty bank reference that can prove the mandatory exact secure-reference lookup path. The all-company bounded discovery found no usable reference; fixtures and shortened values are not accepted for this core live gate. Campaign certification is `sha256:f773e0f6cc2488d3e71edd26cfda32c7f4bf344a9bcb169d7dd2ba2997829fae`.

No SAP posting, clearing, or cash application is allowed.
