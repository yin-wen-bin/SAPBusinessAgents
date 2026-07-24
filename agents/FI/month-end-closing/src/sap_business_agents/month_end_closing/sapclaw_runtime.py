"""Read-only SAPClaw Thin Runtime adapter for approved closing queries."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import http.client
import json
import os
from pathlib import Path
import tomllib
from typing import Any, Callable, Protocol
import urllib.parse

from .gateway import SapDataUnavailable
from .models import CheckDefinition, CheckObservation, ClosingContext, DataSourceTrace


JsonObject = dict[str, Any]
RuntimeTransport = Callable[[str, JsonObject, dict[str, str], float], JsonObject]


class RuntimeClient(Protocol):
    def execute_get(self, payload: JsonObject) -> JsonObject: ...

    def page(self, case_id: str, skip: int) -> JsonObject: ...


class SapClawRuntimeClient:
    """Small standard-library client for the local service behind the MCP tools."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        timeout_seconds: float = 60,
        api_key: str | None = None,
        transport: RuntimeTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._api_key = api_key if api_key is not None else os.getenv("SAPCLAW_API_KEY", "")
        self._transport = transport or _loopback_http_post

    def execute_get(self, payload: JsonObject) -> JsonObject:
        return self._post("/api/v1/runtime/execute-get", payload)

    def page(self, case_id: str, skip: int) -> JsonObject:
        return self._post("/api/v1/runtime/page", {"case_id": case_id, "skip": skip})

    def _post(self, path: str, payload: JsonObject) -> JsonObject:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        try:
            response = self._transport(
                f"{self._base_url}{path}", payload, headers, self._timeout
            )
        except (OSError, TimeoutError, ValueError) as exc:
            raise SapDataUnavailable(f"SAPClaw Runtime request failed: {exc}") from exc
        if not response.get("ok"):
            error = response.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or error)
            else:
                message = str(error or "unknown SAPClaw Runtime error")
            raise SapDataUnavailable(message)
        return response


@dataclass(frozen=True)
class SapClawQuery:
    check_id: str
    production_approved: bool
    validation_status: str
    service_name: str
    resource_path: str
    filter_template: str
    select_fields: tuple[str, ...]
    display_fields: tuple[str, ...]
    support_fields: tuple[str, ...]
    amount_field: str
    amount_mode: str
    currency_field: str
    max_rows: int
    evidence_limit: int


@dataclass(frozen=True)
class CompanyProfile:
    currency: str
    fiscal_year_variant: str


def load_sapclaw_queries(path: str | Path) -> dict[str, SapClawQuery]:
    with Path(path).open("rb") as stream:
        raw = tomllib.load(stream)
    entries = raw.get("queries")
    if not isinstance(entries, list):
        raise ValueError("SAPClaw query config requires [[queries]] entries")
    parsed = [_parse_query(item) for item in entries]
    ids = [item.check_id for item in parsed]
    if len(ids) != len(set(ids)):
        raise ValueError("SAPClaw query config contains duplicate check ids")
    return {item.check_id: item for item in parsed}


