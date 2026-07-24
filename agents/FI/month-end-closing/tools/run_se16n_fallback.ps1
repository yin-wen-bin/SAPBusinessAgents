#Requires -Version 5.1

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet("T001", "T001B", "MARV")]
    [string]$Table,

    [Parameter(Mandatory)]
    [ValidatePattern("^[A-Za-z0-9._-]+\.xlsx$")]
    [string]$File,

    [Parameter()]
    [ValidateRange(1, 5000)]
    [int]$MaxHits = 100,

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$ConfigPath = (Join-Path $env:USERPROFILE ".sap-windowsgui-logon\config.json"),

    [Parameter()]
    [ValidatePattern("^\d{3}$")]
    [string]$ExpectedClient = "100"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$assistantRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$outputDir = [IO.Path]::GetFullPath((Join-Path $assistantRoot ".local\se16n"))
$expectedPrefix = $assistantRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $outputDir.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Resolved SE16N output directory is outside the assistant workspace."
}

$outputPath = Join-Path $outputDir $File
if (Test-Path -LiteralPath $outputPath) {
    throw "Refusing to overwrite an existing SE16N export: $outputPath"
}

$skillRoot = Join-Path $env:USERPROFILE ".codex\skills"
$logonScript = Join-Path $skillRoot "sap-windowsgui-logon\scripts\logon.ps1"
$exportScript = Join-Path $skillRoot "sap-se16n-export\scripts\se16n_export.vbs"
$sessionVerifier = Join-Path $PSScriptRoot "verify_sap_gui_session.py"
if (-not (Test-Path -LiteralPath $logonScript -PathType Leaf)) {
    throw "SAP Windows GUI logon skill was not found."
}
if (-not (Test-Path -LiteralPath $exportScript -PathType Leaf)) {
    throw "SAP SE16N export skill was not found."
}
if (-not (Test-Path -LiteralPath $sessionVerifier -PathType Leaf)) {
    throw "SAP GUI session verifier was not found."
}

New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $logonScript `
    -ConfigPath $ConfigPath -ValidateOnly
if ($LASTEXITCODE -ne 0) {
    throw "SAP GUI logon configuration validation failed."
}

& python.exe $sessionVerifier --client $ExpectedClient
if ($LASTEXITCODE -ne 0) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $logonScript `
        -ConfigPath $ConfigPath
    if ($LASTEXITCODE -ne 0) {
        throw "SAP GUI login failed or could not be verified."
    }
    & python.exe $sessionVerifier --client $ExpectedClient
    if ($LASTEXITCODE -ne 0) {
        throw "SAP GUI session is not uniquely bound to the expected client."
    }
}

& cscript.exe //nologo $exportScript `
    "/table:$Table" `
    "/maxhits:$MaxHits" `
    "/outdir:$outputDir" `
    "/file:$File" `
    "/securitytimeout:60"
if ($LASTEXITCODE -ne 0) {
    throw "SE16N export failed. Inspect the SAP GUI session without retrying credentials."
}
if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf)) {
    throw (
        "SE16N export did not create the expected workbook. An external Save As " +
        "dialog may require stable UI adaptation or manual completion: $outputPath"
    )
}

$hash = Get-FileHash -LiteralPath $outputPath -Algorithm SHA256
[pscustomobject]@{
    Table = $Table
    File = $outputPath
    MaxHits = $MaxHits
    SapClient = $ExpectedClient
    Sha256 = $hash.Hash.ToLowerInvariant()
    ScopeStatus = "review-required"
} | ConvertTo-Json
