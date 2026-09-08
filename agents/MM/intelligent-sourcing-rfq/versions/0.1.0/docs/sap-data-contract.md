# SAP data contract

Required normalized topics are `rfq`, `quotation`, `supplier`, and `source`. Embedded RFQ, supplier-quotation, supplier, info-record, and contract APIs are primary. Conditional ADT reads use EKKO, EKPO, and EINE only after live DDIC metadata validation; SAPSkillhub owns its connection configuration and default selection. Currency, unit, and price unit must be common before ranking.
