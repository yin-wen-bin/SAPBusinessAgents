param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_]+$')]
    [string]$Artifact,

    [switch]$Headed
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$artifactRoot = Join-Path $repositoryRoot ".artifacts\sap-bah\$Artifact\raw"
$statePath = Join-Path $artifactRoot 'browser-state.json'
$playwrightPackage = '@playwright/cli@0.1.18'
$sessionName = "sap-bah-$($Artifact.ToLowerInvariant())"
$userName = $env:SAP_BAH_ID
$password = $env:SAP_BAH_PW

if (-not $userName -or -not $password) {
    throw 'Set SAP_BAH_ID and SAP_BAH_PW. Credentials cannot be supplied as command-line arguments.'
}

New-Item -ItemType Directory -Force -Path $artifactRoot | Out-Null

function Invoke-PlaywrightCli {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    $env:PLAYWRIGHT_CLI_SESSION = $sessionName
    & npx --yes $playwrightPackage @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Playwright CLI failed: $($Arguments -join ' ')"
    }
}

function Find-InteractiveRef {
    param([string]$Snapshot, [string[]]$Patterns)
    foreach ($pattern in $Patterns) {
        $match = [regex]::Match($Snapshot, "(?im)^.*$pattern.*\[ref=(e\d+)\].*$")
        if ($match.Success) { return $match.Groups[1].Value }
    }
    return $null
}

$overviewUrl = "https://api.sap.com/api/$Artifact/overview"
try {
    $openArgs = @('open', $overviewUrl)
    if ($Headed) { $openArgs += '--headed' }
    Invoke-PlaywrightCli @openArgs | Out-Null

    $snapshot = Invoke-PlaywrightCli snapshot | Out-String
    if ($snapshot -match 'accounts\.sap\.com') {
        $userRef = Find-InteractiveRef $snapshot @('textbox.*(?:email|user|ID)', 'input.*(?:email|user)')
        $passwordRef = Find-InteractiveRef $snapshot @('textbox.*password', 'input.*password')
        if (-not $userRef -or -not $passwordRef) {
            throw 'SAP login controls could not be identified. MFA or a changed login flow requires manual review.'
        }
        Invoke-PlaywrightCli fill $userRef $userName | Out-Null
        Invoke-PlaywrightCli fill $passwordRef $password | Out-Null
        $snapshot = Invoke-PlaywrightCli snapshot | Out-String
        $submitRef = Find-InteractiveRef $snapshot @('button.*(?:Log On|Sign In|Continue|登录)')
        if (-not $submitRef) {
            throw 'SAP login submit control could not be identified.'
        }
        Invoke-PlaywrightCli click $submitRef | Out-Null
    }

    Invoke-PlaywrightCli goto $overviewUrl | Out-Null
    $postLogin = Invoke-PlaywrightCli snapshot | Out-String
    if ($postLogin -match 'accounts\.sap\.com|verification code|multi-factor|MFA') {
        throw 'SAP BAH login requires additional interaction; stop and review manually.'
    }
    Invoke-PlaywrightCli state-save $statePath | Out-Null

    $env:SAPBA_BAH_ARTIFACT = $Artifact
    $env:SAPBA_BAH_STATE = $statePath
    $env:SAPBA_BAH_RAW_ROOT = $artifactRoot
    @'
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

artifact = os.environ["SAPBA_BAH_ARTIFACT"]
state_path = Path(os.environ["SAPBA_BAH_STATE"])
raw_root = Path(os.environ["SAPBA_BAH_RAW_ROOT"])
state = json.loads(state_path.read_text(encoding="utf-8"))
session = requests.Session()
for cookie in state.get("cookies", []):
    domain = str(cookie.get("domain") or "")
    if domain.endswith("api.sap.com"):
        session.cookies.set(
            str(cookie.get("name") or ""),
            str(cookie.get("value") or ""),
            domain=domain.lstrip("."),
            path=str(cookie.get("path") or "/"),
        )

def fetch(name: str, url: str, accept: str) -> dict[str, object]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = session.get(url, headers={"Accept": accept}, timeout=(15, 90))
            response.raise_for_status()
            content = response.content
            suffix = {"json": ".json", "yaml": ".yaml", "edmx": ".xml"}.get(name, ".json")
            target = raw_root / f"{name}{suffix}"
            target.write_bytes(content)
            return {
                "name": name,
                "status": "complete",
                "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
                "content_type": response.headers.get("Content-Type"),
                "bytes": len(content),
            }
        except (requests.RequestException, OSError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    return {"name": name, "status": "failed", "error_type": type(last_error).__name__}

base = "https://api.sap.com"
catalog = f"{base}/odata/1.0/catalog.svc/APIContent.APIs('{artifact}')"
manifest = []
manifest.append(fetch("meta", catalog + "?%24select=*&%24format=json", "application/json"))
manifest.append(fetch("versions", catalog + "/Versions?%24format=json", "application/json"))

meta = json.loads((raw_root / "meta.json").read_text(encoding="utf-8"))
data = meta.get("d") or {}
version = data.get("Version")
if not version and isinstance(data.get("results"), list) and data["results"]:
    version = data["results"][0].get("Version")
if not version:
    raise RuntimeError("SAP BAH metadata did not identify an artifact version")

value_url = catalog + "/%24value?Version=" + str(version)
manifest.append(fetch("json", value_url + "&type=json", "application/json"))
manifest.append(fetch("yaml", value_url + "&type=yaml", "application/yaml,text/yaml"))
manifest.append(fetch("edmx", value_url + "&type=edmx", "application/xml"))
manifest.append(fetch("refs_target", f"{base}/api/1.0/resourceReferences/{artifact}?type=API&targetReferences=true", "application/json"))
manifest.append(fetch("refs_source", f"{base}/api/1.0/resourceReferences/{artifact}?type=API&sourceReferences=true", "application/json"))

output = {
    "artifact": artifact,
    "artifact_version": version,
    "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    "files": manifest,
}
(raw_root / "manifest.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(output, ensure_ascii=False, indent=2))
'@ | & (Join-Path $repositoryRoot '.venv\Scripts\python.exe') -
    if ($LASTEXITCODE -ne 0) { throw 'SAP BAH artifact download failed.' }
}
finally {
    Remove-Item Env:SAPBA_BAH_ARTIFACT,Env:SAPBA_BAH_STATE,Env:SAPBA_BAH_RAW_ROOT -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $statePath) {
        Remove-Item -LiteralPath $statePath -Force
    }
    try { Invoke-PlaywrightCli close | Out-Null } catch { }
}
