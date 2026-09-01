from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from docx import Document
from openpyxl import Workbook
from pptx import Presentation
from pypdf import PdfWriter
from fastapi.testclient import TestClient

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.manifests import AgentRepository, is_agent_executable
from sap_business_agents_platform.role_matching import RoleMatchingService
from sap_business_agents_platform.role_matching_documents import load_chunks, preflight_scan, scan_and_extract


def _write_documents(root: Path) -> None:
    (root / "process.md").write_text("采购专员在SAP中创建采购订单。", encoding="utf-8")
    document = Document()
    document.add_heading("P2P", level=1)
    document.add_paragraph("应付会计核对供应商发票。")
    document.save(root / "process.docx")
    workbook = Workbook()
    workbook.active.title = "Operations"
    workbook.active.append(["岗位", "操作"])
    workbook.active.append(["销售", "查询销售订单交货状态"])
    workbook.save(root / "process.xlsx")
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    box = slide.shapes.add_textbox(0, 0, 100, 100)
    box.text = "仓库主管核对收货"
    presentation.save(root / "process.pptx")
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with (root / "scanned.pdf").open("wb") as stream:
        writer.write(stream)
    (root / "legacy.doc").write_bytes(b"legacy")


def test_document_reader_tracks_locations_and_unsupported_files(tmp_path: Path) -> None:
    root = tmp_path / "documents"
    root.mkdir()
    _write_documents(root)
    preflight = preflight_scan(
        [str(root)], max_files=20, max_file_bytes=10_000_000, max_total_bytes=50_000_000
    )
    assert preflight["supported_file_count"] == 5
    assert preflight["ready"] is True
    result = scan_and_extract(
        [str(root)], cache_root=tmp_path / "cache", max_files=20,
        max_file_bytes=10_000_000, max_total_bytes=50_000_000,
    )
    parsed = {item["extension"]: item for item in result["documents"] if item["status"] == "parsed"}
    assert {".md", ".docx", ".xlsx", ".pptx"} <= set(parsed)
    assert load_chunks(parsed[".docx"])[0]["locator"]["kind"] == "docx_paragraph"
    assert load_chunks(parsed[".xlsx"])[0]["locator"]["sheet"] == "Operations"
    assert load_chunks(parsed[".pptx"])[0]["locator"]["slide"] == 1
    assert {item["code"] for item in result["issues"]} >= {
        "legacy_office_format_unsupported", "pdf_text_layer_unavailable"
    }
    assert result["extraction_complete"] is False


def test_incremental_document_scan_reuses_unchanged_parser_cache(tmp_path: Path) -> None:
    source = tmp_path / "process.txt"
    source.write_text("SAP收货操作", encoding="utf-8")
    first = scan_and_extract(
        [str(source)], cache_root=tmp_path / "cache", max_files=5,
        max_file_bytes=1000, max_total_bytes=1000,
    )
    reused = scan_and_extract(
        [str(source)], cache_root=tmp_path / "cache", max_files=5,
        max_file_bytes=1000, max_total_bytes=1000,
        reuse_by_hash={first["documents"][0]["sha256"]: first["documents"][0]},
    )
    assert reused["documents"][0]["reused"] is True


class _FakeRuntime:
    current_provider_id = "codex"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def supports(self, operation: str) -> bool:
        return operation in {"analyze_role_matching", "review_role_matching_feedback"}

    def snapshot(self, provider_id: str | None = None) -> dict:
        return {"provider_id": provider_id or "codex", "sdk_id": "codex-python-sdk", "version": "test", "configuration_digest": "test", "capabilities": ["role_matching"]}

    @contextmanager
    def pin(self, provider_id: str | None):
        yield

    async def cancel(self, thread_id: str | None = None) -> None:
        return None

    async def analyze_role_matching(self, **kwargs):
        self.calls.append(kwargs)
        document = kwargs["documents"][0]
        chunk = document["chunks"][0]
        ref = {"document_id": document["document_id"], "chunk_id": chunk["chunk_id"], "locator": chunk["locator"]}
        return {
            "thread_id": "thread_role_test",
            "analysis": {
                "summary": {"zh": "识别到采购操作", "en": "Procurement operation identified"},
                "roles": [{"name": "采购专员", "evidence_refs": [ref]}],
                "processes": [{"name": "P2P", "evidence_refs": [ref]}],
                "operations": [{"operation_id": "op-1", "role": "采购专员", "department": "采购", "process": "P2P", "name": "查询采购订单", "description": "查询订单状态", "trigger": "日常", "inputs": [], "outputs": [], "sap_system_or_module": "MM", "frequency": "", "controls": [], "evidence_refs": [ref]}],
                "agent_matches": [{"operation_id": "op-1", "agent_id": "procure-to-pay-status", "coverage": "full", "confidence": "high", "reason": "流程一致", "uncovered_capabilities": [], "evidence_refs": [ref]}, {"operation_id": "op-1", "agent_id": "invented-agent", "coverage": "full", "confidence": "high", "reason": "invalid", "evidence_refs": [ref]}],
                "workflow_suggestions": [], "agent_gaps": [], "document_issues": [],
                "non_sap_operation_count": 0,
            },
        }

    async def review_role_matching_feedback(self, **kwargs):
        return await self.analyze_role_matching(**kwargs)


class _Drafts:
    def create(self, *args, **kwargs):  # pragma: no cover - suggestions are empty here
        raise AssertionError("not expected")


