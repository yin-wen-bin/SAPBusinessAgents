from ap_payment_assistant.adapter import PayablesFilter, SapApDataAdapter
from ap_payment_assistant.mock_adapter import MockSapApDataAdapter


def test_mock_implements_adapter_contract() -> None:
    assert isinstance(MockSapApDataAdapter(), SapApDataAdapter)


def test_open_search_excludes_cleared_items() -> None:
    adapter = MockSapApDataAdapter()

    open_items = adapter.search_payables(PayablesFilter(vendor_id="10001234"))
    all_items = adapter.search_payables(
        PayablesFilter(vendor_id="10001234", include_cleared=True)
    )

    assert len(open_items) == 6
    assert len(all_items) == 7
    assert all(not item.is_cleared for item in open_items)


def test_invoice_reference_finds_cleared_invoice() -> None:
    adapter = MockSapApDataAdapter()

    items = adapter.search_payables(
        PayablesFilter(invoice_reference="inv-paid-001", include_cleared=True)
    )

    assert len(items) == 1
    assert items[0].clearing_document == "2000000456"
    assert "BSAK" in items[0].source_objects

