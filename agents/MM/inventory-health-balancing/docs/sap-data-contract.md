# SAP data contract

Required input is `material`, `plant`, and `storage_location`. Optional integer
thresholds `slow_moving_days`, `obsolete_days`, and `expiry_days` accept values
from 1 through 365. Blank values are omitted and disable the related check.

The current-stock topic is always required and contains only
`InventoryStockType=01` with blank `InventorySpecialStockType`. Units must be
homogeneous before quantities are aggregated.

The movement topic is required only when slow-moving or obsolete checking is
enabled. Material-document items are filtered by exact material, plant, storage
location, `InventoryStockType=01`, and blank special-stock type. The query reads
complete history through the snapshot date without a threshold-derived lower
bound or explicit row limit. Posting and creation timestamps come from headers
joined by `MaterialDocumentYear + MaterialDocument`.

`DebitCreditCode=S` creates a Decimal FIFO layer and `H` consumes the oldest
layer, independently by batch. The deterministic result is complete only when
item/header paging is complete, stable keys are unique and monotonic, units are
valid, the FIFO remainder reconciles exactly to current stock, and the current
stock read before and after movement history is identical. Otherwise the Agent
reports `aging_reconciliation_gap` (or the specific evidence gap), leaves age
quantities unknown, and does not report zero risk. The deprecated last-activity
fields remain for compatibility but never classify the age of all stock.

The batch-expiry topic is required only when expiry checking is enabled. The
Batch API is queried by material. Positive-stock batches first match an exact
`BatchIdentifyingPlant`, then a blank identifying plant; records for other
non-blank plants are ignored. Batch identifiers remain strings and retain
leading zeroes. Conflicting dates are never selected arbitrarily.

An expiration before the snapshot is `expired`; a date from the snapshot
through `expiry_days` is `expiring`; a later date is `not_due`. Confirmed
expired or expiring batches remain visible even when another positive-stock
batch is unmatched or lacks a date. In that case `expiry_status=candidate` but
the overall business status is `inconclusive`.

Embedded GET-only OData is primary. Live-DDIC-proven gaps may invoke the approved
read-only `sap-adt-table-export` fallback for MARD, MATDOC, or batch master data,
with a 30,000-row ceiling. For material-level batches the target system exposes
expiry fields through a DDIC include; if the Skill cannot expand that trusted
metadata, it must return a capability gap and the Agent remains inconclusive.
A complete primary Batch API query remains `source_complete=true`; unresolved
expiry evidence is represented separately by `expiry_evidence_complete=false`.
A 5,000-row Provider limit, 30,000-row ADT limit, partial paging, duplicate
keys, or balance mismatch keeps the result `INCONCLUSIVE`.
The contract does not include MB5B, safety-stock subtraction, transfer
quantities, or transfer recommendations.
