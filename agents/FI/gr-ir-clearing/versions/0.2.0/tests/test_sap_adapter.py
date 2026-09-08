from datetime import date

from grir_clearing.models import AnalysisCriteria, HistoryEventType, PurchaseOrderKey
from grir_clearing.sap_adapter import SapTableGrirDataSource


class FakeGateway:
    def __init__(self):
        self.calls = []

    def read_rows(self, table, fields, filters):
        self.calls.append((table, tuple(fields), filters))
        if table == "EKKO":
            return [{"EBELN":"4501","BUKRS":"1000","LIFNR":"V1","WAERS":"CNY","EKGRP":"001"}]
        if table == "EKPO":
            return [{"EBELN":"4501","EBELP":"00010","WERKS":"1000","MATNR":"M1","TXZ01":"Part","MENGE":"10","NETPR":"20","PEINH":"1","LOEKZ":""}]
        if table == "EKBE":
            return [
                {"EBELN":"4501","EBELP":"00010","BELNR":"5001","GJAHR":"2026","BUDAT":"20260701","BEWTP":"E","VGABE":"1","BWART":"101","MENGE":"10","WRBTR":"200","WAERS":"CNY","SHKZG":"S","XBLNR":""},
                {"EBELN":"4501","EBELP":"00010","BELNR":"5002","GJAHR":"2026","BUDAT":"20260702","BEWTP":"E","VGABE":"1","BWART":"122","MENGE":"2","WRBTR":"40","WAERS":"CNY","SHKZG":"H","XBLNR":""},
                {"EBELN":"4501","EBELP":"00010","BELNR":"5101","GJAHR":"2026","BUDAT":"20260703","BEWTP":"Q","VGABE":"2","BWART":"","MENGE":"10","WRBTR":"200","WAERS":"CNY","SHKZG":"S","XBLNR":"INV-1"},
            ]
        raise AssertionError(table)


def test_maps_sap_rows_to_normalized_po_and_signed_history():
    gateway = FakeGateway()
    source = SapTableGrirDataSource(gateway)
    criteria = AnalysisCriteria(as_of_date=date(2026, 7, 22), company_code="1000")

    items = source.list_po_items(criteria)
    history = source.load_po_history([PurchaseOrderKey("4501", "00010")], criteria.as_of_date)

    assert len(items) == 1
    events = history[PurchaseOrderKey("4501", "00010")]
    assert [event.event_type for event in events] == [
        HistoryEventType.GOODS_RECEIPT,
        HistoryEventType.GOODS_RETURN,
        HistoryEventType.INVOICE_RECEIPT,
    ]
    assert events[1].quantity == -2
    assert events[1].amount == -40
    assert [call[0] for call in gateway.calls] == ["EKKO", "EKPO", "EKBE"]
