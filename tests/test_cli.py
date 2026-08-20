from __future__ import annotations

import os

from sap_business_agents_platform.cli import configure_internal_api_url


def test_cli_binds_harness_callback_to_selected_local_port(monkeypatch) -> None:
    monkeypatch.setenv("SAPBA_INTERNAL_API_URL", "http://127.0.0.1:8765")

    value = configure_internal_api_url("localhost", 8768)

    assert value == "http://127.0.0.1:8768"
    assert os.environ["SAPBA_INTERNAL_API_URL"] == value
