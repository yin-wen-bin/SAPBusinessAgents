param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$SdkWheel,
    [Parameter(Mandatory=$true)][string]$WheelSha256
)
$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$interpreter = (Resolve-Path -LiteralPath $Python).Path
$platformVenv = Join-Path $repository '.venv'
if ($interpreter.StartsWith($platformVenv, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'WorkBuddy bootstrap must not use the platform Python environment.'
}
$arguments = @('-I', (Join-Path $PSScriptRoot 'install-workbuddy-runtime.py'), '--python', $interpreter)
if ($SdkWheel) {
    if (-not $WheelSha256) { throw 'A separately verified SDK wheel SHA256 is required.' }
    $arguments += @('--sdk-wheel', (Resolve-Path -LiteralPath $SdkWheel).Path, '--wheel-sha256', $WheelSha256)
}
& $interpreter @arguments
if ($LASTEXITCODE -ne 0) { throw "WorkBuddy installation failed ($LASTEXITCODE); current release unchanged." }