class SapClawRuntimeGateway:
    """Collect normalized observations using allowlisted, schema-reviewed GETs.

    A query does not run merely because it exists in the catalog. It must be
    explicitly marked production_approved after schema, scope, pagination,
    data-quality, and business-rule review. A GUI transaction baseline is not
    required by this adapter.
    """

    provider_name = "sapclaw_runtime_mcp"

    def __init__(
        self,
        client: RuntimeClient,
        queries: dict[str, SapClawQuery],
        expected_client: str | None = None,
    ) -> None:
        self._client = client
        self._queries = queries
        self._expected_client = expected_client
        self._profiles: dict[str, CompanyProfile] = {}

    def report_currency(self, context: ClosingContext) -> str:
        return self._company_profile(context).currency

    def collect(self, context: ClosingContext, check: CheckDefinition) -> CheckObservation:
        query = self._queries.get(check.check_id)
        if query is None:
            raise SapDataUnavailable(f"no SAPClaw query mapped for {check.check_id}")
        if not query.production_approved:
            raise SapDataUnavailable(
                f"SAPClaw query for {check.check_id} is not production approved: "
                f"{query.validation_status}"
            )

        profile = self._company_profile(context)
        period_start, period_end = _calendar_period_bounds(
            context, profile.fiscal_year_variant
        )
        substitutions = {
            "company_code": context.company_code,
            "fiscal_year": str(context.fiscal_year),
            "period": context.period,
            "fiscal_year_period": f"{context.fiscal_year}{context.period:03d}",
            "period_start_odata": _odata_datetime(period_start, end_of_day=False),
            "period_end_odata": _odata_datetime(period_end, end_of_day=True),
        }
        try:
            rendered_filter = query.filter_template.format_map(substitutions)
        except KeyError as exc:
            raise SapDataUnavailable(f"unknown query template variable: {exc}") from exc

        payload = self._client.execute_get(
            {
                "service_name": query.service_name,
                "resource_path": query.resource_path,
                "query_options": {
                    "$filter": rendered_filter,
                    "$select": ",".join(query.select_fields),
                    "$top": str(query.max_rows),
                },
                "function_parameters": {},
                "output_contract": {
                    "mode": "explicit",
                    "display_grain": f"closing evidence rows for {query.check_id}",
                    "requested_fields": list(query.display_fields),
                    "display_fields": list(query.display_fields),
                    "support_fields": list(query.support_fields),
                    "reason": "Approved month-end fields plus non-displayed scope support fields.",
                },
                "user_input": (
                    f"Read-only month-end check {query.check_id} for company code "
                    f"{context.company_code}, fiscal year {context.fiscal_year}, period {context.period}."
                ),
            }
        )
        self._validate_response_client(payload)
        rows, case_ids = self._all_rows(payload, query.max_rows)
        amount = _aggregate_amount(rows, query)
        currencies = {
            str(row.get(query.currency_field, "")).strip()
            for row in rows
            if str(row.get(query.currency_field, "")).strip()
        }
        if currencies and currencies != {profile.currency}:
            raise SapDataUnavailable(
                f"query returned currencies {sorted(currencies)}, expected {profile.currency}"
            )
        evidence = tuple(
            {field: row.get(field) for field in query.display_fields}
            for row in rows[: query.evidence_limit]
        )
        return CheckObservation(
            value=Decimal(len(rows)),
            amount=amount,
            currency=profile.currency,
            evidence=evidence,
            sources=(
                DataSourceTrace(
                    provider=self.provider_name,
                    status="used",
                    service_name=query.service_name,
                    resource=query.resource_path,
                    case_ids=case_ids,
                    detail=f"rows={len(rows)}; filter={rendered_filter}",
                ),
            ),
        )

    def _company_profile(self, context: ClosingContext) -> CompanyProfile:
        cached = self._profiles.get(context.company_code)
        if cached is not None:
            return cached
        response = self._client.execute_get(
            {
                "service_name": "API_COMPANYCODE_SRV",
                "resource_path": "A_CompanyCode",
                "query_options": {
                    "$filter": f"CompanyCode eq '{context.company_code}'",
                    "$select": "CompanyCode,Currency,FiscalYearVariant",
                    "$top": "2",
                },
                "function_parameters": {},
                "output_contract": {
                    "mode": "explicit",
                    "display_grain": "one company code",
                    "requested_fields": ["CompanyCode", "Currency", "FiscalYearVariant"],
                    "display_fields": ["CompanyCode", "Currency", "FiscalYearVariant"],
                    "support_fields": [],
                    "reason": "Resolve and validate month-end scope and company-code currency.",
                },
                "user_input": f"Read-only company code scope validation for {context.company_code}.",
            }
        )
        self._validate_response_client(response)
        rows = _result_rows(response)
        if len(rows) != 1 or rows[0].get("CompanyCode") != context.company_code:
            raise SapDataUnavailable(
                f"company code lookup expected one row for {context.company_code}, got {len(rows)}"
            )
        currency = str(rows[0].get("Currency", "")).strip()
        variant = str(rows[0].get("FiscalYearVariant", "")).strip()
        if not currency or not variant:
            raise SapDataUnavailable("company code currency or fiscal year variant is missing")
        profile = CompanyProfile(currency, variant)
        self._profiles[context.company_code] = profile
        return profile

    def _all_rows(
        self, payload: JsonObject, max_rows: int
    ) -> tuple[list[JsonObject], tuple[str, ...]]:
        rows = _result_rows(payload)
        pagination = _pagination(payload)
        total = int(pagination.get("total_count", len(rows)))
        if total > max_rows:
            raise SapDataUnavailable(
                f"query returned {total} rows, above approved maximum {max_rows}"
            )
        case_id = payload.get("case_id")
        case_ids: list[str] = [str(case_id)] if case_id else []
        seen_skips: set[int] = set()
        while pagination.get("has_next"):
            next_skip = pagination.get("next_skip")
            if not isinstance(next_skip, int) or next_skip in seen_skips or not case_id:
                raise SapDataUnavailable("invalid SAPClaw pagination state")
            seen_skips.add(next_skip)
            page = self._client.page(str(case_id), next_skip)
            self._validate_response_client(page)
            page_case_id = page.get("case_id")
            if page_case_id and str(page_case_id) not in case_ids:
                case_ids.append(str(page_case_id))
            rows.extend(_result_rows(page))
            pagination = _pagination(page)
            if len(rows) > max_rows:
                raise SapDataUnavailable("query exceeded approved maximum while paging")
        if len(rows) != total:
            raise SapDataUnavailable(
                f"incomplete SAPClaw result: received {len(rows)} of {total} rows"
            )
        return rows, tuple(case_ids)

    def _validate_response_client(self, payload: JsonObject) -> None:
        if self._expected_client is None:
            return
        requests = payload.get("executed_requests")
        if not isinstance(requests, list) or not requests:
            raise SapDataUnavailable(
                "SAPClaw response has no executed request metadata for client validation"
            )
        observed: set[str] = set()
        for item in requests:
            if not isinstance(item, dict) or not item.get("success"):
                continue
            url = str(item.get("url", ""))
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            observed.update(query.get("sap-client", []))
        if observed != {self._expected_client}:
            raise SapDataUnavailable(
                f"SAPClaw executed client mismatch: expected {self._expected_client}, "
                f"observed {sorted(observed)}"
            )


