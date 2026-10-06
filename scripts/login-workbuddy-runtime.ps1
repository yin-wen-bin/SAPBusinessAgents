param(
    [switch]$VerifyOnly,
    [switch]$CheckLogin,
    [switch]$NoBrowser,
    [string]$MethodId = 'external'
)
$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$platformPython = Join-Path $repository '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $platformPython -PathType Leaf)) {
    throw 'Platform Python is needed only to verify the SDK-free WorkBuddy release registry.'
}

# The platform interpreter reads the registry, never imports or runs the SDK.
$verificationCode = @'
import json, sys
from pathlib import Path
from sap_business_agents_platform.workbuddy_environment import WorkBuddyEnvironment, WorkBuddyError
try:
    release = WorkBuddyEnvironment(Path(sys.argv[1])).release()
    print(json.dumps({name: release[name] for name in ('environment_digest', 'python_path', 'cli_path', 'sdk_version', 'cli_version')}))
except WorkBuddyError as exc:
    print(exc.code, file=sys.stderr)
    sys.exit(1)
'@
$output = & $platformPython -B -c $verificationCode $repository
if ($LASTEXITCODE -ne 0 -or -not $output) {
    throw 'WorkBuddy release verification failed; no login process was started.'
}
$release = $output | ConvertFrom-Json
if ($VerifyOnly) {
    $output
    return
}

# The bundled headless CLI needs SDK authentication control messages, not a TUI launch.
# Only the independent interpreter imports the SDK. No prompt/model task is sent.
$loginHelper = Join-Path $PSScriptRoot 'login-workbuddy-runtime.py'
if (-not (Test-Path -LiteralPath $loginHelper -PathType Leaf)) {
    throw 'WorkBuddy authentication helper is missing; no login process was started.'
}
Write-Host ('Verified WorkBuddy SDK {0}, CLI {1}. Starting authentication check.' -f $release.sdk_version, $release.cli_version)
$loginArguments = @('-I', '-B', $loginHelper, '--cli-path', $release.cli_path, '--method-id', $MethodId)
if ($CheckLogin) { $loginArguments += '--check-only' }
if ($NoBrowser) { $loginArguments += '--no-browser' }
& $release.python_path @loginArguments
if ($LASTEXITCODE -ne 0) {
    throw 'WorkBuddy login was not confirmed. Read the diagnostic above; no model or SAP task was started.'
}
