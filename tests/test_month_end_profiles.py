from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.month_end_profiles import (
    ENABLE_CONFIRMATION,
    MonthEndProfileError,
    MonthEndProfileService,
)
import sap_business_agents_platform.month_end_profiles as profile_module


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "agents" / "FI" / "month-end-closing" / "config" / "profiles.example.json"


def _profile(profile_id: str = "closing-1710") -> dict[str, object]:
    value = deepcopy(json.loads(EXAMPLE.read_text(encoding="utf-8"))["profiles"][0])
    value.update(
        {
            "profile_id": profile_id,
            "version": "1.0.0",
            "enabled": False,
            "system_alias": "default",
            "sap_client": "100",
            "company_code": "1710",
            "effective_from": "2026-01-01",
        }
    )
    value["co"]["controlling_area"] = "A000"
    return value


def _sap_payload(rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "ok": True,
        "source_complete": True,
        "source_truncated": False,
        "data": {"results": rows, "source_complete": True, "source_truncated": False},
    }


class FakeSapRead:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.plans: list[dict[str, object]] = []

    async def execute_plan(self, plan, query="", conversation_id=None):
        del query, conversation_id
        self.plans.append(deepcopy(plan))
        if self.unavailable:
            raise RuntimeError("offline")
        if plan["entity_set"] == "A_CompanyCode":
            return _sap_payload(
                [
                    {
                        "CompanyCode": "1710",
                        "CompanyCodeName": "Test Company",
                        "Currency": "CNY",
                        "FiscalYearVariant": "K4",
                        "ControllingArea": "A000",
                    }
                ]
            )
        return _sap_payload(
            [
                {"Ledger": "0L", "IsLeadingLedger": True},
                {"Ledger": "2L", "IsLeadingLedger": False},
            ]
        )


def _service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sap=None) -> MonthEndProfileService:
    monkeypatch.delenv("SAPBA_MONTH_END_PROFILE_PATH", raising=False)
    monkeypatch.setenv("SAPBA_SAP_SYSTEM_ALIAS", "default")
    return MonthEndProfileService(
        repository_root=ROOT,
        data_root=tmp_path / "data",
        database_path=tmp_path / "platform.sqlite3",
        sap_client="100",
        sap_read=sap or FakeSapRead(),
    )


def test_missing_registry_create_is_atomic_and_old_enabled_default_is_compatible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, monkeypatch)
    initial = service.registry()
    assert initial["registry_exists"] is False
    assert initial["editable"] is True

    created = service.create(
        _profile(),
        expected_registry_digest=initial["registry_digest"],
        actor="local-user",
    )
    assert created["profile"]["version"] == "1.0.0"
    assert created["profile"]["enabled"] is False
    assert service.path.is_file()
    assert not list(service.path.parent.glob("profiles.*.tmp"))

    stored = json.loads(service.path.read_text(encoding="utf-8"))
    stored["profiles"][0].pop("enabled")
    service.path.write_text(json.dumps(stored), encoding="utf-8")
    listing = service.registry()
    assert listing["profiles"][0]["profile"]["enabled"] is True


def test_online_validation_uses_two_get_plans_then_allows_enable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sap = FakeSapRead()
    service = _service(tmp_path, monkeypatch, sap)
    initial = service.registry()
    created = service.create(
        _profile(), expected_registry_digest=initial["registry_digest"], actor="tester"
    )
    validated = asyncio.run(service.validate(created["profile"], online=True))
    assert validated["online_validation"]["status"] == "validated"
    assert validated["can_enable"] is True
    assert [plan["http_method"] for plan in sap.plans] == ["GET", "GET"]
    assert [plan["entity_set"] for plan in sap.plans] == ["A_CompanyCode", "A_Ledger"]

    enabled = service.set_enabled(
        "closing-1710",
        True,
        expected_registry_digest=created["registry_digest"],
        expected_profile_digest=created["profile_digest"],
        actor="tester",
        confirmation=ENABLE_CONFIRMATION,
    )
    assert enabled["profile"]["enabled"] is True
    assert enabled["profile"]["version"] == "1.0.1"
    with sqlite3.connect(service.database_path) as conn:
        verification = conn.execute(
            "SELECT provider, actor, metadata_json FROM month_end_profile_verifications"
        ).fetchone()
        audit_actions = {
            row[0]
            for row in conn.execute(
                "SELECT action FROM month_end_profile_audit_events"
            ).fetchall()
        }
    assert verification[:2] == ("embedded-sap-odata", "local-user")
    assert "Currency" not in verification[2]
    assert {"create", "online_validate", "enable"} <= audit_actions


