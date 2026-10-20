"""Explicit immutable requests for the first fixture set, not general SAP scope.

Changing a fixture or parameter creates a different case binding. No arbitrary
request text, tool endpoint or callback can be supplied by a manifest.
"""
from sap_business_agents_platform.runtime_validation import ValidationError


def parameters(case):
    value = {"case_id": case.id, "fixture": case.fixture, "source_agent": "mm-po-gr-status"}
    if case.id.startswith("query.") and case.sap:
        value.update(purchase_order="4500001466", query_case=case.fixture)
    elif case.id == "sample.scope":
        value.update(purchase_order="4500001466", schedule_line_delivery_date="2022-11-15",
                     purchasing_organization="1010", selected_fields=["plant"])
    elif case.id == "acceptance.campaign":
        value.update(purchase_order="4500001466", schedule_line_delivery_date="2022-11-15", purchasing_organization="1010")
    return value


def validate(case, value):
    if value != parameters(case):
        raise ValidationError("validation_fixture_parameters_changed")
