# Inventory Health Check

Deterministic, GET-only MM Agent for the current unrestricted-use inventory snapshot and optional slow-moving, obsolete-stock, and expiry checks. When a movement-age check is enabled, it rebuilds the remaining quantity layers from complete material-movement history with Decimal FIFO and reconciles them to two identical current-stock snapshots. Run `inventory-health --format markdown`. Blank thresholds disable the corresponding check. This Agent does not call MB5B, post inventory adjustments, calculate transfer quantities, or recommend transfers.
