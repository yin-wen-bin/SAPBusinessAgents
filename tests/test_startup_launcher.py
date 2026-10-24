from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "Start-SAPBusinessAgents.ps1"
CMD_LAUNCHER = ROOT / "start-sap-business-agents.cmd"


def test_cmd_launcher_preserves_arguments_and_requires_explicit_restart() -> None:
    source = CMD_LAUNCHER.read_text(encoding="utf-8")

    invocation = next(line for line in source.splitlines() if line.startswith("powershell.exe "))
    assert 'Start-SAPBusinessAgents.ps1" %*' in invocation
    assert '-Restart' not in invocation
    assert ' -Restart %*' in (ROOT / 'restart-sap-business-agents.cmd').read_text()


def test_launcher_contract_includes_cached_preview_and_explicit_dev_mode() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert "[switch]$Dev" in source
    assert "[switch]$RebuildSite" in source
    assert '".local-data\\site-builds"' in source
    runtime = (ROOT / 'scripts/service-startup.py').read_text()
    assert "'dev' if" in runtime and '"preview"' in runtime
    # Astro can auto-background in an agent-owned process. The launcher must
    # retain a foreground PID for health checks and owned-process cleanup.
    assert runtime.count('"--ignore-lock"') == 2
    assert '"last-attempt.json"' in source
    assert "GetActiveTcpListeners" in source
    assert source.count("Get-NetTCPConnection") == 1


def test_restart_preserves_the_prevalidated_site_build_path_until_preview_start() -> None:
    # The single orchestrator passes one content fingerprint through prepare,
    # private-port validation and final switch; it never resets a validated path.
    source = (ROOT / 'scripts/service-startup.py').read_text()
    body = source[source.index('def _ensure('):source.index('def ensure(')]
    assert body.index('"prepare_site"') < body.index('"site_candidate_ready"') < body.index('stop_owned(')
    assert 'switch_site(root, fingerprint' in body
    assert 'dist = root / ".local-data/site-builds" / fingerprint / "dist"' in source
    assert 'startup_site_build_missing' in source


def _platform_health_probe_source() -> str:
    source = LAUNCHER.read_text(encoding="utf-8")
    return source[
        source.index("function Test-PlatformHealth") : source.index("function Test-SiteHealth")
    ]


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows PowerShell is unavailable")
@pytest.mark.parametrize(
    ("mock_result", "expected"),
    [
        (
            "return [pscustomobject]@{ ok=$true; loopback_only=$true; "
            "sap_read=[pscustomobject]@{ selected_provider='embedded'; "
            "data=[pscustomobject]@{ read_only=$true } } }",
            True,
        ),
        (
            "return [pscustomobject]@{ ok=$false; loopback_only=$true; "
            "sap_read=[pscustomobject]@{ selected_provider='embedded'; "
            "data=[pscustomobject]@{ read_only=$true } } }",
            False,
        ),
        ("throw 'offline'", False),
    ],
)
def test_api_health_probe_uses_twenty_seconds_and_handles_results(
    tmp_path: Path,
    mock_result: str,
    expected: bool,
) -> None:
    script = tmp_path / "test-platform-health.ps1"
    expected_literal = "$true" if expected else "$false"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "function Invoke-RestMethod {\n"
        "  param([string]$Uri, [int]$TimeoutSec)\n"
        "  $script:CapturedUri = $Uri\n"
        "  $script:CapturedTimeout = $TimeoutSec\n"
        f"  {mock_result}\n"
        "}\n"
        f"{_platform_health_probe_source()}\n"
        "$ApiUrl = 'http://127.0.0.1:8765'\n"
        "$actual = Test-PlatformHealth\n"
        "if ($script:CapturedTimeout -ne 20) { throw \"unexpected timeout: $script:CapturedTimeout\" }\n"
        "if ($script:CapturedUri -ne \"$ApiUrl/api/health\") { throw \"unexpected URI: $script:CapturedUri\" }\n"
        f"if ($actual -ne {expected_literal}) {{ throw \"unexpected result: $actual\" }}\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


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
