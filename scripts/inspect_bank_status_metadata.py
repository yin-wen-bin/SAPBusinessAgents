"""Read exact DDIC/CDS semantics behind bank status fields; never read business rows."""
import hashlib
import json
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests

from scripts.direct_ar_adt_snapshot import preview, literal, digest


def inspect(profile):
    def read(stage, sql, limit):
        try:
            return preview(profile, sql, limit)
        except ValueError as exc:
            raise ValueError(stage + ":" + str(exc)) from None
    fields, total = read("DD03L", "SELECT ROLLNAME, DOMNAME, DATATYPE, LENG\nFROM DD03L\nWHERE TABNAME = 'FEBEP'\nAND FIELDNAME = 'ESTAT'\nAND AS4LOCAL = 'A'\nAND AS4VERS = '0000'", 10)
    if total != 1 or len(fields) != 1:
        raise ValueError("posting_error_field_metadata_ambiguous")
    elements, total = read("DD04L", "SELECT DOMNAME\nFROM DD04L\nWHERE ROLLNAME = " + literal(fields[0]["ROLLNAME"]) + "\nAND AS4LOCAL = 'A'\nAND AS4VERS = '0000'", 10)
    if total != 1 or len(elements) != 1:
        raise ValueError("posting_error_domain_metadata_ambiguous")
    domain = elements[0]["DOMNAME"]
    texts, total = read(
        "DD04T",
        "SELECT ROLLNAME, DDTEXT, REPTEXT, SCRTEXT_S, SCRTEXT_M, SCRTEXT_L\n"
        "FROM DD04T\nWHERE ROLLNAME = " + literal(fields[0]["ROLLNAME"]) +
        "\nAND DDLANGUAGE = 'E'\nAND AS4LOCAL = 'A'",
        20,
    )
    if len(texts) != total:
        raise ValueError("posting_error_data_element_text_incomplete")
    values, total = read("DD07V", "SELECT DOMNAME, DOMVALUE_L, DDTEXT, VALPOS\nFROM DD07V\nWHERE DOMNAME = " + literal(domain) + "\nAND DDLANGUAGE = 'E'\nORDER BY VALPOS", 100)
    if len(values) != total:
        raise ValueError("posting_error_domain_values_incomplete")
    lifecycle, total = read(
        "LIFECYCLE",
        "SELECT DOMNAME, DOMVALUE_L, DDTEXT, VALPOS\nFROM DD07V\n"
        "WHERE DOMNAME = 'FARP_BS_ITM_LIFECYC_STAT'\nAND DDLANGUAGE = 'E'\nORDER BY VALPOS",
        100,
    )
    if len(lifecycle) != total:
        raise ValueError("lifecycle_domain_values_incomplete")

    def source(name):
        base = str(profile["base_url"]).rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("source_connection_invalid")
        session = requests.Session()
        session.trust_env = False
        session.auth = (profile["username"], profile["password"])
        verify = str(profile.get("verify_ssl", True)).lower() not in {"false", "0", "no"}
        try:
            response = session.get(
                base + "/sap/bc/adt/ddic/ddl/sources/" + quote(name.lower(), safe="") + "/source/main",
                headers={"Accept": "text/plain", "X-SAP-Client": str(profile.get("client", ""))},
                timeout=90,
                verify=verify,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise ValueError("source_network_" + type(exc).__name__) from None
        finally:
            session.close()
        if response.status_code != 200:
            raise ValueError("source_http_" + str(response.status_code))
        lines = response.text.splitlines()
        selected = [line.strip() for line in lines if any(token in line for token in (
            "PostingErrorStatus", "LifeCycSts", "IsCompleted",
            "BankLedgerIsPosted", "SubledgerIsPostedSuccessfully", "SubledgerDocument",
        ))]
        return {
            "object": name,
            "sha256": "sha256:" + hashlib.sha256(response.content).hexdigest(),
            "status_excerpts": selected,
        }

    result = {
        "posting_error": {
            "field": "FEBEP-ESTAT",
            "data_element": fields[0]["ROLLNAME"],
            "domain": domain,
            "field_metadata": fields[0],
            "texts": texts,
            "domain_values": values,
        },
        "lifecycle": {
            "domain": "FARP_BS_ITM_LIFECYC_STAT",
            "domain_values": lifecycle,
        },
        "cds_sources": [
            source("P_Arbanktransactiondocitem_06"),
            source("P_Arbanktransactiondocitem_07"),
        ],
    }
    result["metadata_hash"] = digest(result)
    return result


if __name__ == "__main__":
    profile = json.loads((Path.home() / ".codex/secure/sap-direct-readonly.json").read_text(encoding="utf-8"))
    try:
        print(json.dumps(inspect(profile)))
    except ValueError as exc:
        print(json.dumps({"error_code": str(exc)}))
        raise SystemExit(2)
