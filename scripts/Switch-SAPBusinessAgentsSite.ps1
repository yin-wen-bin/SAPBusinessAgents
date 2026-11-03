[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$DistPath,
    [Parameter(Mandatory = $true)] [string]$Fingerprint,
    [Parameter(Mandatory = $true)] [string]$ExpectedAgentId,
    [Parameter(Mandatory = $true)] [string]$ExpectedVersion,
    [Parameter(Mandatory = $true)] [string]$ExpectedModule,
    [Parameter(Mandatory = $true)] [string]$ResultPath,
    [string]$ApiUrl = "",
    [ValidateSet("active", "inactive")] [string]$ExpectedLifecycleState = "active",
    [ValidateRange(1, 65535)] [int]$SitePort = 4321
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ScriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $ScriptDirectory))
$BuildRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot ".local-data\site-builds"))
$ResolvedDist = [System.IO.Path]::GetFullPath($DistPath)
$LockPath = Join-Path $ProjectRoot ".local-data\deployment.lock"
$CurrentPath = Join-Path $BuildRoot "current.json"
$PreviousPath = Join-Path $BuildRoot "previous.json"
$NodePath = (Get-Command node.exe -ErrorAction Stop).Source
$AstroPath = Join-Path $ProjectRoot "site\node_modules\astro\bin\astro.mjs"
$SiteRoot = Join-Path $ProjectRoot "site"
$LogRoot = Join-Path $ProjectRoot ".local-data\site-releases\switches"
$Timestamp = Get-Date -Format "yyyyMMddTHHmmssfff"
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ResultPath) | Out-Null

