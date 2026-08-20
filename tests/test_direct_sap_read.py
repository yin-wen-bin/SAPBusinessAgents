from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from scripts.direct_sap_read import _schema, _sort_value, _validate_request


METADATA = b"""<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx"
 xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
 xmlns:sap="http://www.sap.com/Protocols/SAPData"
 xmlns="http://schemas.microsoft.com/ado/2008/09/edm">
 <edmx:DataServices m:DataServiceVersion="2.0">
  <Schema Namespace="TEST">
   <EntityType Name="ItemType">
    <Key><PropertyRef Name="Document"/><PropertyRef Name="Item"/></Key>
    <Property Name="Document" Type="Edm.String" Nullable="false"/>
    <Property Name="Item" Type="Edm.String" sap:display-format="NonNegative" Nullable="false"/>
   </EntityType>
   <EntityContainer Name="Container" m:IsDefaultEntityContainer="true">
    <EntitySet Name="A_Item" EntityType="TEST.ItemType"/>
   </EntityContainer>
  </Schema>
 </edmx:DataServices>
</edmx:Edmx>"""


def _request() -> dict:
    return {
        "source_id": "items",
        "service_name": "API_TEST_SRV",
        "service_path": "/sap/opu/odata/sap/API_TEST_SRV",
        "odata_version": "2.0",
        "entity_set": "A_Item",
        "select_fields": ["Document", "Item"],
        "filter": "Document eq '1'",
        "order_by": ["Document", "Item"],
        "page_size": 100,
        "max_rows": 30000,
    }


def test_direct_reader_uses_live_keys_and_sap_numeric_string_semantics() -> None:
    fields, keys = _schema(METADATA, "A_Item")

    assert keys == ["Document", "Item"]
    assert fields["Item"] == "Edm.String:NonNegative"
    assert _sort_value("2", fields["Item"]) == (1, Decimal("2"))
    assert _sort_value("10", fields["Item"]) == (1, Decimal("10"))
    assert _sort_value("2", "Edm.String:UpperCase") == (1, Decimal("2"), "2")
    assert _sort_value("10", "Edm.String:UpperCase") == (1, Decimal("10"), "10")


def test_direct_reader_rejects_unbounded_or_oversized_requests() -> None:
    request = _request()
    assert _validate_request(request) == request

    request["filter"] = ""
    with pytest.raises(ValueError, match="bounded"):
        _validate_request(request)

    request = _request()
    request["max_rows"] = 30001
    with pytest.raises(ValueError, match="safety limit"):
        _validate_request(request)


def test_direct_reader_rejects_non_odata_service_paths() -> None:
    request = _request()
    request["service_path"] = "https://example.com/anything"

    with pytest.raises(ValueError, match="approved SAP OData path"):
        _validate_request(request)


def test_direct_reader_contract_does_not_treat_the_row_ceiling_as_complete() -> None:
    source = (Path(__file__).resolve().parents[1] / "scripts" / "direct_sap_read.py").read_text(
        encoding="utf-8"
    )

    assert '"$top": str(request["max_rows"])' in source
    assert "paging_complete = False" in source
