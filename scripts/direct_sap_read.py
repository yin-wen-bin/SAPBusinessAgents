from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path
from typing import Any


NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
V2_PATH = re.compile(r"^/sap/opu/odata/sap/[A-Z0-9_]+$")
V4_PATH = re.compile(r"^/sap/opu/odata4/[A-Za-z0-9_./-]+$")


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _hash_json(value: Any) -> str:
    return _hash_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _bool(value: Any, default: bool = True) -> bool:
    if value in {None, ""}:
        return default
    return str(value).strip().casefold() not in {"0", "false", "no", "off"}


def _schema(metadata: bytes, entity_set: str) -> tuple[dict[str, str], list[str]]:
    root = ET.fromstring(metadata)
    entity_type_name = ""
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "EntitySet" and element.attrib.get("Name") == entity_set:
            entity_type_name = str(element.attrib.get("EntityType") or "").rsplit(".", 1)[-1]
            break
    if not entity_type_name:
        raise ValueError(f"entity set {entity_set!r} is absent from live metadata")
    fields: dict[str, str] = {}
    keys: list[str] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "EntityType" or element.attrib.get("Name") != entity_type_name:
            continue
        for child in element:
            local = child.tag.rsplit("}", 1)[-1]
            if local == "Property" and child.attrib.get("Name"):
                edm_type = str(child.attrib.get("Type") or "Edm.String")
                display_format = next(
                    (
                        str(value)
                        for key, value in child.attrib.items()
                        if key.rsplit("}", 1)[-1] in {"display-format", "DisplayFormat"}
                    ),
                    "",
                )
                fields[str(child.attrib["Name"])] = (
                    f"{edm_type}:{display_format}" if display_format else edm_type
                )
            if local == "Key":
                keys.extend(
                    str(item.attrib["Name"])
                    for item in child
                    if item.attrib.get("Name")
                )
        break
    if not fields or not keys:
        raise ValueError(f"live metadata for {entity_set!r} has no fields or stable key")
    return fields, keys


def _sort_value(value: Any, edm_type: str) -> Any:
    if value in {None, ""}:
        return (0, "")
    base_type = edm_type.split(":", 1)[0]
    if base_type in {
        "Edm.Byte", "Edm.SByte", "Edm.Int16", "Edm.Int32", "Edm.Int64",
        "Edm.Decimal", "Edm.Double", "Edm.Single",
    } or edm_type.endswith(":NonNegative"):
        return (1, Decimal(str(value)))
    # Several SAP document identifiers are exposed as Edm.String but the
    # Gateway orders their digit-only values numerically. Preserve the raw
    # text as a tie-breaker so leading-zero variants remain distinct.
    text = str(value)
    if base_type == "Edm.String" and text.isdigit():
        return (1, Decimal(text), text)
    return (1, text)


def _ensure_stable_artifact_order(
    rows: list[dict[str, Any]],
    ordered: list[str],
    fields: dict[str, str],
    *,
    page_count: int,
    paging_complete: bool,
) -> tuple[list[dict[str, Any]], bool]:
    raw_values = [tuple(str(row.get(key) or "") for key in ordered) for row in rows]
    if len(raw_values) != len(set(raw_values)):
        raise ValueError("stable paging key contains duplicates")
    stable_values = [
        tuple(_sort_value(row.get(key), fields[key]) for key in ordered)
        for row in rows
    ]
    if stable_values == sorted(stable_values):
        return rows, False
    # Some SAP analytical entities explicitly mark all properties as
    # non-sortable and ignore $orderby. If the entire bounded result arrived
    # in one exhausted page, client sorting makes the saved artifact
    # deterministic without weakening paging completeness. Multi-page or
    # truncated reads still fail closed because their page boundary is not
    # proven stable.
    if page_count == 1 and paging_complete:
        return (
            sorted(
                rows,
                key=lambda row: tuple(
                    _sort_value(row.get(key), fields[key]) for key in ordered
                ),
            ),
            True,
        )
    first = next(
        index
        for index in range(1, len(stable_values))
        if stable_values[index] < stable_values[index - 1]
    )
    differing = next(
        field
        for field, previous, current in zip(
            ordered, stable_values[first - 1], stable_values[first]
        )
        if previous != current
    )
    raise ValueError(
        "stable paging key is non-monotonic at row "
        f"{first + 1}; first differing field={differing}; edm_type={fields[differing]}"
    )


def _validate_request(value: dict[str, Any]) -> dict[str, Any]:
    required = {
        "source_id", "service_name", "service_path", "odata_version", "entity_set",
        "select_fields", "filter", "order_by", "page_size", "max_rows",
    }
    if set(value) != required:
        raise ValueError("request has unexpected or missing fields")
    if value["odata_version"] not in {"2.0", "4.0"}:
        raise ValueError("odata_version must be 2.0 or 4.0")
    path_pattern = V2_PATH if value["odata_version"] == "2.0" else V4_PATH
    if not path_pattern.fullmatch(str(value["service_path"])):
        raise ValueError("service_path is outside the approved SAP OData path family")
    for field in ("source_id", "service_name", "entity_set"):
        if not NAME.fullmatch(str(value[field])):
            raise ValueError(f"{field} is invalid")
    for field in ("select_fields", "order_by"):
        items = value[field]
        if not isinstance(items, list) or not items or any(not NAME.fullmatch(str(item)) for item in items):
            raise ValueError(f"{field} must be a non-empty field-name array")
    if not isinstance(value["filter"], str) or not value["filter"].strip():
        raise ValueError("an explicit bounded OData filter is required")
    for field in ("page_size", "max_rows"):
        if not isinstance(value[field], int) or isinstance(value[field], bool) or value[field] < 1:
            raise ValueError(f"{field} must be a positive integer")
    if value["page_size"] > 5000 or value["max_rows"] > 30000:
        raise ValueError("page_size/max_rows exceeds the direct-read safety limit")
    return value


