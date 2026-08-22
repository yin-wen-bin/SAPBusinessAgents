from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "Start-SAPBusinessAgents.ps1"


def test_launcher_contract_includes_cached_preview_and_explicit_dev_mode() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert "[switch]$Dev" in source
    assert "[switch]$RebuildSite" in source
    assert '".local-data\\site-builds"' in source
    assert '"preview", "--host", "127.0.0.1"' in source
    assert '"dev", "--host", "127.0.0.1"' in source
    assert '"last-attempt.json"' in source
    assert "GetActiveTcpListeners" in source
    assert source.count("Get-NetTCPConnection") == 1


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows PowerShell is unavailable")
def test_launcher_parses_in_windows_powershell() -> None:
    parser_command = (
        "$tokens=$null; $errors=$null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{LAUNCHER}', [ref]$tokens, [ref]$errors) | Out-Null; "
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Error $_.Message }; exit 1 }"
    )

    completed = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", parser_command],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
