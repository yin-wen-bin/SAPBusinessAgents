"""Validate restricted-artifact UI/API controls against an isolated real run.

The report contains only booleans, counts, identifiers already present in the
public result, and digests.  Revealed business values are held in memory only
long enough to prove that they are absent from default responses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from sap_business_agents_platform.acceptance import agent_execution_digest, canonical_hash
from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings


JsonObject = dict[str, Any]
SECURITY_HEADERS = {
    "cache-control": "no-store",
    "content-security-policy": "sandbox",
    "x-content-type-options": "nosniff",
}


def _load(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _headers_are_safe(response) -> bool:
    return all(response.headers.get(name) == value for name, value in SECURITY_HEADERS.items())


def _sensitive_values(rows: list[JsonObject]) -> list[str]:
    names = {
        "payer_name", "bank_reference", "document_reference_id",
        "one_time_account", "bank_account", "bank_account_iban",
    }
    values: list[str] = []
    for row in rows:
        for key, value in row.items():
            if str(key).casefold() not in names:
                continue
            text = str(value or "").strip()
            if len(text) >= 3:
                values.append(text)
    return list(dict.fromkeys(values))


def _contains_any(payload: Any, values: list[str]) -> bool:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return any(value in serialized for value in values)


def _fixed_run_identity(payload: JsonObject) -> tuple[str, str, str]:
    """Accept a standalone fixed result or an immutable acceptance artifact."""
    fixed = payload.get("fixed_agent")
    if isinstance(fixed, dict):
        case = payload.get("case") if isinstance(payload.get("case"), dict) else {}
        hashes = payload.get("hashes") if isinstance(payload.get("hashes"), dict) else {}
        return (
            str(fixed.get("run_id") or ""),
            str(case.get("agent_id") or ""),
            str(hashes.get("agent_execution_digest") or ""),
        )
    return (
        str(payload.get("run_id") or ""),
        str(payload.get("agent_id") or ""),
        str(payload.get("agent_execution_digest") or ""),
    )


def validate(
    *,
    repository: Path,
    data_root: Path,
    fixed_result_path: Path,
    agent_snapshot_path: Path,
    rules_source_path: Path | None,
    output: Path,
    local_origin: str,
    destructive: bool,
    locales_verified: bool,
) -> JsonObject:
    fixed_result = _load(fixed_result_path)
    manifest = _load(agent_snapshot_path)
    rules_source = (
        rules_source_path.read_text(encoding="utf-8") if rules_source_path else None
    )
    run_id, result_agent_id, result_digest = _fixed_run_identity(fixed_result)
    if not run_id or result_agent_id != manifest.get("slug"):
        raise ValueError("fixed result does not match the candidate Agent")
    expected_digest = agent_execution_digest(manifest, rules_source)
    if result_digest and result_digest != expected_digest:
        raise ValueError("fixed result execution digest does not match the candidate Agent")

    settings = replace(
        Settings.from_env(repository),
        data_root=data_root,
        draft_root=data_root / "frontend-drafts",
        local_ui_origins=(local_origin,),
        enforce_agent_acceptance=False,
    )
    app = create_app(settings)
    checks: dict[str, bool] = {}
    with TestClient(app) as client:
        public_run = client.get(f"/api/runs/{run_id}")
        if public_run.status_code != 200:
            raise ValueError("isolated API cannot read the fixed-Agent run")
        artifact_rows = app.state.store.list_structured_artifacts(run_id)
        available = [item for item in artifact_rows if not item.get("deleted_at")]
        if len(available) != 1:
            raise ValueError("frontend case must contain exactly one available restricted artifact")
        artifact = available[0]
        artifact_id = str(artifact["artifact_id"])
        path = f"/api/runs/{run_id}/structured-artifacts/{artifact_id}"
        origin = {"Origin": local_origin}

        checks["non_local_origin_rejected"] = client.get("/api/security/csrf").status_code == 403
        csrf_response = client.get("/api/security/csrf", headers=origin)
        checks["csrf_response_headers"] = (
            csrf_response.status_code == 200 and _headers_are_safe(csrf_response)
        )
        csrf = str((csrf_response.json() if csrf_response.status_code == 200 else {}).get("csrf_token") or "")
        bad_csrf = client.post(
            path + "/reveal",
            json={"operation": "rows"},
            headers={**origin, "X-SAPBA-CSRF": "invalid"},
        )
        checks["invalid_csrf_rejected"] = bad_csrf.status_code == 403

        reveal = client.post(
            path + "/reveal",
            json={"operation": "rows"},
            headers={**origin, "X-SAPBA-CSRF": csrf},
        )
        checks["rows_reveal_authorized"] = reveal.status_code == 200 and _headers_are_safe(reveal)
        rows_token = str((reveal.json() if reveal.status_code == 200 else {}).get("token") or "")
        rows_response = client.get(
            path + "/rows?offset=0&limit=200",
            headers={**origin, "X-SAPBA-Reveal-Token": rows_token},
        )
        revealed_rows = (
            rows_response.json().get("rows")
            if rows_response.status_code == 200 and isinstance(rows_response.json(), dict)
            else []
        )
        if not isinstance(revealed_rows, list) or any(not isinstance(item, dict) for item in revealed_rows):
            raise ValueError("restricted rows response is invalid")
        sensitive = _sensitive_values(revealed_rows)
        checks["rows_response_headers"] = rows_response.status_code == 200 and _headers_are_safe(rows_response)
        checks["artifact_row_count_matches"] = (
            rows_response.status_code == 200
            and rows_response.json().get("total_rows") == artifact.get("row_count")
        )
        checks["default_run_has_no_sensitive_values"] = not _contains_any(public_run.json(), sensitive)
        public_events = [item.model_dump(mode="json") for item in app.state.store.events_after(run_id)]
        checks["events_have_no_sensitive_values"] = not _contains_any(public_events, sensitive)

        replay = client.get(
            path + "/rows",
            headers={**origin, "X-SAPBA-Reveal-Token": rows_token},
        )
        checks["rows_token_one_time"] = replay.status_code == 401

        cross_operation = client.post(
            path + "/reveal",
            json={"operation": "rows"},
            headers={**origin, "X-SAPBA-CSRF": csrf},
        )
        cross_token = str(cross_operation.json().get("token") or "")
        cross_response = client.get(
            path + "/download",
            headers={**origin, "X-SAPBA-Reveal-Token": cross_token},
        )
        checks["cross_operation_token_rejected"] = cross_response.status_code == 401

        cross_run = client.post(
            path + "/reveal",
            json={"operation": "rows"},
            headers={**origin, "X-SAPBA-CSRF": csrf},
        )
        cross_run_token = str(cross_run.json().get("token") or "")
        cross_run_response = client.get(
            f"/api/runs/run_other/structured-artifacts/{artifact_id}/rows",
            headers={**origin, "X-SAPBA-Reveal-Token": cross_run_token},
        )
        checks["cross_run_token_rejected"] = cross_run_response.status_code in {401, 404}

        invalid_cursor_reveal = client.post(
            path + "/reveal",
            json={"operation": "rows"},
            headers={**origin, "X-SAPBA-CSRF": csrf},
        )
        invalid_cursor_response = client.get(
            path + "/rows?cursor=invalid",
            headers={
                **origin,
                "X-SAPBA-Reveal-Token": str(invalid_cursor_reveal.json().get("token") or ""),
            },
        )
        checks["invalid_cursor_rejected"] = invalid_cursor_response.status_code == 400

        expired_token = secrets.token_urlsafe(32)
        app.state.store.save_artifact_reveal_token(
            token_hash=hashlib.sha256(expired_token.encode("utf-8")).hexdigest(),
            run_id=run_id,
            artifact_id=artifact_id,
            operation="rows",
            artifact_sha256=str(artifact["sha256"]),
            expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        )
        expired_response = client.get(
            path + "/rows",
            headers={**origin, "X-SAPBA-Reveal-Token": expired_token},
        )
        checks["expired_token_rejected"] = expired_response.status_code == 401

        download_reveal = client.post(
            path + "/reveal",
            json={"operation": "download"},
            headers={**origin, "X-SAPBA-CSRF": csrf},
        )
        download_response = client.get(
            path + "/download",
            headers={
                **origin,
                "X-SAPBA-Reveal-Token": str(download_reveal.json().get("token") or ""),
            },
        )
        checks["download_response_headers"] = (
            download_response.status_code == 200
            and _headers_are_safe(download_response)
            and download_response.content.startswith(b"\xef\xbb\xbf")
        )
        formula_values = [value for value in sensitive if value.startswith(("=", "+", "-", "@"))]
        checks["csv_formula_injection_prevented"] = all(
            ("'" + value) in download_response.text for value in formula_values
        )

        checks["locales_manually_verified"] = locales_verified
        if destructive:
            deleted = client.request(
                "DELETE",
                path,
                json={"reason": "acceptance_destruction_run"},
                headers={**origin, "X-SAPBA-CSRF": csrf},
            )
            checks["artifact_deleted"] = deleted.status_code == 200 and bool(deleted.json().get("deleted_at"))
            deleted_reveal = client.post(
                path + "/reveal",
                json={"operation": "rows"},
                headers={**origin, "X-SAPBA-CSRF": csrf},
            )
            checks["deleted_artifact_returns_gone"] = deleted_reveal.status_code == 410
            tombstone = app.state.store.get_structured_artifact(run_id, artifact_id)
            checks["deletion_tombstone_has_no_business_values"] = (
                bool(tombstone.get("deleted_at"))
                and not _contains_any(tombstone, sensitive)
            )

    payload = {
        "verdict": "PASS" if checks and all(checks.values()) else "FAIL",
        "agent_id": str(manifest["slug"]),
        "agent_version": str(manifest["version"]),
        "agent_execution_digest": expected_digest,
        "fixed_agent_run_id": run_id,
        "locales": {"zh": "PASS" if locales_verified else "FAIL", "en": "PASS" if locales_verified else "FAIL"},
        "checks": checks,
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "report_digest": "",
    }
    payload["report_digest"] = canonical_hash({key: value for key, value in payload.items() if key != "report_digest"})
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an isolated real restricted-artifact run.")
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--fixed-result", type=Path, required=True)
    parser.add_argument("--agent-snapshot", type=Path, required=True)
    parser.add_argument("--rules-source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--local-origin", default="http://127.0.0.1:4322")
    parser.add_argument("--destructive", action="store_true")
    parser.add_argument("--locales-verified", action="store_true")
    args = parser.parse_args()
    result = validate(
        repository=args.repository.resolve(),
        data_root=args.data_root.resolve(),
        fixed_result_path=args.fixed_result.resolve(),
        agent_snapshot_path=args.agent_snapshot.resolve(),
        rules_source_path=args.rules_source.resolve() if args.rules_source else None,
        output=args.output.resolve(),
        local_origin=args.local_origin,
        destructive=args.destructive,
        locales_verified=args.locales_verified,
    )
    print(json.dumps({"verdict": result["verdict"], "check_count": len(result["checks"])}))
    return 0 if result["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