def test_offline_validation_saves_disabled_and_cannot_enable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, monkeypatch, FakeSapRead(unavailable=True))
    initial = service.registry()
    created = service.create(
        _profile(), expected_registry_digest=initial["registry_digest"], actor="tester"
    )
    validated = asyncio.run(service.validate(created["profile"], online=True))
    assert validated["online_validation"]["status"] == "unavailable"
    with pytest.raises(MonthEndProfileError) as caught:
        service.set_enabled(
            "closing-1710",
            True,
            expected_registry_digest=created["registry_digest"],
            expected_profile_digest=created["profile_digest"],
            actor="tester",
            confirmation=ENABLE_CONFIRMATION,
        )
    assert caught.value.code == "month_end_profile_invalid"


def test_update_bumps_patch_invalidates_validation_and_overlap_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, monkeypatch)
    initial = service.registry()
    first = service.create(
        _profile("first"), expected_registry_digest=initial["registry_digest"], actor="tester"
    )
    asyncio.run(service.validate(first["profile"], online=True))
    enabled = service.set_enabled(
        "first",
        True,
        expected_registry_digest=first["registry_digest"],
        expected_profile_digest=first["profile_digest"],
        actor="tester",
        confirmation=ENABLE_CONFIRMATION,
    )
    changed_profile = deepcopy(enabled["profile"])
    changed_profile["thresholds"]["grir_age_days"] = 91
    changed = service.update(
        "first",
        changed_profile,
        expected_registry_digest=enabled["registry_digest"],
        expected_profile_digest=enabled["profile_digest"],
        actor="tester",
    )
    assert changed["profile"]["version"] == "1.0.2"
    assert changed["profile"]["enabled"] is False
    assert service.registry()["profiles"][0]["validation"]["status"] == "not_validated"

    asyncio.run(service.validate(changed["profile"], online=True))
    reenabled = service.set_enabled(
        "first",
        True,
        expected_registry_digest=changed["registry_digest"],
        expected_profile_digest=changed["profile_digest"],
        actor="tester",
        confirmation=ENABLE_CONFIRMATION,
    )
    second = _profile("second")
    created_second = service.create(
        second,
        expected_registry_digest=reenabled["registry_digest"],
        actor="tester",
    )
    asyncio.run(service.validate(created_second["profile"], online=True))
    with pytest.raises(MonthEndProfileError) as caught:
        service.set_enabled(
            "second",
            True,
            expected_registry_digest=created_second["registry_digest"],
            expected_profile_digest=created_second["profile_digest"],
            actor="tester",
            confirmation=ENABLE_CONFIRMATION,
        )
    assert caught.value.code == "month_end_profile_invalid"
    assert caught.value.detail["errors"][0]["code"] == "enabled_effective_range_overlap"