def _client(profile: dict[str, Any]):
    if profile.get("read_only") is not True:
        raise ValueError("Codex direct SAP profile must declare read_only=true")
    base = str(profile.get("base_url") or "").rstrip("/")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Codex direct SAP base URL is invalid")
    token = base64.b64encode(
        f"{profile.get('username')}:{profile.get('password')}".encode()
    ).decode()
    headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}
    context = ssl.create_default_context()
    if not _bool(profile.get("verify_ssl"), True):
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    timeout = max(1, int(profile.get("timeout_ms") or 90000) // 1000)

    def get(url: str, *, accept: str = "application/json") -> bytes:
        request = urllib.request.Request(url, headers={**headers, "Accept": accept}, method="GET")
        with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
            return response.read()

    return base, str(profile.get("client") or ""), get


def run(profile: dict[str, Any], request: dict[str, Any], output: Path) -> dict[str, Any]:
    request = _validate_request(request)
    base, client, get = _client(profile)
    service_root = base + str(request["service_path"])
    metadata_url = service_root + "/$metadata?" + urllib.parse.urlencode({"sap-client": client})
    metadata = get(metadata_url, accept="application/xml")
    fields, stable_keys = _schema(metadata, str(request["entity_set"]))
    selected = [str(item) for item in request["select_fields"]]
    ordered = [str(item) for item in request["order_by"]]
    missing = sorted((set(selected) | set(ordered)) - set(fields))
    if missing:
        raise ValueError(f"live metadata is missing requested fields: {missing!r}")
    if not set(stable_keys).issubset(ordered):
        raise ValueError(f"order_by must include every live stable key: {stable_keys!r}")
    params = {
        "$format": "json",
        "$select": ",".join(selected),
        "$filter": request["filter"],
        "$orderby": ",".join(f"{field} asc" for field in ordered),
        # OData $top is a total result cap, not a client page-size hint. Asking
        # for the full bounded allowance lets server-driven paging retain its
        # own next-link while preventing a one-page $top from being mistaken
        # for source completeness.
        "$top": str(request["max_rows"]),
        "sap-client": client,
    }
    url = service_root + "/" + str(request["entity_set"]) + "?" + urllib.parse.urlencode(params)
    expected = urllib.parse.urlparse(service_root)
    rows: list[dict[str, Any]] = []
    pages = 0
    paging_complete = True
    while url:
        parsed = urllib.parse.urlparse(url)
        if (
            parsed.scheme != expected.scheme
            or parsed.netloc != expected.netloc
            or not parsed.path.startswith(expected.path.rstrip("/") + "/")
        ):
            raise ValueError("OData next-link escaped the exact service root")
        pages += 1
        if pages > 1000:
            raise ValueError("page guard exceeded")
        payload = json.loads(get(url))
        if request["odata_version"] == "2.0":
            container = payload.get("d") or {}
            batch = container.get("results") or []
            next_link = container.get("__next")
        else:
            batch = payload.get("value") or []
            next_link = payload.get("@odata.nextLink")
        rows.extend(dict(item) for item in batch if isinstance(item, dict))
        if len(rows) >= int(request["max_rows"]):
            # Exactly reaching the declared ceiling is still bounded: without
            # a count or a provably exhausted next-link we cannot know whether
            # an additional source row exists.
            paging_complete = False
            rows = rows[: int(request["max_rows"])]
            break
        url = urllib.parse.urljoin(service_root.rstrip("/") + "/", str(next_link)) if next_link else ""
    rows, client_sorted = _ensure_stable_artifact_order(
        rows,
        ordered,
        fields,
        page_count=pages,
        paging_complete=paging_complete,
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "metadata.edmx").write_bytes(metadata)
    (output / "rows.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        "source_id": request["source_id"],
        "service_name": request["service_name"],
        "odata_version": request["odata_version"],
        "entity_set": request["entity_set"],
        "schema_hash": _hash_bytes(metadata),
        "query_hash": _hash_json({key: request[key] for key in request if key != "source_id"}),
        "row_count": len(rows),
        "page_count": pages,
        "stable_order_by": ordered,
        "client_sorted": client_sorted,
        "paging_complete": paging_complete,
        "source_complete": paging_complete,
        "primary": False,
        "http_method": "GET",
        "rows_hash": _hash_json(rows),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent Codex GET-only SAP OData reader.")
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path.home() / ".codex" / "secure" / "sap-direct-readonly.json",
    )
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = run(
        _load_object(args.profile.resolve()),
        _load_object(args.request.resolve()),
        args.output.resolve(),
    )
    print(json.dumps({key: manifest[key] for key in ("source_id", "row_count", "page_count", "paging_complete", "source_complete", "schema_hash", "query_hash")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
