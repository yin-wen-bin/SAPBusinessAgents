# SAP data contract

Required normalized topics are `stock`, `movement`, `batch_expiry`, and `parameters`. Embedded stock, material-document, and batch APIs are primary. Conditional ADT fallbacks are MARD, MATDOC, MCH1, and MARC. Only unrestricted/available stock is eligible for balancing; confirmed quantities require complete safety-stock, unit, and batch-quantity evidence.
