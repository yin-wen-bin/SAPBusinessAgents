[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$ApiPort = 8765,
    [ValidateRange(1, 65535)]
    [int]$SitePort = 4321,
    [ValidateRange(5, 300)]
    [int]$StartupTimeoutSeconds = 90,
    [switch]$NoBrowser,
    [switch]$Restart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Split-Path -Parent $ScriptDirectory)

$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$ApiUrl = "http://127.0.0.1:$ApiPort"
$SiteHealthUrl = "http://127.0.0.1:$SitePort/"
$SiteUrl = "http://127.0.0.1:$SitePort/zh/"
$Timestamp = Get-Date -Format "yyyyMMddTHHmmss"
$LogRoot = Join-Path $ProjectRoot ".local-data\startup\$Timestamp"
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null

function Write-Step {
    param([string]$Message)
    Write-Host "[SAPBusinessAgents] $Message" -ForegroundColor Cyan
}

function Get-ListenerProcessId {
    param([int]$Port)

    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -in @("127.0.0.1", "::1", "0.0.0.0", "::") } |
        Select-Object -First 1
    if ($null -eq $listener) {
        return $null
    }
    return [int]$listener.OwningProcess
}

function Test-PlatformHealth {
    try {
        $health = Invoke-RestMethod -Uri "$ApiUrl/api/health" -TimeoutSec 5
        return (
            $health.ok -eq $true -and
            $health.loopback_only -eq $true -and
            $health.sap_read.selected_provider -eq "embedded" -and
            $health.sap_read.data.read_only -eq $true
        )
    }
    catch {
        return $false
    }
}

function Test-SiteHealth {
    try {
        $response = Invoke-WebRequest -Uri $SiteHealthUrl -UseBasicParsing -TimeoutSec 5
        return (
            $response.StatusCode -eq 200 -and
            $response.Content -match "SAP Business Agents"
        )
    }
    catch {
        return $false
    }
}

