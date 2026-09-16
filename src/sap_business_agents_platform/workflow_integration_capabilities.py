from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class WorkflowIntegrationCapability:
    """The workflow-facing contract, independent of a native connector tool."""

    capability: str
    input_operations: frozenset[str]
    output_operations: frozenset[str]
    output_binding_operations: frozenset[str]
    approval_operations: frozenset[str]
    read_only_input_operations: frozenset[str]

    def requires_output_binding(self, operation: str) -> bool:
        return operation in self.output_binding_operations

    def requires_approval(self, operation: str) -> bool:
        return operation in self.approval_operations


class WorkflowIntegrationHandler(Protocol):
    """Runtime behavior for one registered workflow capability."""

    def normalize_input(self, operation: str, result: Any) -> Any: ...

    def create_output_action(
        self,
        run_id: str,
        item: dict[str, Any],
        draft: dict[str, Any],
        integrations: Any,
        idempotency_key: str,
    ) -> dict[str, Any]: ...


# Only registered capabilities may appear in executable workflows. A planned
# connector must not become callable merely because it appears in the UI.
WORKFLOW_INTEGRATION_CAPABILITIES: dict[str, WorkflowIntegrationCapability] = {
    "mail.v1": WorkflowIntegrationCapability(
        capability="mail.v1",
        input_operations=frozenset({"search", "read"}),
        output_operations=frozenset({"draft", "send"}),
        output_binding_operations=frozenset({"send"}),
        approval_operations=frozenset({"send"}),
        read_only_input_operations=frozenset({"search", "read"}),
    ),
}


def workflow_integration_capability(
    capability: str,
) -> WorkflowIntegrationCapability | None:
    return WORKFLOW_INTEGRATION_CAPABILITIES.get(capability)
