from __future__ import annotations

import json
from pathlib import Path

import pytest

from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.models import RunCreate
from sap_business_agents_platform.normalization import (
    FieldReference,
    SapInputNormalizationError,
    SapValueNormalizer,
)
from sap_business_agents_platform.sap_read import EmbeddedODataProvider


def _catalog(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "normalization.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_user_input_is_trimmed_before_required_and_optional_checks(tmp_path: Path) -> None:
    normalizer = SapValueNormalizer(
        _catalog(tmp_path, {"input_aliases": {"material": "uppercase"}, "fields": []})
    )
    schema = {
        "type": "object",
        "properties": {
            "material": {"type": "string"},
            "note": {"type": "string"},
            "optional": {"type": "string"},
            "values": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["material", "note"],
        "additionalProperties": False,
    }

    assert normalizer.normalize_input(
        {
            "material": "\t tg10 \n",
            "note": "  中文 Internal Value  ",
            "optional": "\u2003 ",
            "values": [" A ", " B "],
        },
        schema,
    ) == {
        "material": "TG10",
        "note": "中文 Internal Value",
        "values": ["A", "B"],
    }

    with pytest.raises(SapInputNormalizationError, match="Missing required input"):
        normalizer.normalize_input({"material": "  ", "note": "ok"}, schema)
    with pytest.raises(SapInputNormalizationError, match="Missing required input"):
        normalizer.normalize_input(
            {"material": "TG10", "note": "ok", "values": ["A", "  "]}, schema
        )


def test_exact_field_rules_normalize_plan_and_detect_input_conflicts(tmp_path: Path) -> None:
    fields = [
        {
            "service_name": "API_TEST",
            "odata_version": "2.0",
            "entity_set": "Items",
            "field": "Material",
            "input_normalization": "uppercase",
        },
        {
            "service_name": "API_TEST",
            "odata_version": "2.0",
            "entity_set": "Texts",
            "field": "Description",
            "input_normalization": "preserve",
        },
    ]
    normalizer = SapValueNormalizer(_catalog(tmp_path, {"fields": fields}))
    plan = normalizer.normalize_plan(
        {
            "service_name": "API_TEST",
            "odata_version": "2.0",
            "entity_set": "Items",
            "filters": [
                {"field": "Material", "operator": "in", "value": [" tg10 ", " fg29 "]}
            ],
        }
    )
    assert plan["filters"][0]["value"] == ["TG10", "FG29"]

    with pytest.raises(SapInputNormalizationError) as error:
        normalizer.normalize_plan(
            {
                "service_name": "API_TEST",
                "odata_version": "2.0",
                "entity_set": "Items",
                "filters": [
                    {"field": "Material", "operator": "in", "value": ["TG10", " "]}
                ],
            }
        )
    assert error.value.code == "sap_input_normalization_failed"

    with pytest.raises(SapInputNormalizationError) as conflict:
        normalizer.normalize_input(
            {"identifier": "abc"},
            {"properties": {"identifier": {"type": "string"}}, "required": ["identifier"]},
            field_references={
                "identifier": [
                    FieldReference("API_TEST", "2.0", "Items", "Material"),
                    FieldReference("API_TEST", "2.0", "Texts", "Description"),
                ]
            },
        )
    assert conflict.value.code == "sap_input_normalization_conflict"


def test_v2_and_v4_uppercase_metadata_are_exposed() -> None:
    v2 = """<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx"
      xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
      xmlns:sap="http://www.sap.com/Protocols/SAPData" Version="1.0">
      <edmx:DataServices m:DataServiceVersion="2.0"><Schema xmlns="http://schemas.microsoft.com/ado/2008/09/edm" Namespace="T">
      <EntityType Name="Item"><Key><PropertyRef Name="Material"/></Key><Property Name="Material" Type="Edm.String" sap:display-format="UpperCase"/></EntityType>
      <EntityContainer Name="C"><EntitySet Name="Items" EntityType="T.Item"/></EntityContainer>
      </Schema></edmx:DataServices></edmx:Edmx>"""
    version, parsed = EmbeddedODataProvider._parse_metadata(v2)
    assert version == "2.0"
    assert parsed["Items"]["fields"][0]["display_format"] == "UpperCase"

    v4 = """<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">
      <edmx:DataServices><Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="T">
      <EntityType Name="Item"><Key><PropertyRef Name="Material"/></Key><Property Name="Material" Type="Edm.String"><Annotation Term="com.sap.vocabularies.Common.v1.IsUpperCase" Bool="true"/></Property></EntityType>
      <EntityContainer Name="C"><EntitySet Name="Items" EntityType="T.Item"/></EntityContainer>
      </Schema></edmx:DataServices></edmx:Edmx>"""
    version, parsed = EmbeddedODataProvider._parse_metadata(v4)
    assert version == "4.0"
    assert parsed["Items"]["fields"][0]["is_uppercase"] is True


def test_progress_and_sse_sequence_are_persisted_together(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")
    run_id = "run_progress"
    store.create_run(
        run_id,
        RunCreate(mode="free_query", query=" query "),
    )
    event = store.set_progress(
        run_id,
        phase="reading_sap",
        state="active",
        current_step_id="read_material",
        current_tool="sap_read",
        completed_units=1,
        total_units=3,
        determinate=True,
    )
    record = store.get_run(run_id)
    assert event.type == "progress_changed"
    assert record.progress.phase == "reading_sap"
    assert record.progress.event_sequence == event.sequence
    assert event.data["progress"]["event_sequence"] == event.sequence
    assert store.events_after(run_id)[-1].data["progress"] == record.progress.model_dump(mode="json")
