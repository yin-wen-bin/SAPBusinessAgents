"""Fresh direct reads prove the frozen baseline did not change during a case.

No Agent, Skill, or platform Provider is imported. Public anchor summaries
contain only hashes and counts. Exact replay contracts and all rows stay in
the encrypted direct-reader artifacts.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.direct_sap_read import run as direct_read, read_encrypted_rows, _hash_json
from scripts.direct_ar_adt_snapshot import capture as direct_adt_read


def anchor_value(source):
    fields = ("source_id", "schema_hash", "query_hash", "rows_hash", "row_count", "stable_order_by")
    if source.get("source_complete") is not True or source.get("paging_complete") is not True:
        raise ValueError("source_anchor_incomplete")
    if any(source.get(name) is None for name in fields):
        raise ValueError("source_anchor_metadata_missing")
    return {name: source[name] for name in fields}


def validate_frozen_source(source, manifest, rows):
    if not source.get("source_snapshot_hash") or _hash_json(rows) != source["source_snapshot_hash"]:
        raise ValueError("source_snapshot_digest_mismatch")
    if manifest.get("rows_hash") != source["source_snapshot_hash"]:
        raise ValueError("source_snapshot_digest_mismatch")
    for name in ("source_id", "schema_hash", "query_hash", "row_count", "stable_order_by"):
        if manifest.get(name) != source.get(name):
            raise ValueError("source_snapshot_metadata_mismatch")
    return anchor_value(manifest)


def capture(baseline, profile, output):
    if output.exists():
        raise ValueError("source_anchor_artifact_immutable")
    sources = baseline.get("sources")
    if not isinstance(sources, list) or not sources or baseline.get("supplemental_sources"):
        raise ValueError("source_anchor_coverage_missing")
    prepared, identities = [], set()
    # Preflight EVERY source before any network access. An unanchorable source
    # must not silently disappear from an otherwise successful anchor set.
    for source in sources:
        access_method = source.get("access_method")
        expected_method = "GET" if access_method == "odata_get" else "POST"
        if access_method not in {"odata_get", "adt_data_preview"} or source.get("http_method") != expected_method:
            raise ValueError("source_anchor_method_not_supported")
        if not source.get("source_snapshot_ref"):
            raise ValueError("source_snapshot_missing")
        identity = source.get("source_id")
        if not identity or identity in identities:
            raise ValueError("source_anchor_identity_ambiguous")
        identities.add(identity)
        snapshot = Path(source["source_snapshot_ref"])
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        frozen = validate_frozen_source(source, manifest, read_encrypted_rows(snapshot))
        if access_method == "odata_get":
            replay = read_encrypted_rows(snapshot / "replay")
            if len(replay) != 1 or set(replay[0]) != {"request"}:
                raise ValueError("source_anchor_replay_invalid")
            request = replay[0]["request"]
            if not isinstance(request, dict) or request.get("source_id") != identity:
                raise ValueError("source_anchor_replay_invalid")
            if _hash_json({k: v for k, v in request.items() if k != "source_id"}) != frozen["query_hash"]:
                raise ValueError("source_anchor_replay_drift")
        else:
            request = manifest.get("spec")
            if not isinstance(request, dict) or manifest.get("query_hash") != frozen["query_hash"]:
                raise ValueError("source_anchor_replay_invalid")
        prepared.append((access_method, request, frozen))
    output.mkdir(parents=True)
    observed, expected = [], []
    for index, (access_method, request, frozen) in enumerate(prepared):
        if access_method == "odata_get":
            fresh = direct_read(profile, request, output / str(index), encrypt_rows=True)
        else:
            fresh = direct_adt_read(profile, request, output / str(index))
        observed.append(anchor_value(fresh))
        expected.append(frozen)
    result = {
        "schema_version": "1.0",
        "baseline_hash": _hash_json(baseline),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "expected": _hash_json(expected),
        "observed": _hash_json(observed),
        "sources": observed,
        "verdict": "PASS" if observed == expected else "CHANGED",
    }
    with (output / "anchor.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    return result


def summarize(baseline, before, after):
    baseline_hash = _hash_json(baseline)
    valid = all(x.get("baseline_hash") == baseline_hash and x.get("verdict") == "PASS"
                and x.get("observed") == x.get("expected") for x in (before, after))
    valid = valid and before.get("observed") == after.get("observed")
    return {
        "verdict": "PASS" if valid else "CHANGED",
        "baseline_hash": baseline_hash,
        "before": before.get("observed"), "after": after.get("observed"),
        "before_observed_at": before.get("observed_at"),
        "after_observed_at": after.get("observed_at"),
        "reason_code": None if valid else "sap_source_changed_during_acceptance",
    }
