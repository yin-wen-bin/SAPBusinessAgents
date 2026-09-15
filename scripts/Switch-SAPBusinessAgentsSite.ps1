[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$DistPath,
    [Parameter(Mandatory = $true)] [string]$Fingerprint,
    [Parameter(Mandatory = $true)] [string]$ExpectedAgentId,
    [Parameter(Mandatory = $true)] [string]$ExpectedVersion,
    [Parameter(Mandatory = $true)] [string]$ExpectedModule,
    [Parameter(Mandatory = $true)] [string]$ResultPath,
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
        if ($line -match '^\s*TCP\s+(?<local>\S+)\s+\S+\s+LISTENING\s+(?<pid>\d+)\s*$' -and
            $Matches.local -match ':(?<port>\d+)$' -and [int]$Matches.port -eq $Port) {
            return [int]$Matches.pid
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
    return Start-Process -FilePath $NodePath -ArgumentList @(
        $AstroPath, "preview", "--host", "127.0.0.1", "--port", [string]$SitePort,
        "--outDir", $BuildDist
    ) -WorkingDirectory $SiteRoot -WindowStyle Hidden -RedirectStandardOutput $stdout `
      -RedirectStandardError $stderr -PassThru
}

function Wait-Site {
    param([object]$Process, [string]$ExpectedFingerprint, [string]$AgentId, [string]$Version, [string]$Module, [int]$Seconds)
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        $Process.Refresh()
        if ($Process.HasExited) { return $false }
        try {
            if ([string]::IsNullOrWhiteSpace($ExpectedFingerprint)) {
                $home = Invoke-WebRequest -Uri "http://127.0.0.1:$SitePort/zh/" -UseBasicParsing -TimeoutSec 1
                if ($home.StatusCode -eq 200 -and $home.Content.Contains("SAP Business Agents")) { return $true }
            }
            else {
                $marker = Invoke-RestMethod -Uri "http://127.0.0.1:$SitePort/sapba-build.json" -TimeoutSec 1
                if ($marker.fingerprint -eq $ExpectedFingerprint) {
                    if ([string]::IsNullOrWhiteSpace($AgentId) -or
                        [string]::IsNullOrWhiteSpace($Version) -or
                        [string]::IsNullOrWhiteSpace($Module)) {
                        $home = Invoke-WebRequest -Uri "http://127.0.0.1:$SitePort/zh/" -UseBasicParsing -TimeoutSec 1
                        if ($home.StatusCode -eq 200 -and $home.Content.Contains("SAP Business Agents")) { return $true }
                    }
                    else {
                        $detail = Invoke-WebRequest -Uri "http://127.0.0.1:$SitePort/zh/agents/$Module/$AgentId/" -UseBasicParsing -TimeoutSec 1
                        if ($detail.StatusCode -eq 200 -and $detail.Content.Contains($AgentId) -and
                            $detail.Content.Contains($Version)) { return $true }
                    }
                }
            }
        }
        catch { }
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
    rolled_back = $false
    previous_fingerprint = $null
    completed_at = $null
}
$lock = $null
try {
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
    if (Test-Path -LiteralPath $CurrentPath -PathType Leaf) {
        $current = Get-Content -LiteralPath $CurrentPath -Raw | ConvertFrom-Json
        $oldDist = [string]$current.dist_path
        $oldFingerprint = [string]$current.fingerprint
    }
    if ($null -ne $oldPid) {
        $oldCommand = Get-ProcessCommandLine -ProcessId $oldPid
        if ($oldCommand.IndexOf($ProjectRoot, [System.StringComparison]::OrdinalIgnoreCase) -lt 0 -and
            $oldCommand.IndexOf($BuildRoot, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
            throw "Port $SitePort is owned by an unexpected process."
        }
        if ([string]::IsNullOrWhiteSpace($oldDist)) { $oldDist = Get-OutDirFromCommandLine -CommandLine $oldCommand }
        Stop-Site -ProcessId $oldPid
    }

    $newProcess = Start-Site -BuildDist $ResolvedDist -Label "candidate"
    if (-not (Wait-Site -Process $newProcess -ExpectedFingerprint $Fingerprint -AgentId $ExpectedAgentId -Version $ExpectedVersion -Module $ExpectedModule -Seconds 15)) {
        if (-not $newProcess.HasExited) { Stop-Process -Id $newProcess.Id -ErrorAction SilentlyContinue }
        if (-not [string]::IsNullOrWhiteSpace($oldDist) -and (Test-Path -LiteralPath $oldDist -PathType Container)) {
            $oldMarkerPath = Join-Path $oldDist "sapba-build.json"
            $rollbackFingerprint = $oldFingerprint
            $rollbackAgent = ""
            $rollbackVersion = ""
            $rollbackModule = ""
            if (Test-Path -LiteralPath $oldMarkerPath -PathType Leaf) {
                $oldMarker = Get-Content -LiteralPath $oldMarkerPath -Raw | ConvertFrom-Json
                $rollbackFingerprint = [string]$oldMarker.fingerprint
                $rollbackAgent = [string]$oldMarker.agent_id
                $rollbackVersion = [string]$oldMarker.version
                $rollbackModule = [string]$oldMarker.catalog_module
            }
            $rollback = Start-Site -BuildDist $oldDist -Label "rollback"
            if (Wait-Site -Process $rollback -ExpectedFingerprint $rollbackFingerprint -AgentId $rollbackAgent -Version $rollbackVersion -Module $rollbackModule -Seconds 15) {
                $result.rolled_back = $true
            }
        }
        $result.failure_code = "site_refresh_health_failed"
        throw "The candidate Web UI failed health checks."
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