function Write-JsonAtomic {
    param([string]$Path, [object]$Value)
    $temporary = "$Path.tmp-$PID"
    $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Get-ListenerProcessId {
    param([int]$Port)
    $netstatPath = Join-Path $env:SystemRoot "System32\netstat.exe"
    foreach ($line in (& $netstatPath -ano -p TCP)) {
        if ($line -match '^\s*TCP\s+(?<local>\S+)\s+\S+\s+LISTENING\s+(?<pid>\d+)\s*$') {
            $listenerPid = [int]$Matches.pid
            $localEndpoint = [string]$Matches.local
            if ($localEndpoint -match ':(?<port>\d+)$' -and [int]$Matches.port -eq $Port) {
                if ($listenerPid -gt 0) { return $listenerPid }
            }
        }
    }
    return $null
}

function Get-ProcessCommandLine {
    param([int]$ProcessId)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId"
    if ($null -eq $process) { return "" }
    return [string]$process.CommandLine
}

function Stop-ManagedPreview {
    # Astro 7 may detach its preview launcher. Its own project-scoped CLI
    # verifies the live lock before stopping that project's recorded server.
    $previewLock = Join-Path $SiteRoot ".astro\preview.json"
    if (-not (Test-Path -LiteralPath $previewLock -PathType Leaf)) { return $false }
    $preview = Get-Content -LiteralPath $previewLock -Raw | ConvertFrom-Json
    if ([int]$preview.port -ne $SitePort -or [int]$preview.pid -le 0) { return $false }
    $previewUri = [uri][string]$preview.url
    if ($previewUri.Scheme -ne "http" -or $previewUri.Host -notin @("127.0.0.1", "localhost") -or $previewUri.Port -ne $SitePort) {
        throw "The project preview lock does not identify the requested local server."
    }
    Push-Location -LiteralPath $SiteRoot
    try {
        $previewStatus = (& $NodePath $AstroPath preview status 2>&1 | Out-String)
        if ($LASTEXITCODE -ne 0 -or $previewStatus -notmatch ("pid " + [regex]::Escape([string]$preview.pid) + "[,)]")) {
            throw "The project preview process could not be confirmed."
        }
        $previewStop = (& $NodePath $AstroPath preview stop 2>&1 | Out-String)
        if ($LASTEXITCODE -ne 0 -or $previewStop -notmatch ("Stopped preview server \(pid " + [regex]::Escape([string]$preview.pid) + "\)")) {
            throw "The project preview process did not confirm shutdown."
        }
    }
    finally { Pop-Location }
    return $true
}

function Get-OutDirFromCommandLine {
    param([string]$CommandLine)
    if ($CommandLine -match '(?i)--outDir\s+(?:"(?<quoted>[^"]+)"|(?<plain>\S+))') {
        return [System.IO.Path]::GetFullPath($(if ($Matches.quoted) { $Matches.quoted } else { $Matches.plain }))
    }
    return $null
}

function Start-Site {
    param([string]$BuildDist, [string]$Label)
    $stdout = Join-Path $LogRoot "$Timestamp-$Label.stdout.log"
    $stderr = Join-Path $LogRoot "$Timestamp-$Label.stderr.log"
    $oldSiteBase = [Environment]::GetEnvironmentVariable("PUBLIC_SITE_BASE", "Process")
    $oldApiUrl = [Environment]::GetEnvironmentVariable("PUBLIC_SAPBA_API_URL", "Process")
    try {
        $env:PUBLIC_SITE_BASE = "/"
        $env:PUBLIC_SAPBA_API_URL = $ApiUrl
        return Start-Process -FilePath $NodePath -ArgumentList @(
            $AstroPath, "preview", "--host", "127.0.0.1", "--port", [string]$SitePort,
            "--outDir", $BuildDist, "--ignore-lock"
        ) -WorkingDirectory $SiteRoot -WindowStyle Hidden -RedirectStandardOutput $stdout `
          -RedirectStandardError $stderr -PassThru
    }
    finally {
        if ($null -eq $oldSiteBase) { Remove-Item Env:PUBLIC_SITE_BASE -ErrorAction SilentlyContinue }
        else { $env:PUBLIC_SITE_BASE = $oldSiteBase }
        if ($null -eq $oldApiUrl) { Remove-Item Env:PUBLIC_SAPBA_API_URL -ErrorAction SilentlyContinue }
        else { $env:PUBLIC_SAPBA_API_URL = $oldApiUrl }
    }
}

function Wait-Site {
    param([object]$Process, [string]$ExpectedFingerprint, [string]$AgentId, [string]$Version, [string]$Module, [int]$Seconds, [string]$LifecycleState = "active")
    $script:LastHealthFailure = ""
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        $Process.Refresh()
        if ($Process.HasExited) { return $false }
        try {
            if ([string]::IsNullOrWhiteSpace($ExpectedFingerprint)) {
                $siteResponse = Invoke-WebRequest -Uri "http://127.0.0.1:$SitePort/zh/" -UseBasicParsing -TimeoutSec 1
                if ($siteResponse.StatusCode -eq 200 -and $siteResponse.Content.Contains("SAP Business Agents")) { return $true }
            }
            else {
                $marker = Invoke-RestMethod -Uri "http://127.0.0.1:$SitePort/sapba-build.json" -TimeoutSec 1
                if ($marker.fingerprint -eq $ExpectedFingerprint) {
                    $markerState = "active"
                    if ($marker.PSObject.Properties.Name -contains "lifecycle_state") { $markerState = [string]$marker.lifecycle_state }
                    if ($markerState -ne $LifecycleState) { throw "The build lifecycle state does not match." }
                    if ($LifecycleState -eq "inactive") {
                        if ([string]::IsNullOrWhiteSpace($AgentId) -or [string]::IsNullOrWhiteSpace($Module)) {
                            throw "Inactive health checks require the exact Agent identity."
                        }
                        foreach ($language in @("zh", "en")) {
                            # Read only the status headers. In particular,
                            # do not throw for or drain a 404 error body.
                            Add-Type -AssemblyName System.Net.Http
                            $detailHandler = [System.Net.Http.HttpClientHandler]::new()
                            $detailHandler.UseProxy = $false
                            $detailClient = [System.Net.Http.HttpClient]::new($detailHandler)
                            $detailClient.Timeout = [TimeSpan]::FromSeconds(1)
                            $detailMessage = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Head, "http://127.0.0.1:$SitePort/$language/agents/$Module/$AgentId/")
                            $detailResponse = $null
                            try {
                                $detailResponse = $detailClient.SendAsync($detailMessage, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
                                $detailStatus = [int]$detailResponse.StatusCode
                            }
                            finally {
                                if ($null -ne $detailResponse) { $detailResponse.Dispose() }
                                $detailMessage.Dispose()
                                $detailClient.Dispose()
                            }
                            if ($detailStatus -ne 404) { throw "The inactive Agent is still publicly available." }
                        }
                        $siteResponse = Invoke-WebRequest -Uri "http://127.0.0.1:$SitePort/zh/" -UseBasicParsing -TimeoutSec 1
                        if ($siteResponse.StatusCode -eq 200 -and $siteResponse.Content.Contains("SAP Business Agents")) { return $true }
                        throw "The inactive build home page is unavailable."
                    }
                    if ([string]::IsNullOrWhiteSpace($AgentId) -or
                        [string]::IsNullOrWhiteSpace($Version) -or
                        [string]::IsNullOrWhiteSpace($Module)) {
                        $siteResponse = Invoke-WebRequest -Uri "http://127.0.0.1:$SitePort/zh/" -UseBasicParsing -TimeoutSec 1
                        if ($siteResponse.StatusCode -eq 200 -and $siteResponse.Content.Contains("SAP Business Agents")) { return $true }
                    }
                    else {
                        $detail = Invoke-WebRequest -Uri "http://127.0.0.1:$SitePort/zh/agents/$Module/$AgentId/" -UseBasicParsing -TimeoutSec 1
                        if ($detail.StatusCode -eq 200 -and $detail.Content.Contains($AgentId) -and
                            $detail.Content.Contains($Version)) { return $true }
                    }
                }
            }
        }
        catch { $script:LastHealthFailure = [string]$_.Exception.Message }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Stop-Site {
    param([int]$ProcessId)
    Stop-Process -Id $ProcessId -ErrorAction Stop
    $deadline = (Get-Date).AddSeconds(15)
    do {
        if ($null -eq (Get-ListenerProcessId -Port $SitePort)) { return }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    throw "The previous Web UI did not release its port."
}

$result = [ordered]@{
    status = "failed"
    fingerprint = $Fingerprint
    failure_code = $null
    failure_detail = $null
    rolled_back = $false
    previous_fingerprint = $null
    completed_at = $null
}
$lock = $null
try {
    if ([string]::IsNullOrWhiteSpace($ApiUrl)) {
        $ApiUrl = [Environment]::GetEnvironmentVariable("PUBLIC_SAPBA_API_URL", "Process")
    }
    if ([string]::IsNullOrWhiteSpace($ApiUrl)) { $ApiUrl = "http://127.0.0.1:8765" }
    $prefix = $BuildRoot.TrimEnd('\') + [System.IO.Path]::DirectorySeparatorChar
    if (-not $ResolvedDist.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Prepared Web UI is outside the immutable build root."
    }
    $markerPath = Join-Path $ResolvedDist "sapba-build.json"
    if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) { throw "Prepared Web UI marker is missing." }
    $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
    if ($marker.fingerprint -ne $Fingerprint) {
        throw "Prepared Web UI marker does not match the requested publication."
    }

    $deadline = (Get-Date).AddSeconds(60)
    do {
        try {
            $lock = [System.IO.File]::Open($LockPath, [System.IO.FileMode]::OpenOrCreate,
                [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
            break
        }
        catch [System.IO.IOException] {
            if ((Get-Date) -ge $deadline) { throw "Another startup or deployment operation still owns the Web UI lock." }
            Start-Sleep -Milliseconds 250
        }
    } while ($null -eq $lock)

    $oldPid = Get-ListenerProcessId -Port $SitePort
    $oldDist = $null
    $oldFingerprint = $null
    $oldPointerPid = $null
    if (Test-Path -LiteralPath $CurrentPath -PathType Leaf) {
        $current = Get-Content -LiteralPath $CurrentPath -Raw | ConvertFrom-Json
        $oldDist = [string]$current.dist_path
        $oldFingerprint = [string]$current.fingerprint
        if ($current.PSObject.Properties.Name -contains "pid" -and $null -ne $current.pid) {
            $oldPointerPid = [int]$current.pid
        }
    }
    if (Stop-ManagedPreview) {
        $oldPid = $null
        if ([string]::IsNullOrWhiteSpace($oldDist)) { $oldDist = Join-Path $SiteRoot "dist" }
    }
    elseif ($null -eq $oldPid -and $null -ne $oldPointerPid -and -not [string]::IsNullOrWhiteSpace($oldDist)) {
        # Some Windows sessions redact socket-owner PIDs. A current pointer
        # needs both its exact process arguments and its served fingerprint.
        $pointerCommand = Get-ProcessCommandLine -ProcessId $oldPointerPid
        $pointerDist = [System.IO.Path]::GetFullPath($oldDist)
        if ($pointerDist.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase) -and
            $pointerCommand.IndexOf($pointerDist, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
            $pointerCommand -match ("--port\s+" + $SitePort + "(\s|$)")) {
            $servedPointer = Invoke-RestMethod -Uri "http://127.0.0.1:$SitePort/sapba-build.json" -TimeoutSec 2
            if ($servedPointer.fingerprint -eq $oldFingerprint) { $oldPid = $oldPointerPid }
        }
    }
    if ($null -ne $oldPid) {
        $managedByCurrentPointer = $false
        if ($null -ne $oldPointerPid -and $oldPointerPid -eq $oldPid -and
            -not [string]::IsNullOrWhiteSpace($oldDist)) {
            $resolvedOldDist = [System.IO.Path]::GetFullPath($oldDist)
            $managedByCurrentPointer = $resolvedOldDist.StartsWith(
                $prefix, [System.StringComparison]::OrdinalIgnoreCase
            ) -and (Test-Path -LiteralPath $resolvedOldDist -PathType Container)
            if ($managedByCurrentPointer) { $oldDist = $resolvedOldDist }
        }
        if (-not $managedByCurrentPointer) {
            $oldCommand = Get-ProcessCommandLine -ProcessId $oldPid
            if ($oldCommand.IndexOf($ProjectRoot, [System.StringComparison]::OrdinalIgnoreCase) -lt 0 -and
                $oldCommand.IndexOf($BuildRoot, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
                throw "Port $SitePort is owned by an unexpected process."
            }
            if ([string]::IsNullOrWhiteSpace($oldDist)) {
                $oldDist = Get-OutDirFromCommandLine -CommandLine $oldCommand
            }
        }
        Stop-Site -ProcessId $oldPid
    }

    $newProcess = Start-Site -BuildDist $ResolvedDist -Label "candidate"
    if (-not (Wait-Site -Process $newProcess -ExpectedFingerprint $Fingerprint -AgentId $ExpectedAgentId -Version $ExpectedVersion -Module $ExpectedModule -Seconds 15 -LifecycleState $ExpectedLifecycleState)) {
        if (-not $newProcess.HasExited) { Stop-Process -Id $newProcess.Id -ErrorAction SilentlyContinue }
        if (-not [string]::IsNullOrWhiteSpace($oldDist) -and (Test-Path -LiteralPath $oldDist -PathType Container)) {
            $oldMarkerPath = Join-Path $oldDist "sapba-build.json"
            $rollbackFingerprint = $oldFingerprint
            $rollbackAgent = ""
            $rollbackVersion = ""
            $rollbackModule = ""
            $rollbackState = "active"
            if (Test-Path -LiteralPath $oldMarkerPath -PathType Leaf) {
                $oldMarker = Get-Content -LiteralPath $oldMarkerPath -Raw | ConvertFrom-Json
                $rollbackFingerprint = [string]$oldMarker.fingerprint
                $rollbackAgent = [string]$oldMarker.agent_id
                $rollbackVersion = [string]$oldMarker.version
                $rollbackModule = [string]$oldMarker.catalog_module
                if ($oldMarker.PSObject.Properties.Name -contains "lifecycle_state") { $rollbackState = [string]$oldMarker.lifecycle_state }
            }
            $rollback = Start-Site -BuildDist $oldDist -Label "rollback"
            if (Wait-Site -Process $rollback -ExpectedFingerprint $rollbackFingerprint -AgentId $rollbackAgent -Version $rollbackVersion -Module $rollbackModule -Seconds 15 -LifecycleState $rollbackState) {
                $result.rolled_back = $true
                Write-JsonAtomic -Path $CurrentPath -Value ([ordered]@{
                    fingerprint = $rollbackFingerprint; dist_path = $oldDist; pid = $rollback.Id;
                    agent_id = $rollbackAgent; version = $rollbackVersion; lifecycle_state = $rollbackState;
                    recorded_at = (Get-Date).ToString("o")
                })
            }
        }
        $result.failure_code = "site_refresh_health_failed"
        throw ("The candidate Web UI failed health checks. " + $script:LastHealthFailure)
    }

    if (-not [string]::IsNullOrWhiteSpace($oldDist)) {
        Write-JsonAtomic -Path $PreviousPath -Value ([ordered]@{
            fingerprint = $oldFingerprint; dist_path = $oldDist; recorded_at = (Get-Date).ToString("o")
        })
    }
    Write-JsonAtomic -Path $CurrentPath -Value ([ordered]@{
        fingerprint = $Fingerprint; dist_path = $ResolvedDist; pid = $newProcess.Id;
        agent_id = $ExpectedAgentId; version = $ExpectedVersion; recorded_at = (Get-Date).ToString("o")
    })
    $result.status = "completed"
    $result.previous_fingerprint = $oldFingerprint
}
catch {
    $result.failure_detail = [string]$_.Exception.Message
    if ([string]::IsNullOrWhiteSpace([string]$result.failure_code)) {
        $result.failure_code = "site_refresh_failed"
    }
}
finally {
    $result.completed_at = (Get-Date).ToString("o")
    if ($null -ne $lock) { $lock.Dispose() }
    Write-JsonAtomic -Path $ResultPath -Value $result
}

if ($result.status -ne "completed") { exit 1 }
