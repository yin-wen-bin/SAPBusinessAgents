# SAP data contract

Required normalized topics are `po_schedule`, `receipt`, and `supplier`. Embedded PO, material-document, and supplier APIs are primary. Conditional ADT reads use EKET, EKBE, and EKKO within supplier, purchasing-organization, optional plant, and 365-day bounds. Returns and reversals are netted; formal OTIF requires complete schedule and receipt dates plus at least five due schedule lines.
