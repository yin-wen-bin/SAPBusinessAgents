# AR Cash Application three-stage live acceptance

Status: `PASS / validated_pending_activation`

The independent `sap-bank-receipt-evidence` gate passed complete-zero, non-zero, lifecycle `M`, error-status `0`, forced partition, read-only, and privacy checks. Three live cases passed deterministic recomputation:

- a complete-zero receipt period;
- a posted receipt with customer-subledger and confirmed-clearing evidence plus bilingual restricted-artifact frontend checks;
- a posted receipt pending cash application.

The independent baseline, GPT-5.6 Sol acceptance projection, and candidate fixed-Agent snapshot matched in all three cases. Reveal, download, token replay, expiry, cross-run/cross-operation, cursor, CSRF, Origin, deletion, and tombstone checks passed in isolated frontend validation. Raw payer and bank-reference values were not exposed to public output, SSE, logs, Runtime context, browser storage, or public artifacts.

The fixed Agent now exposes only company code and value-date range inputs. Exact bank-reference search is intentionally outside the `0.1.0` public contract and therefore is not an acceptance requirement. The Campaign covered every required tag: complete zero, posted receipt, customer subledger, confirmed clearing, pending application, and restricted artifact.

The new candidate execution digest is `sha256:96b516de67d1cdbe4ceb93f72840546fa71a356f6ae68a8675961b8556fe9226`. Campaign certification is `sha256:551dbf78e488ef29f4a36672461789763c8e4d7f8dda37bfbfa9fcfa1272f2a5`.

The new restricted-artifact certification run passed 16 frontend checks. A separate destructive run passed 19 checks covering deletion and tombstone behavior without modifying the certification artifact.

No SAP posting, clearing, or cash application is allowed.
