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

The batch-expiry topic is required only when expiry checking is enabled. Expiry
is evaluated only for batches that have current positive stock. Missing expiry
or failed batch association produces `unknown`.

Embedded GET-only OData is primary. Live-DDIC-proven gaps may invoke the approved
read-only `sap-adt-table-export` fallback for MARD, MATDOC, or MCHA, with a
30,000-row ceiling. A 5,000-row Provider limit, 30,000-row ADT limit, partial
paging, duplicate keys, or balance mismatch keeps the result `INCONCLUSIVE`.
The contract does not include MB5B, safety-stock subtraction, transfer
quantities, or transfer recommendations.