async def _role_matching_session_scenario(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    selected = tmp_path / "selected"
    selected.mkdir()
    secret_text = "采购专员在SAP中查询采购订单 UNIQUE_BODY_74219"
    (selected / "p2p.txt").write_text(secret_text, encoding="utf-8")
    settings = replace(
        Settings(), repository_root=repository_root, data_root=tmp_path / "data",
        draft_root=tmp_path / "drafts",
    )
    store = RunStore(settings.database_path)
    runtime = _FakeRuntime()
    service = RoleMatchingService(settings, store, AgentRepository(repository_root / "agents"), runtime, _Drafts())
    await service.start()
    try:
        session = await service.create(paths=[str(selected)], locale="zh", consent=True)
        for _ in range(100):
            current = service.get(session["session_id"])
            if current["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.02)
        assert current["status"] == "completed", current.get("error")
        revision = service.revision(session["session_id"], 1)
        assert revision["result"]["agent_matches"][0]["agent_id"] == "procure-to-pay-status"
        assert len(revision["result"]["agent_matches"]) == 1
        assert runtime.calls and all("paths" not in document for document in runtime.calls[0]["documents"])
        assert secret_text.encode("utf-8") not in settings.database_path.read_bytes()
        assert all("path" not in json.dumps(event["data"]).lower() for event in store.role_matching_events_after(session["session_id"]))
        original_digest = revision["result_digest"]
        await service.feedback(session["session_id"], base_revision=1, message="聚焦采购岗位", mode="incremental", added_paths=[], excluded_document_ids=[])
        for _ in range(100):
            current = service.get(session["session_id"])
            if current["status"] in {"completed", "failed"} and current["current_revision"] == 2:
                break
            await asyncio.sleep(0.02)
        assert service.revision(session["session_id"], 1)["result_digest"] == original_digest
        assert service.revision(session["session_id"], 2)["parent_revision"] == 1
        assert service.documents(session["session_id"])[0].get("reused") is None  # internal hint is not contractual
    finally:
        await service.stop()


def test_role_matching_session_is_immutable_and_does_not_persist_body(tmp_path: Path) -> None:
    asyncio.run(_role_matching_session_scenario(tmp_path))


def test_role_matching_assistant_is_not_executable_or_composable() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = AgentRepository(root / "agents").get("role-agent-matching")
    assert manifest["kind"] == "platform_assistant"
    assert manifest["assistant"]["composable"] is False
    assert is_agent_executable(manifest) is False


def test_workflow_suggestions_use_only_executable_agents_and_compiler_v4(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    settings = replace(
        Settings(), repository_root=repository_root, data_root=tmp_path / "data",
        draft_root=tmp_path / "drafts",
    )
    service = RoleMatchingService(
        settings,
        RunStore(settings.database_path),
        AgentRepository(repository_root / "agents"),
        _FakeRuntime(),
        _Drafts(),
    )
    catalog = service._catalog()
    valid = {
        "suggestion_id": "wf-p2p",
        "title": {"zh": "采购订单跟踪", "en": "Purchase order tracking"},
        "description": "Trace purchase-order business status.",
        "stages": [
            {
                "id": "trace_purchase_order",
                "capability": {"zh": "跟踪采购订单", "en": "Trace purchase order"},
                "agent_id": "procure-to-pay-status",
                "confidence": "high",
                "reason": "The agent provides the required P2P evidence chain.",
                "bindings": [],
                "requested_outputs": [
                    "po_results", "business_status", "source_complete",
                    "evidence_complete", "business_report",
                ],
            }
        ],
    }
    compiled, issues = service._compile_suggestions(
        "rolesession_12345678", [valid], catalog, "zh"
    )
    assert issues == []
    assert compiled[0]["compiler_version"] == 4
    assert compiled[0]["compiled_workflow"]["readOnly"] is True
    assert compiled[0]["compiled_workflow"]["nodes"][0]["agentId"] == "procure-to-pay-status"

    blocked = {
        **valid,
        "suggestion_id": "wf-blocked",
        "stages": [{**valid["stages"][0], "agent_id": "billing-output-monitor"}],
    }
    rejected, blocked_issues = service._compile_suggestions(
        "rolesession_12345678", [blocked], catalog, "zh"
    )
    assert rejected == []
    assert blocked_issues


def test_role_matching_api_preflight_and_session(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    source = tmp_path / "role.txt"
    source.write_text("SAP采购专员查询采购订单", encoding="utf-8")
    settings = replace(
        Settings(), repository_root=repository_root, data_root=tmp_path / "data",
        draft_root=tmp_path / "drafts", free_query_runtime="planner_legacy",
    )
    app = create_app(settings, planner=_FakeRuntime())
    with TestClient(app) as client:
        preflight = client.post("/api/role-matching/preflight", json={"paths": [str(source)]})
        assert preflight.status_code == 200
        assert preflight.json()["ready"] is True
        rejected = client.post(
            "/api/role-matching/sessions",
            json={"paths": [str(source)], "locale": "zh", "consentToRuntime": False},
        )
        assert rejected.status_code == 422
        response = client.post(
            "/api/role-matching/sessions",
            json={"paths": [str(source)], "locale": "zh", "consentToRuntime": True},
        )
        assert response.status_code == 202, response.text
        session_id = response.json()["session_id"]
        for _ in range(100):
            current = client.get(f"/api/role-matching/sessions/{session_id}").json()
            if current["status"] in {"completed", "failed"}:
                break
            import time
            time.sleep(0.02)
        assert current["status"] == "completed", current.get("error")
        assert client.get(f"/api/role-matching/sessions/{session_id}/revisions/1").status_code == 200
        assert client.get(f"/api/role-matching/sessions/{session_id}/revisions/1/report.md").status_code == 200
        json_report = client.get(
            f"/api/role-matching/sessions/{session_id}/revisions/1/report.json"
        )
        assert json_report.status_code == 200
        assert json_report.headers["content-disposition"].endswith(
            'filename="role-matching-report.json"'
        )
