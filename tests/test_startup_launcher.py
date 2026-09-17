from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "Start-SAPBusinessAgents.ps1"
CMD_LAUNCHER = ROOT / "start-sap-business-agents.cmd"


def test_cmd_launcher_always_restarts_and_preserves_extra_arguments() -> None:
    source = CMD_LAUNCHER.read_text(encoding="utf-8")

    invocation = next(line for line in source.splitlines() if line.startswith("powershell.exe "))
    assert 'Start-SAPBusinessAgents.ps1" -Restart %*' in invocation


def test_launcher_contract_includes_cached_preview_and_explicit_dev_mode() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert "[switch]$Dev" in source
    assert "[switch]$RebuildSite" in source
    assert '".local-data\\site-builds"' in source
    assert '"preview", "--host", "127.0.0.1"' in source
    assert '"dev", "--host", "127.0.0.1"' in source
    # Astro can auto-background in an agent-owned process. The launcher must
    # retain a foreground PID for health checks and owned-process cleanup.
    assert source.count('"--ignore-lock"') == 2
    assert '"last-attempt.json"' in source
    assert "GetActiveTcpListeners" in source
    assert source.count("Get-NetTCPConnection") == 1


def test_restart_preserves_the_prevalidated_site_build_path_until_preview_start() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    initial_dist = source.index("$SiteDist = $null")
    restart_build = source.index(
        '$SiteDist = Join-Path (Join-Path $SiteBuildRoot $SiteFingerprint) "dist"'
    )
    preview_start = source.index(
        '-Arguments @($AstroCli, "preview", "--host", "127.0.0.1"'
    )

    assert initial_dist < source.index("try {")
    assert source.count("$SiteDist = $null") == 1
    assert initial_dist < restart_build < preview_start
    assert "The validated Web UI build path is unavailable before preview startup." in source


def test_api_health_probe_allows_the_catalog_health_response_to_finish() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    health_probe = source[
        source.index("function Test-PlatformHealth") : source.index("function Test-SiteHealth")
    ]

    assert 'Invoke-RestMethod -Uri "$ApiUrl/api/health" -TimeoutSec 5' in health_probe
    assert "-TimeoutSec 1" not in health_probe


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
