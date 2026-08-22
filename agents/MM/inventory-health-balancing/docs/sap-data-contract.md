# SAP data contract

Required input is `material`, `plant`, and `storage_location`. Optional integer
thresholds `slow_moving_days`, `obsolete_days`, and `expiry_days` accept values
from 1 through 365. Blank values are omitted and disable the related check.

The current-stock topic is always required and contains only
`InventoryStockType=01` with blank `InventorySpecialStockType`. Units must be
homogeneous before quantities are aggregated.

The movement topic is required only when slow-moving or obsolete checking is
enabled. Material-document items are filtered by exact material, plant, storage
location, and involved fiscal years. Posting dates come from headers joined by
the grouped `MaterialDocumentYear + MaterialDocument` key. A complete empty
window establishes only `stock_age_lower_bound_days`; it never invents a last
movement date.

The batch-expiry topic is required only when expiry checking is enabled. Expiry
is evaluated only for batches that have current positive stock. Missing expiry
or failed batch association produces `unknown`.

Embedded GET-only OData is primary. Live-DDIC-proven gaps may invoke the approved
read-only `sap-adt-table-export` fallback for MARD, MATDOC, or MCHA. The contract
does not include historical stock balances, MB5B, safety-stock subtraction,
transfer quantities, or transfer recommendations.
