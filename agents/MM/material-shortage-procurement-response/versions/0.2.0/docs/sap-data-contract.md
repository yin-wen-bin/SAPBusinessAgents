# SAP data contract

Primary evidence comes from Embedded GET-only MRP and purchasing OData APIs. Required normalized topics are `mrp`, `pr`, `po_schedule`, and `source`. ADT fallbacks are static approved reads of MDKP, EBAN, EKET, and EINE. A topic is complete only when the API is complete or the ADT result is `complete`, `read_only=true`, `validated=true`, fully paged, issue-free, and its adjacent SHA-256 manifest verifies.