function Wait-ForHealth {
    param(
        [string]$Name,
        [scriptblock]$Probe
    )

    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    do {
        if (& $Probe) {
            return
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    throw "$Name did not become healthy within $StartupTimeoutSeconds seconds."
}

function Stop-ExpectedListener {
    param(
        [string]$Name,
        [int]$Port,
        [string]$ExpectedRoot,
        [string]$Reason = "restart was requested"
    )

    $listenerProcessId = Get-ListenerProcessId -Port $Port
    if ($null -eq $listenerProcessId) {
        return
    }

    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$listenerProcessId"
    $belongsToExpectedRoot = (
        $null -ne $process -and
        -not [string]::IsNullOrWhiteSpace($process.CommandLine) -and
        $process.CommandLine.IndexOf(
            $ExpectedRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -ge 0
    )
    if (-not $belongsToExpectedRoot) {
        throw "$Name port $Port is owned by an unexpected process. Refusing to stop it."
    }

    Write-Step "Stopping $Name process $listenerProcessId because $Reason."
    Stop-Process -Id $listenerProcessId
    $deadline = (Get-Date).AddSeconds(15)
    do {
        if ($null -eq (Get-ListenerProcessId -Port $Port)) {
            return
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    throw "$Name did not release port $Port after it was stopped."
}

function Start-CheckedProcess {
    param(
        [string]$Name,
        [int]$Port,
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [scriptblock]$Probe,
        [string]$StdoutPath,
        [string]$StderrPath
    )

    if (& $Probe) {
        $existingProcessId = Get-ListenerProcessId -Port $Port
        Write-Step "$Name is already healthy on port $Port (PID $existingProcessId)."
        return $existingProcessId
    }

    $conflictingProcessId = Get-ListenerProcessId -Port $Port
    if ($null -ne $conflictingProcessId) {
        Stop-ExpectedListener `
            -Name $Name `
            -Port $Port `
            -ExpectedRoot $WorkingDirectory `
            -Reason "the existing project process is unhealthy or uses a different configuration"
    }
    if (-not (Test-Path -LiteralPath $FilePath -PathType Leaf)) {
        throw "$Name executable was not found: $FilePath"
    }
    if (-not (Test-Path -LiteralPath $WorkingDirectory -PathType Container)) {
        throw "$Name working directory was not found: $WorkingDirectory"
    }

    Write-Step "Starting $Name on port $Port."
    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden `
        -RedirectStandardOutput $StdoutPath `
        -RedirectStandardError $StderrPath `
        -PassThru

    try {
        Wait-ForHealth -Name $Name -Probe $Probe
    }
    catch {
        $startedListenerProcessId = Get-ListenerProcessId -Port $Port
        if ($null -ne $startedListenerProcessId) {
            Stop-Process -Id $startedListenerProcessId -ErrorAction SilentlyContinue
        }
        if (-not $process.HasExited -and $process.Id -ne $startedListenerProcessId) {
            Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $StderrPath) {
            Write-Host "--- $Name stderr ---" -ForegroundColor Yellow
            Get-Content -LiteralPath $StderrPath -Tail 30
        }
        throw
    }

    $listenerProcessId = Get-ListenerProcessId -Port $Port
    Write-Step "$Name is healthy (PID $listenerProcessId)."
    return $listenerProcessId
}

$PlatformPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$SiteRoot = Join-Path $ProjectRoot "site"
$AstroCli = Join-Path $SiteRoot "node_modules\astro\bin\astro.mjs"
$NodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
$NpmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue

if (-not (Test-Path -LiteralPath $PlatformPython -PathType Leaf)) {
    throw "SAPBusinessAgents virtual environment is missing. Follow the first-start commands in README.md."
}
if ($null -eq $NodeCommand -or $null -eq $NpmCommand) {
    throw "Node.js and npm must be installed before the Web UI can start."
}
if (-not (Test-Path -LiteralPath $AstroCli -PathType Leaf)) {
    throw "Web UI dependencies are missing. Run 'npm ci' once under $SiteRoot."
}

if ($Restart) {
    Stop-ExpectedListener -Name "Web UI" -Port $SitePort -ExpectedRoot $ProjectRoot
    Stop-ExpectedListener -Name "SAPBusinessAgents API" -Port $ApiPort -ExpectedRoot $ProjectRoot
}

$env:PUBLIC_SAPBA_API_URL = $ApiUrl
$env:SAPBA_INTERNAL_API_URL = $ApiUrl

Write-Step "Using the Embedded GET-only SAP Provider."

$PlatformProcessId = Start-CheckedProcess `
    -Name "SAPBusinessAgents API" `
    -Port $ApiPort `
    -FilePath $PlatformPython `
    -Arguments @(
        "-m", "sap_business_agents_platform.cli",
        "--host", "127.0.0.1", "--port", [string]$ApiPort
    ) `
    -WorkingDirectory $ProjectRoot `
    -Probe ${function:Test-PlatformHealth} `
    -StdoutPath (Join-Path $LogRoot "platform.stdout.log") `
    -StderrPath (Join-Path $LogRoot "platform.stderr.log")

if (-not (Test-SiteHealth)) {
    $siteListenerProcessId = Get-ListenerProcessId -Port $SitePort
    if ($null -ne $siteListenerProcessId) {
        throw "Web UI cannot start: port $SitePort is already used by PID $siteListenerProcessId."
    }

    Write-Step "Generating the Agent catalog."
    Push-Location $SiteRoot
    try {
        & $NpmCommand.Source run catalog
        if ($LASTEXITCODE -ne 0) {
            throw "Agent catalog generation failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }

    $SiteProcessId = Start-CheckedProcess `
        -Name "Web UI" `
        -Port $SitePort `
        -FilePath $NodeCommand.Source `
        -Arguments @(
            $AstroCli, "dev", "--host", "127.0.0.1", "--port", [string]$SitePort
        ) `
        -WorkingDirectory $SiteRoot `
        -Probe ${function:Test-SiteHealth} `
        -StdoutPath (Join-Path $LogRoot "site.stdout.log") `
        -StderrPath (Join-Path $LogRoot "site.stderr.log")
}
else {
    $SiteProcessId = Get-ListenerProcessId -Port $SitePort
    Write-Step "Web UI is already healthy on port $SitePort (PID $SiteProcessId)."
}

$Services = [ordered]@{
    sap_read = [ordered]@{ provider = "embedded"; healthy = $true }
    api = [ordered]@{ pid = $PlatformProcessId; url = $ApiUrl; healthy = $true }
    site = [ordered]@{ pid = $SiteProcessId; url = $SiteUrl; healthy = $true }
}

$State = [ordered]@{
    started_at = (Get-Date).ToString("o")
    log_directory = $LogRoot
    services = $Services
}
$StatePath = Join-Path $ProjectRoot ".local-data\startup\latest.json"
$State | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $StatePath -Encoding UTF8

Write-Host ""
Write-Host "SAPBusinessAgents is ready." -ForegroundColor Green
Write-Host "Web UI:   $SiteUrl"
Write-Host "Local API: $ApiUrl"
Write-Host "SAP Read:  embedded"
Write-Host "Logs:      $LogRoot"

if (-not $NoBrowser) {
    Write-Step "Opening the Web UI in the default browser."
    Start-Process $SiteUrl
}
