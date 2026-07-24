"""Module-specific checkers sharing a deterministic threshold evaluator."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import CheckDefinition, CheckObservation


@dataclass(frozen=True)
class Evaluation:
    is_exception: bool
    message: str


class ThresholdChecker:
    """Base checker; subclasses create an extension point per SAP module."""

    handler: str = ""
    modules: frozenset[str] = frozenset()

    def evaluate(self, definition: CheckDefinition, observation: CheckObservation) -> Evaluation:
        if definition.module not in self.modules:
            raise ValueError(f"handler {self.handler} does not support module {definition.module}")
        matched = _compare(observation.value, definition.operator, definition.threshold)
        state = "exception" if matched else "passed"
        message = (
            f"{definition.metric_name}={observation.value} evaluated {definition.operator} "
            f"{definition.threshold}: {state}"
        )
        return Evaluation(matched, message)


class AccountsPayableChecker(ThresholdChecker):
    handler = "ap"
    modules = frozenset({"FI-AP"})


class AccountsReceivableChecker(ThresholdChecker):
    handler = "ar"
    modules = frozenset({"FI-AR"})


class GeneralLedgerChecker(ThresholdChecker):
    handler = "gl"
    modules = frozenset({"FI-GL"})


class AssetAccountingChecker(ThresholdChecker):
    handler = "aa"
    modules = frozenset({"FI-AA"})


class ControllingChecker(ThresholdChecker):
    handler = "co"
    modules = frozenset({"CO"})


class MaterialsManagementChecker(ThresholdChecker):
    handler = "mm"
    modules = frozenset({"MM"})


class SalesDistributionChecker(ThresholdChecker):
    handler = "sd"
    modules = frozenset({"SD"})


def default_checkers() -> dict[str, ThresholdChecker]:
    instances = (
        AccountsPayableChecker(),
        AccountsReceivableChecker(),
        GeneralLedgerChecker(),
        AssetAccountingChecker(),
        ControllingChecker(),
        MaterialsManagementChecker(),
        SalesDistributionChecker(),
    )
    return {item.handler: item for item in instances}


def _compare(actual: Decimal, operator: str, threshold: Decimal) -> bool:
    operations = {
        "eq": actual == threshold,
        "ne": actual != threshold,
        "gt": actual > threshold,
        "gte": actual >= threshold,
        "lt": actual < threshold,
        "lte": actual <= threshold,
    }
    try:
        return operations[operator]
    except KeyError as exc:
        raise ValueError(f"unsupported operator: {operator}") from exc

