# AR Cash Application three-stage live acceptance

Status: `NOT_TESTED`

The manifest, deterministic rules, approved read-only Skill contract, exact FI tuple binding, sensitive-input channel, and encrypted restricted-artifact path have automated coverage. The Agent remains inactive until all of the following are compared on sanitized live samples:

1. independent SAP/ADT bank and FI baseline;
2. Codex free-query result using approved read-only tools;
3. fixed-Agent result, including public and restricted projections.

The acceptance must cover a complete empty period, an active posted receipt, a reversed or in-process receipt, confirmed FI clearing, a unique candidate, and an ambiguous relationship. It must also verify that raw payer and bank-reference values never enter SQLite run payloads, SSE, logs, Runtime context, or public artifacts.

No SAP posting, clearing, or cash application is allowed.
