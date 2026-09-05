"""Independent, bounded AR source capture. No Skill or platform Provider imports.

Only the three reviewed AR sources and read-only ADT Data Preview endpoint are
reachable. Raw response rows are encrypted immediately; diagnostics are codes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests

from scripts.direct_sap_read import write_encrypted_rows

ENDPOINT = "/sap/bc/adt/datapreview/freestyle"
ALLOWED = {
    "I_ARBANKSTATEMENTITEM": ("CompanyCode", "BankStatementShortID", "BankStatementItem"),
    "MHND": ("MANDT", "LAUFD", "LAUFI", "KOART", "BUKRS", "KUNNR", "LIFNR", "CPDKY", "SKNRZE", "SMABER", "SMAHSK", "BBUKRS", "BELNR", "GJAHR", "BUZEI"),
    "MHNK": ("MANDT", "LAUFD", "LAUFI", "KOART", "BUKRS", "KUNNR", "LIFNR", "CPDKY", "SKNRZE", "SMABER", "SMAHSK", "BUSAB"),
}
SOURCE_KIND = {
    "I_ARBANKSTATEMENTITEM": "cds",
    "MHND": "table",
    "MHNK": "table",
}
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,60}")


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def literal(value):
    if not isinstance(value, str) or len(value) > 100 or any(ord(char) < 32 for char in value):
        raise ValueError("direct_adt_filter_invalid")
    return "'" + value.replace("'", "''") + "'"


def compile_query(spec):
    if set(spec) != {"object", "fields", "filters", "row_limit"}:
        raise ValueError("direct_adt_contract_invalid")
    obj = spec["object"]
    if obj not in ALLOWED or type(spec["row_limit"]) is not int or not 1 <= spec["row_limit"] <= 10000:
        raise ValueError("direct_adt_source_not_allowed")
    fields = spec["fields"]
    if not isinstance(fields, list) or not fields or len(fields) > 100 or any(
            not isinstance(name, str) or not IDENTIFIER.fullmatch(name) for name in fields):
        raise ValueError("direct_adt_fields_invalid")
    if len(set(name.upper() for name in fields)) != len(fields):
        raise ValueError("direct_adt_fields_invalid")
    if not set(name.upper() for name in ALLOWED[obj]) <= {name.upper() for name in fields}:
        raise ValueError("direct_adt_full_key_required")
    filters = spec["filters"]
    if not isinstance(filters, list) or not filters or len(filters) > 20:
        raise ValueError("direct_adt_filters_required")
    predicates = []
    for item in filters:
        if (not isinstance(item, dict) or set(item) != {"field", "operator", "value"}
                or item["field"] not in fields or item["operator"] not in {"=", "<>", "<=", ">="}):
            raise ValueError("direct_adt_filter_invalid")
        predicates.append(item["field"] + " " + item["operator"] + " " + literal(item["value"]))
    return ("SELECT\n  " + ",\n  ".join(fields) + "\nFROM " + obj
            + "\nWHERE\n  " + "\n  AND ".join(predicates)
            + "\nORDER BY\n  " + ",\n  ".join(ALLOWED[obj]))


def parse_xml(data):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("direct_adt_xml_invalid")
    root = ET.fromstring(data)
    local = lambda tag: tag.rsplit("}", 1)[-1]
    columns, totals = {}, set()
    for element in root.iter():
        for key, value in element.attrib.items():
            if local(key).lower() in {"totalrows", "totalnumberofrows", "numberofrows"} and value.isdigit():
                totals.add(int(value))
        if local(element.tag).lower() in {"totalrows", "totalnumberofrows", "numberofrows"} and (element.text or "").isdigit():
            totals.add(int(element.text))
        if local(element.tag) != "columns":
            continue
        meta = next((child for child in element if local(child.tag) == "metadata"), None)
        if meta is None:
            raise ValueError("direct_adt_columns_invalid")
        name = next((value.upper() for key, value in meta.attrib.items() if local(key) == "name"), "")
        if not name or name in columns:
            raise ValueError("direct_adt_columns_invalid")
        dataset = next((child for child in element if local(child.tag) == "dataSet"), None)
        columns[name] = [] if dataset is None else [(child.text or "") for child in dataset if local(child.tag) == "data"]
    lengths = {len(values) for values in columns.values()}
    if not columns or len(lengths) != 1 or len(totals) != 1:
        raise ValueError("direct_adt_count_unproven")
    total = next(iter(totals))
    if total < next(iter(lengths)):
        raise ValueError("direct_adt_count_unproven")
    rows = [{key: values[index] for key, values in columns.items()} for index in range(next(iter(lengths)))]
    return rows, total


def preview(profile, sql, row_limit):
    """Internal transport for fixed generated SELECTs, never exposed as a CLI SQL option."""
    if profile.get("read_only") is not True:
        raise ValueError("direct_adt_readonly_profile_required")
    base = str(profile["base_url"]).rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("direct_adt_connection_invalid")
    verify = str(profile.get("verify_ssl", True)).lower() not in {"false", "0", "no"}
    kwargs = {"timeout": 90, "verify": verify, "allow_redirects": False}
    session = requests.Session()
    session.trust_env = False
    session.auth = (profile["username"], profile["password"])
    headers = {"Accept": "application/vnd.sap.adt.datapreview.table.v1+xml",
               "X-SAP-Client": str(profile.get("client", "")), "Accept-Language": "en"}
    try:
        token_response = session.get(base + ENDPOINT, headers={**headers, "x-csrf-token": "fetch"}, **kwargs)
        token = token_response.headers.get("x-csrf-token")
        if not token or token_response.status_code not in {200, 204, 405}:
            raise ValueError("direct_adt_csrf_unavailable")
        response = session.post(base + ENDPOINT, params={"rowNumber": row_limit},
                                data=sql.encode("utf-8"), headers={**headers, "x-csrf-token": token,
                                "Content-Type": "text/plain; charset=utf-8"}, **kwargs)
        if response.status_code != 200:
            raise ValueError("direct_adt_http_" + str(response.status_code))
        rows, total = parse_xml(response.content)
    except (requests.RequestException, OSError) as exc:
        raise ValueError("direct_adt_network_" + type(exc).__name__) from None
    finally:
        session.close()
    return rows, total


def read_metadata(profile, object_name):
    """Read live source/DDIC metadata without importing either tested Skill."""
    kind = SOURCE_KIND[object_name]
    prefix = (
        "/sap/bc/adt/ddic/ddl/sources/"
        if kind == "cds"
        else "/sap/bc/adt/ddic/tables/"
    )
    path = prefix + quote(object_name.lower(), safe="") + "/source/main"
    base = str(profile["base_url"]).rstrip("/")
    verify = str(profile.get("verify_ssl", True)).lower() not in {"false", "0", "no"}
    session = requests.Session()
    session.trust_env = False
    session.auth = (profile["username"], profile["password"])
    try:
        response = session.get(
            base + path,
            headers={
                "Accept": "text/plain",
                "X-SAP-Client": str(profile.get("client", "")),
                "Accept-Language": "en",
            },
            timeout=90,
            verify=verify,
            allow_redirects=False,
        )
        if response.status_code == 200 and response.content.strip():
            payload = response.content
            metadata_path = path
        elif response.status_code == 404 and kind == "table":
            escaped = object_name.replace("'", "''")
            sql = (
                "SELECT TABNAME, FIELDNAME, POSITION, KEYFLAG, DATATYPE, LENG, DECIMALS, ROLLNAME\n"
                "FROM DD03L\n"
                f"WHERE TABNAME = '{escaped}' AND AS4LOCAL = 'A' AND AS4VERS = '0000'\n"
                "ORDER BY TABNAME, POSITION, FIELDNAME"
            )
            rows, total = preview(profile, sql, 10000)
            if not rows or total != len(rows):
                raise ValueError("direct_adt_metadata_incomplete")
            payload = json.dumps(
                rows,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            metadata_path = "dd03l-fallback:" + object_name.lower()
        else:
            raise ValueError("direct_adt_metadata_http_" + str(response.status_code))
    except requests.RequestException as exc:
        raise ValueError("direct_adt_metadata_network_" + type(exc).__name__) from None
    finally:
        session.close()
    normalized = payload.decode("utf-8", errors="replace").upper()
    if any(field.upper() not in normalized for field in ALLOWED[object_name]):
        raise ValueError("direct_adt_metadata_key_missing")
    return payload, metadata_path


def capture(profile, spec, output):
    sql = compile_query(spec)
    if output.exists():
        raise ValueError("direct_adt_snapshot_immutable")
    metadata, metadata_path = read_metadata(profile, spec["object"])
    rows, total = preview(profile, sql, spec["row_limit"])
    if rows and set(rows[0]) != {name.upper() for name in spec["fields"]}:
        raise ValueError("direct_adt_projection_changed")
    keys = [tuple(row[key.upper()] for key in ALLOWED[spec["object"]]) for row in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("direct_adt_duplicate_key")
    output.mkdir(parents=True)
    (output / "metadata.source").write_bytes(metadata)
    artifact = write_encrypted_rows(output, rows)
    metadata_sha256 = "sha256:" + hashlib.sha256(metadata).hexdigest()
    rows_hash = digest(rows)
    manifest = {
        "source_id": "adt_" + spec["object"].lower() + "_" + digest(spec)[7:19],
        "object": spec["object"], "access_method": "adt_data_preview", "http_method": "POST",
        "semantic_read_only": True, "endpoint": ENDPOINT, "query_hash": digest(spec),
        "stable_order_by": list(ALLOWED[spec["object"]]), "source_complete": total == len(rows),
        "paging_complete": total == len(rows), "row_count": len(rows), "total_rows": total,
        "observed_at": datetime.now(timezone.utc).isoformat(), "restricted_artifact": artifact,
        "metadata_path": metadata_path, "metadata_sha256": metadata_sha256,
        "schema_hash": metadata_sha256,
        "rows_hash": rows_hash,
        "result_columns_hash": digest(spec["fields"]), "spec": spec,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=Path.home() / ".codex/secure/sap-direct-readonly.json")
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = capture(json.loads(args.profile.read_text(encoding="utf-8")),
                         json.loads(args.spec.read_text(encoding="utf-8")), args.output.resolve())
    except ValueError as exc:
        print(json.dumps({"error_code": str(exc)}))
        return 2
    print(json.dumps({key: result[key] for key in ("object", "row_count", "total_rows", "source_complete", "query_hash")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
