#Requires -Version 5.1

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet("T001", "T001B", "MARV", "TABA")]
    [string]$Table,

    [Parameter(Mandatory)]
    [ValidatePattern("^[A-Za-z0-9._-]+\.json$")]
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
$outputDir = [IO.Path]::GetFullPath((Join-Path $assistantRoot ".local\se16n-grid"))
$expectedPrefix = $assistantRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $outputDir.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Resolved SE16N grid output directory is outside the assistant workspace."
}

$outputPath = Join-Path $outputDir $File
if (Test-Path -LiteralPath $outputPath) {
    throw "Refusing to overwrite an existing SE16N grid export: $outputPath"
}

$skillRoot = Join-Path $env:USERPROFILE ".codex\skills"
$logonScript = Join-Path $skillRoot "sap-windowsgui-logon\scripts\logon.ps1"
$sessionVerifier = Join-Path $PSScriptRoot "verify_sap_gui_session.py"
$gridExporter = Join-Path $PSScriptRoot "export_se16n_grid.py"

New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $logonScript `
    -ConfigPath $ConfigPath -ValidateOnly
if ($LASTEXITCODE -ne 0) {
    throw "SAP GUI logon configuration validation failed."
}

& python.exe $sessionVerifier --client $ExpectedClient
if ($LASTEXITCODE -ne 0) {
    throw "SAP GUI session is not uniquely authenticated and idle for the expected client."
}

& python.exe $gridExporter `
    --table $Table `
    --max-hits $MaxHits `
    --output $outputPath `
    --client $ExpectedClient
if ($LASTEXITCODE -ne 0) {
    throw "SE16N ALV grid export failed."
}
if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf)) {
    throw "SE16N ALV grid export did not create the expected JSON evidence."
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