def _parse_query(item: object) -> SapClawQuery:
    if not isinstance(item, dict):
        raise ValueError("each SAPClaw query must be a TOML table")
    select_fields = _string_list(item, "select_fields")
    display_fields = _string_list(item, "display_fields")
    support_fields = _string_list(item, "support_fields", required=False)
    if not set((*display_fields, *support_fields)) <= set(select_fields):
        raise ValueError("display/support fields must be included in select_fields")
    amount_mode = _text(item, "amount_mode")
    if amount_mode not in {"absolute_sum", "signed_sum", "zero"}:
        raise ValueError(f"unsupported amount_mode: {amount_mode}")
    amount_field = _text(item, "amount_field", required=amount_mode != "zero")
    if amount_field and amount_field not in select_fields:
        raise ValueError("amount_field must be included in select_fields")
    max_rows = item.get("max_rows", 1000)
    evidence_limit = item.get("evidence_limit", 20)
    if not isinstance(max_rows, int) or not 1 <= max_rows <= 5000:
        raise ValueError("max_rows must be between 1 and 5000")
    if not isinstance(evidence_limit, int) or not 0 <= evidence_limit <= 100:
        raise ValueError("evidence_limit must be between 0 and 100")
    approved = item.get("production_approved")
    if not isinstance(approved, bool):
        raise ValueError("production_approved must be boolean")
    return SapClawQuery(
        check_id=_text(item, "check_id"),
        production_approved=approved,
        validation_status=_text(item, "validation_status"),
        service_name=_text(item, "service_name"),
        resource_path=_text(item, "resource_path"),
        filter_template=_text(item, "filter_template"),
        select_fields=select_fields,
        display_fields=display_fields,
        support_fields=support_fields,
        amount_field=amount_field,
        amount_mode=amount_mode,
        currency_field=_text(item, "currency_field"),
        max_rows=max_rows,
        evidence_limit=evidence_limit,
    )


def _aggregate_amount(rows: list[JsonObject], query: SapClawQuery) -> Decimal:
    if query.amount_mode == "zero":
        return Decimal("0")
    total = Decimal("0")
    for row in rows:
        raw = row.get(query.amount_field)
        try:
            amount = Decimal(str(raw))
        except (InvalidOperation, TypeError) as exc:
            raise SapDataUnavailable(
                f"invalid amount in {query.amount_field}: {raw!r}"
            ) from exc
        total += abs(amount) if query.amount_mode == "absolute_sum" else amount
    return total


def _calendar_period_bounds(context: ClosingContext, variant: str) -> tuple[date, date]:
    if variant != "K4":
        raise SapDataUnavailable(
            f"fiscal year variant {variant} requires an approved fiscal calendar resolver"
        )
    if context.period > 12:
        raise SapDataUnavailable("special periods require an approved posting-date policy")
    last_day = calendar.monthrange(context.fiscal_year, context.period)[1]
    return (
        date(context.fiscal_year, context.period, 1),
        date(context.fiscal_year, context.period, last_day),
    )


def _odata_datetime(value: date, *, end_of_day: bool) -> str:
    time_part = "23:59:59" if end_of_day else "00:00:00"
    return f"datetime'{value.isoformat()}T{time_part}'"


def _result_rows(payload: JsonObject) -> list[JsonObject]:
    data = payload.get("data")
    rows = data.get("results") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise SapDataUnavailable("SAPClaw response does not contain valid result rows")
    return list(rows)


def _pagination(payload: JsonObject) -> JsonObject:
    value = payload.get("pagination")
    if not isinstance(value, dict):
        raise SapDataUnavailable("SAPClaw response does not contain pagination metadata")
    return value


def _text(item: dict[str, object], key: str, *, required: bool = True) -> str:
    value = item.get(key, "")
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"missing or invalid text field: {key}")
    return value.strip()


def _string_list(
    item: dict[str, object], key: str, *, required: bool = True
) -> tuple[str, ...]:
    value = item.get(key, [])
    if not isinstance(value, list) or not all(isinstance(part, str) and part for part in value):
        raise ValueError(f"invalid string list: {key}")
    if required and not value:
        raise ValueError(f"missing string list: {key}")
    return tuple(dict.fromkeys(value))


def _loopback_http_post(
    url: str, payload: JsonObject, headers: dict[str, str], timeout: float
) -> JsonObject:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("SAPClaw Runtime base URL must be a local HTTP loopback address")
    body = json.dumps(payload).encode("utf-8")
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout)
    try:
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        response_body = response.read()
        if not 200 <= response.status < 300:
            detail = response_body.decode("utf-8", errors="replace")
            raise OSError(f"HTTP {response.status}: {detail}")
    finally:
        connection.close()
    decoded = json.loads(response_body.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("SAPClaw Runtime returned a non-object response")
    return decoded
