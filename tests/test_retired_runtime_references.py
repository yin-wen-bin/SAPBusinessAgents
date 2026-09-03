from __future__ import annotations

from scripts.check_retired_runtime_references import active_violations


def test_active_contracts_do_not_reference_retired_thin_runtime() -> None:
    assert active_violations() == []
