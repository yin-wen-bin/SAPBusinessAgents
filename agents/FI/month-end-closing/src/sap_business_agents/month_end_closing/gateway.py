"""Replaceable boundary between the closing domain and SAP data access."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any, Protocol

from .models import CheckDefinition, CheckObservation, ClosingContext, DataSourceTrace


class SapDataUnavailable(RuntimeError):
    """Raised when a required SAP observation cannot be obtained safely."""

    def __init__(
        self, message: str, sources: tuple[DataSourceTrace, ...] = ()
    ) -> None:
        super().__init__(message)
        self.sources = sources


class SapGateway(Protocol):
    """Port implemented by fixture, RFC, OData, CDS, or warehouse adapters.

    A production adapter should apply company code and fiscal-period filters,
    execute only read operations, and return the normalized observation below.
    """

    provider_name: str

    def collect(self, context: ClosingContext, check: CheckDefinition) -> CheckObservation:
        """Read and normalize one configured checklist observation."""

    def report_currency(self, context: ClosingContext) -> str:
        """Resolve the company-code currency used by every normalized amount."""


class FixtureSapGateway:
    """Read-only gateway backed by a deterministic JSON fixture."""

    provider_name = "fixture"

    def __init__(self, payload: dict[str, Any], source_name: str = "inline fixture") -> None:
        self._payload = payload
        self._source_name = source_name

    @classmethod
    def from_file(cls, path: str | Path) -> "FixtureSapGateway":
        try:
            with Path(path).open("r", encoding="utf-8") as stream:
                payload = json.load(stream)
        except (OSError, json.JSONDecodeError) as exc:
            raise SapDataUnavailable(f"cannot read fixture: {exc}") from exc
        if not isinstance(payload, dict):
            raise SapDataUnavailable("fixture root must be an object")
        return cls(payload, str(Path(path).resolve()))

    def collect(self, context: ClosingContext, check: CheckDefinition) -> CheckObservation:
        self._validate_scope(context)
        checks = self._payload.get("checks")
        if not isinstance(checks, dict) or check.check_id not in checks:
            raise SapDataUnavailable(f"missing observation for {check.check_id}")
        raw = checks[check.check_id]
        if not isinstance(raw, dict):
            raise SapDataUnavailable(f"observation for {check.check_id} must be an object")
        try:
            value = Decimal(str(raw["value"]))
            amount = Decimal(str(raw.get("amount", 0)))
        except (KeyError, InvalidOperation) as exc:
            raise SapDataUnavailable(f"invalid numeric observation for {check.check_id}") from exc
        evidence = raw.get("evidence", [])
        if not isinstance(evidence, list) or not all(isinstance(item, dict) for item in evidence):
            raise SapDataUnavailable(f"invalid evidence for {check.check_id}")
        currency = str(raw.get("currency", self._payload.get("currency", "CNY")))
        return CheckObservation(
            value,
            amount,
            currency,
            tuple(evidence),
            sources=(
                DataSourceTrace(
                    provider=self.provider_name,
                    status="used",
                    resource=self._source_name,
                ),
            ),
        )

    def report_currency(self, context: ClosingContext) -> str:
        self._validate_scope(context)
        currency = self._payload.get("currency")
        if not isinstance(currency, str) or not currency.strip():
            raise SapDataUnavailable("fixture requires a report currency")
        return currency.strip()

    def _validate_scope(self, context: ClosingContext) -> None:
        expected = {
            "company_code": context.company_code,
            "fiscal_year": context.fiscal_year,
            "period": context.period,
        }
        actual = {key: self._payload.get(key) for key in expected}
        if actual != expected:
            raise SapDataUnavailable(f"fixture scope mismatch: expected {expected}, got {actual}")


class CompositeSapGateway:
    """Use ordered providers and retain an audit trail when a fallback is needed."""

    provider_name = "composite"

    def __init__(self, *gateways: SapGateway) -> None:
        if not gateways:
            raise ValueError("at least one gateway is required")
        self._gateways = gateways

    def report_currency(self, context: ClosingContext) -> str:
        errors: list[str] = []
        for gateway in self._gateways:
            try:
                return gateway.report_currency(context)
            except SapDataUnavailable as exc:
                errors.append(str(exc))
        raise SapDataUnavailable("; ".join(errors))

    def collect(self, context: ClosingContext, check: CheckDefinition) -> CheckObservation:
        errors: list[str] = []
        unavailable_sources: list[DataSourceTrace] = []
        for gateway in self._gateways:
            try:
                observation = gateway.collect(context, check)
                if unavailable_sources:
                    return replace(
                        observation,
                        sources=tuple(unavailable_sources) + observation.sources,
                    )
                return observation
            except SapDataUnavailable as exc:
                errors.append(f"{type(gateway).__name__}: {exc}")
                if exc.sources:
                    unavailable_sources.extend(exc.sources)
                else:
                    unavailable_sources.append(
                        DataSourceTrace(
                            provider=getattr(
                                gateway, "provider_name", type(gateway).__name__
                            ),
                            status="unavailable",
                            detail=str(exc),
                        )
                    )
        raise SapDataUnavailable("; ".join(errors), tuple(unavailable_sources))


FallbackSapGateway = CompositeSapGateway