def test_invalid_existing_and_external_registry_are_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    external = tmp_path / "external" / "profiles.json"
    external.parent.mkdir(parents=True)
    external.write_text("{bad", encoding="utf-8")
    monkeypatch.setenv("SAPBA_MONTH_END_PROFILE_PATH", str(external))
    service = MonthEndProfileService(
        repository_root=ROOT,
        data_root=tmp_path / "data",
        database_path=tmp_path / "platform.sqlite3",
        sap_client="100",
        sap_read=FakeSapRead(),
    )
    listing = service.registry()
    assert listing["registry_valid"] is False
    assert listing["editable"] is False
    with pytest.raises(MonthEndProfileError) as caught:
        service.create(_profile(), expected_registry_digest="x", actor="tester")
    assert caught.value.code == "month_end_profile_registry_external_read_only"
    assert external.read_text(encoding="utf-8") == "{bad"


def test_invalid_default_registry_and_failed_atomic_replace_preserve_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, monkeypatch)
    service.path.parent.mkdir(parents=True)
    service.path.write_text("{bad", encoding="utf-8")
    listing = service.registry()
    assert listing["editable"] is False
    with pytest.raises(MonthEndProfileError) as invalid:
        service.create(_profile(), expected_registry_digest="x", actor="tester")
    assert invalid.value.code == "month_end_profile_registry_invalid"
    assert service.path.read_text(encoding="utf-8") == "{bad"

    service.path.write_text(
        json.dumps({"schema_version": 1, "profiles": []}), encoding="utf-8"
    )
    before = service.path.read_bytes()
    digest = service.registry()["registry_digest"]

    def fail_replace(source, target):
        del source, target
        raise OSError("simulated")

    monkeypatch.setattr(profile_module.os, "replace", fail_replace)
    with pytest.raises(MonthEndProfileError) as failed:
        service.create(_profile(), expected_registry_digest=digest, actor="tester")
    assert failed.value.code == "month_end_profile_registry_write_failed"
    assert service.path.read_bytes() == before
    assert not list(service.path.parent.glob("profiles.*.tmp"))


def test_schema_rejects_unknown_fields_and_invalid_evidence_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, monkeypatch)
    profile = _profile()
    profile["sap_password"] = "must-not-be-stored"
    profile["approved_evidence_sources"].append("arbitrary_table")
    result = asyncio.run(service.validate(profile, online=False))
    assert result["valid"] is False
    assert any(item["code"] == "schema_additionalProperties" for item in result["errors"])
    assert any(item["code"] in {"schema_enum", "unknown_evidence_source"} for item in result["errors"])
    assert not service.path.exists()


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        repository_root=ROOT,
        data_root=tmp_path / "data",
        draft_root=tmp_path / "drafts",
        sap_client="100",
        enforce_agent_acceptance=True,
    )


def test_month_end_profile_api_returns_field_errors_and_conflicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SAPBA_MONTH_END_PROFILE_PATH", raising=False)
    app = create_app(_settings(tmp_path), embedded_provider=FakeSapRead())
    with TestClient(app) as client:
        listing = client.get("/api/month-end/profiles").json()
        invalid = _profile()
        invalid["effective_to"] = "2025-01-01"
        validation = client.post(
            "/api/month-end/profiles/validate",
            json={"profile": invalid, "online": False},
        )
        assert validation.status_code == 200
        assert any(item["field"] == "effective_to" for item in validation.json()["errors"])

        created = client.post(
            "/api/month-end/profiles",
            json={
                "profile": _profile(),
                "expected_registry_digest": listing["registry_digest"],
            },
        )
        assert created.status_code == 201
        stale = client.post(
            "/api/month-end/profiles",
            json={
                "profile": _profile("other"),
                "expected_registry_digest": listing["registry_digest"],
            },
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "month_end_profile_conflict"

        created_body = created.json()
        unconfirmed = client.put(
            "/api/month-end/profiles/closing-1710/enabled",
            json={
                "enabled": True,
                "expected_registry_digest": created_body["registry_digest"],
                "expected_profile_digest": created_body["profile_digest"],
            },
        )
        assert unconfirmed.status_code == 403
        assert unconfirmed.json()["detail"]["code"] == "month_end_profile_confirmation_required"
