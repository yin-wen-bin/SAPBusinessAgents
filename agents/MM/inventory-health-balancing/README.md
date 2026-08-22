# Inventory Health Check

Deterministic, GET-only MM Agent for the current inventory snapshot and optional slow-moving, obsolete-stock, and expiry checks. Run `inventory-health --format markdown`. Blank thresholds disable the corresponding check. This Agent does not reconstruct historical balances, call MB5B, calculate transfer quantities, or recommend transfers.
