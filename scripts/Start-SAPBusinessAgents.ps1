[CmdletBinding()]
param(
    [ValidateRange(1, 65535)] [int]$ApiPort = 8765,
    [ValidateRange(1, 65535)] [int]$SitePort = 4321,
    [ValidateRange(5, 300)] [int]$StartupTimeoutSeconds = 90,
    [switch]$NoBrowser,
    [switch]$Restart,
    [switch]$Dev,
    [switch]$RebuildSite
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$LaunchStartedAt = Get-Date
$ScriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $ScriptDirectory))
$ApiUrl = "http://127.0.0.1:$ApiPort"
$SiteHealthUrl = "http://127.0.0.1:$SitePort/"
$SiteUrl = "http://127.0.0.1:$SitePort/zh/"
$Timestamp = Get-Date -Format "yyyyMMddTHHmmss"
$StartupRoot = Join-Path $ProjectRoot ".local-data\startup"
$LogRoot = Join-Path $StartupRoot $Timestamp
$LauncherLog = Join-Path $LogRoot "launcher.log"
$LastAttemptPath = Join-Path $StartupRoot "last-attempt.json"
$LatestPath = Join-Path $StartupRoot "latest.json"
$ResultPath = Join-Path $LogRoot "result.json"
$SiteBuildRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot ".local-data\site-builds"))
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
New-Item -ItemType Directory -Force -Path $SiteBuildRoot | Out-Null

$PhaseDurations = [ordered]@{}
$CurrentPhase = "preflight"
$StartedProcesses = New-Object System.Collections.ArrayList
$StartedServices = New-Object System.Collections.ArrayList
$PlatformProcessId = $null
$SiteProcessId = $null
$PlatformHealthy = $false
$SiteHealthy = $false
$SiteMode = if ($Dev) { "dev" } else { "preview" }
$SiteFingerprint = $null
$SiteBuildReused = $false
$SiteBuildDurationMs = 0

function Write-LauncherMessage {
    param(
        [string]$Message,
        [ValidateSet("INFO", "WARN", "ERROR")] [string]$Level = "INFO",
        [ConsoleColor]$Color = [ConsoleColor]::Cyan
    )
    $line = "{0} [{1}] {2}" -f (Get-Date).ToString("o"), $Level, $Message
    Add-Content -LiteralPath $LauncherLog -Value $line -Encoding UTF8
    Write-Host "[SAPBusinessAgents] $Message" -ForegroundColor $Color
}

function Invoke-StartupPhase {
    param([string]$Name, [scriptblock]$Action)
    $script:CurrentPhase = $Name
    $started = Get-Date
    Write-LauncherMessage "START $Name"
    try { & $Action }
    finally {
        $duration = [int][Math]::Round(((Get-Date) - $started).TotalMilliseconds)
        $script:PhaseDurations[$Name] = $duration
        Write-LauncherMessage "END $Name (${duration} ms)"
    }
}

function Test-PortListening {
    param([int]$Port)
    $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    foreach ($listener in $listeners) {
        if ($listener.Port -eq $Port) { return $true }
    }
    return $false
}

function Get-ListenerProcessId {
    param([int]$Port)
    if (-not (Test-PortListening -Port $Port)) { return $null }
    try {
        $netstatPath = Join-Path $env:SystemRoot "System32\netstat.exe"
        $lines = & $netstatPath -ano -p TCP
        foreach ($line in $lines) {
            if ($line -notmatch '^\s*TCP\s+(?<local>\S+)\s+\S+\s+LISTENING\s+(?<pid>\d+)\s*$') { continue }
            $localEndpoint = $Matches.local
            $processId = [int]$Matches.pid
            if ($localEndpoint -match ':(?<port>\d+)$' -and [int]$Matches.port -eq $Port) {
                return $processId
            }
        }
    }
    catch {
        Write-LauncherMessage "netstat PID lookup failed for port $Port; using the PowerShell fallback." "WARN" Yellow
    }
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -in @("127.0.0.1", "::1", "0.0.0.0", "::") } |
        Select-Object -First 1
    if ($null -eq $listener) { return $null }
    return [int]$listener.OwningProcess
}

function Test-PlatformHealth {
    try {
        $health = Invoke-RestMethod -Uri "$ApiUrl/api/health" -TimeoutSec 1
        return ($health.ok -eq $true -and $health.loopback_only -eq $true -and
            $health.sap_read.selected_provider -eq "embedded" -and $health.sap_read.data.read_only -eq $true)
    }
    catch { return $false }
}

function Test-SiteHealth {
    try {
        $response = Invoke-WebRequest -Uri $SiteHealthUrl -UseBasicParsing -TimeoutSec 1
        return ($response.StatusCode -eq 200 -and $response.Content -match "SAP Business Agents")
    }
    catch { return $false }
}

function Assert-PathWithinRoot {
    param([string]$Path, [string]$Root)
    $resolvedPath = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    $resolvedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
    $prefix = $resolvedRoot + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolvedPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to operate outside the expected local cache root: $resolvedPath"
    }
}

function Test-ProcessBelongsToProject {
    param([int]$ProcessId, [string]$ExpectedRoot)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId"
    return ($null -ne $process -and -not [string]::IsNullOrWhiteSpace($process.CommandLine) -and
        $process.CommandLine.IndexOf($ExpectedRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
}

function Get-ExistingSiteMode {
    param([int]$ProcessId)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId"
    if ($null -eq $process -or [string]::IsNullOrWhiteSpace($process.CommandLine)) { return "existing" }
    if ($process.CommandLine -match '(?i)astro(?:\.mjs)?\s+preview(?:\s|$)') { return "preview" }
    if ($process.CommandLine -match '(?i)astro(?:\.mjs)?\s+dev(?:\s|$)') { return "dev" }
    return "existing"
}

function Stop-ExpectedListener {
    param(
        [string]$Name,
        [int]$Port,
        [string]$ExpectedRoot,
        [string]$Reason = "restart was requested"
    )
    $listenerProcessId = Get-ListenerProcessId -Port $Port
    if ($null -eq $listenerProcessId) { return }
    if (-not (Test-ProcessBelongsToProject -ProcessId $listenerProcessId -ExpectedRoot $ExpectedRoot)) {
        throw "$Name port $Port is owned by an unexpected process (PID $listenerProcessId). Refusing to stop it."
    }
    Write-LauncherMessage "Stopping $Name process $listenerProcessId because $Reason."
    Stop-Process -Id $listenerProcessId
    $deadline = (Get-Date).AddSeconds(15)
    do {
        if (-not (Test-PortListening -Port $Port)) { return }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    throw "$Name did not release port $Port after it was stopped."
}

function Start-LocalProcess {
    param(
        [string]$Name, [int]$Port, [string]$FilePath, [string[]]$Arguments,
        [string]$WorkingDirectory, [string]$StdoutPath, [string]$StderrPath
    )
    if (Test-PortListening -Port $Port) {
        $owner = Get-ListenerProcessId -Port $Port
        throw "$Name cannot start: port $Port is already used by PID $owner."
    }
    Write-LauncherMessage "Starting $Name on port $Port."
    $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath -PassThru
    [void]$script:StartedProcesses.Add($process)
    [void]$script:StartedServices.Add([pscustomobject]@{ Name = $Name; Port = $Port })
    return $process
}

function Write-ProcessLogTail {
    param([string]$Name, [string]$StdoutPath, [string]$StderrPath)
    foreach ($entry in @(
        [pscustomobject]@{ Label = "stdout"; Path = $StdoutPath },
        [pscustomobject]@{ Label = "stderr"; Path = $StderrPath }
    )) {
        if (Test-Path -LiteralPath $entry.Path) {
            Write-Host "--- $Name $($entry.Label) ---" -ForegroundColor Yellow
            Get-Content -LiteralPath $entry.Path -Tail 30
        }
    }
}

function Get-SiteFingerprint {
    param([string]$SiteRoot, [string]$AgentsRoot, [string]$NodeVersion)
    $files = New-Object System.Collections.Generic.List[System.IO.FileInfo]
    foreach ($directory in @((Join-Path $SiteRoot "src"), (Join-Path $SiteRoot "scripts"))) {
        Get-ChildItem -LiteralPath $directory -Recurse -File |
            Where-Object { $_.FullName -notlike (Join-Path $SiteRoot "src\generated\*") } |
            ForEach-Object { $files.Add($_) }
    }
    Get-ChildItem -LiteralPath $AgentsRoot -Recurse -File -Filter "agent.json" | ForEach-Object { $files.Add($_) }
    foreach ($name in @("astro.config.mjs", "package.json", "package-lock.json")) {
        $files.Add((Get-Item -LiteralPath (Join-Path $SiteRoot $name)))
    }

    $material = New-Object System.Text.StringBuilder
    foreach ($file in ($files | Sort-Object FullName -Unique)) {
        $stream = [System.IO.File]::OpenRead($file.FullName)
        $hasher = [System.Security.Cryptography.SHA256]::Create()
        try { $hash = [System.BitConverter]::ToString($hasher.ComputeHash($stream)).Replace("-", "").ToLowerInvariant() }
        finally { $hasher.Dispose(); $stream.Dispose() }
        $relative = $file.FullName.Substring($ProjectRoot.Length).TrimStart('\').Replace('\', '/')
        [void]$material.AppendLine("$relative|$hash")
    }
    [void]$material.AppendLine("node|$NodeVersion")
    [void]$material.AppendLine("site_base|/")
    [void]$material.AppendLine("api_url|$ApiUrl")
    $finalHasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($material.ToString())
        return [System.BitConverter]::ToString($finalHasher.ComputeHash($bytes)).Replace("-", "").ToLowerInvariant()
    }
    finally { $finalHasher.Dispose() }
}

function Test-SiteBuild {
    param([string]$DistPath)
    return ((Test-Path -LiteralPath (Join-Path $DistPath "index.html") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $DistPath "zh\index.html") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $DistPath "_astro") -PathType Container))
}

function Invoke-SiteCommand {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Site command failed with exit code ${LASTEXITCODE}: $Command $($Arguments -join ' ')"
    }
}

function Build-SiteCache {
    param([string]$Fingerprint, [string]$SiteRoot, [string]$NodePath, [string]$NpmPath, [string]$AstroPath)
    $finalRoot = Join-Path $SiteBuildRoot $Fingerprint
    $finalDist = Join-Path $finalRoot "dist"
    $temporaryRoot = Join-Path $SiteBuildRoot (".{0}.tmp-{1}-{2}" -f $Fingerprint, $PID, $Timestamp)
    $temporaryDist = Join-Path $temporaryRoot "dist"
    Assert-PathWithinRoot -Path $finalRoot -Root $SiteBuildRoot
    Assert-PathWithinRoot -Path $temporaryRoot -Root $SiteBuildRoot
    New-Item -ItemType Directory -Force -Path $temporaryRoot | Out-Null
    $oldSiteBase = [Environment]::GetEnvironmentVariable("PUBLIC_SITE_BASE", "Process")
    $oldApiUrl = [Environment]::GetEnvironmentVariable("PUBLIC_SAPBA_API_URL", "Process")
    try {
        $env:PUBLIC_SITE_BASE = "/"
        $env:PUBLIC_SAPBA_API_URL = $ApiUrl
        Push-Location $SiteRoot
        try {
            Invoke-SiteCommand -Command $NpmPath -Arguments @("run", "validate")
            Invoke-SiteCommand -Command $NpmPath -Arguments @("run", "catalog")
            Invoke-SiteCommand -Command $NodePath -Arguments @($AstroPath, "build", "--outDir", $temporaryDist)
        }
        finally { Pop-Location }
        if (-not (Test-SiteBuild -DistPath $temporaryDist)) { throw "The local Web UI build is incomplete." }
        if (Test-Path -LiteralPath $finalRoot) { Remove-Item -LiteralPath $finalRoot -Recurse -Force }
        Move-Item -LiteralPath $temporaryRoot -Destination $finalRoot
        return $finalDist
    }
    finally {
        if ($null -eq $oldSiteBase) { Remove-Item Env:PUBLIC_SITE_BASE -ErrorAction SilentlyContinue }
        else { $env:PUBLIC_SITE_BASE = $oldSiteBase }
        if ($null -eq $oldApiUrl) { Remove-Item Env:PUBLIC_SAPBA_API_URL -ErrorAction SilentlyContinue }
        else { $env:PUBLIC_SAPBA_API_URL = $oldApiUrl }
        if (Test-Path -LiteralPath $temporaryRoot) {
            Assert-PathWithinRoot -Path $temporaryRoot -Root $SiteBuildRoot
            Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
        }
    }
}

function Remove-OldSiteBuilds {
    param([string]$CurrentFingerprint)
    $builds = Get-ChildItem -LiteralPath $SiteBuildRoot -Directory |
        Where-Object { $_.Name -notmatch '^\.' -and (Test-SiteBuild -DistPath (Join-Path $_.FullName "dist")) } |
        Sort-Object LastWriteTime -Descending
    $keep = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    [void]$keep.Add($CurrentFingerprint)
    foreach ($build in $builds) {
        if ($keep.Count -lt 3) { [void]$keep.Add($build.Name); continue }
        if (-not $keep.Contains($build.Name)) {
            Assert-PathWithinRoot -Path $build.FullName -Root $SiteBuildRoot
            Remove-Item -LiteralPath $build.FullName -Recurse -Force
            Write-LauncherMessage "Removed old Web UI cache $($build.Name)."
        }
    }
}

function Wait-ForServices {
    param(
        [object]$ApiProcess, [object]$WebProcess,
        [string]$ApiStdout, [string]$ApiStderr, [string]$SiteStdout, [string]$SiteStderr
    )
    $script:CurrentPhase = "api_health_wait/site_health_wait"
    $started = Get-Date
    $apiCompleted = $script:PlatformHealthy
    $siteCompleted = $script:SiteHealthy
    Write-LauncherMessage "START api_health_wait"
    Write-LauncherMessage "START site_health_wait"
    if ($apiCompleted) { $script:PhaseDurations["api_health_wait"] = 0; Write-LauncherMessage "END api_health_wait (0 ms; reused healthy service)" }
    if ($siteCompleted) { $script:PhaseDurations["site_health_wait"] = 0; Write-LauncherMessage "END site_health_wait (0 ms; reused healthy service)" }
    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    do {
        if (-not $apiCompleted -and $null -ne $ApiProcess) {
            $ApiProcess.Refresh()
            if ($ApiProcess.HasExited) {
                Write-ProcessLogTail -Name "SAPBusinessAgents API" -StdoutPath $ApiStdout -StderrPath $ApiStderr
                throw "SAPBusinessAgents API exited before becoming healthy (exit code $($ApiProcess.ExitCode))."
            }
        }
        if (-not $siteCompleted -and $null -ne $WebProcess) {
            $WebProcess.Refresh()
            if ($WebProcess.HasExited) {
                Write-ProcessLogTail -Name "Web UI" -StdoutPath $SiteStdout -StderrPath $SiteStderr
                throw "Web UI exited before becoming healthy (exit code $($WebProcess.ExitCode))."
            }
        }
        if (-not $apiCompleted -and (Test-PlatformHealth)) {
            $apiCompleted = $true; $script:PlatformHealthy = $true
            $script:PlatformProcessId = Get-ListenerProcessId -Port $ApiPort
            $duration = [int][Math]::Round(((Get-Date) - $started).TotalMilliseconds)
            $script:PhaseDurations["api_health_wait"] = $duration
            Write-LauncherMessage "END api_health_wait (${duration} ms)"
        }
        if (-not $siteCompleted -and (Test-SiteHealth)) {
            $siteCompleted = $true; $script:SiteHealthy = $true
            $script:SiteProcessId = Get-ListenerProcessId -Port $SitePort
            $duration = [int][Math]::Round(((Get-Date) - $started).TotalMilliseconds)
            $script:PhaseDurations["site_health_wait"] = $duration
            Write-LauncherMessage "END site_health_wait (${duration} ms)"
        }
        if ($apiCompleted -and $siteCompleted) { return }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    $pending = @()
    if (-not $apiCompleted) { $pending += "SAPBusinessAgents API" }
    if (-not $siteCompleted) { $pending += "Web UI" }
    throw "$($pending -join ' and ') did not become healthy within $StartupTimeoutSeconds seconds."
}

function New-AttemptState {
    param([string]$Status, [object]$Failure = $null)
    $completedAt = Get-Date
    return [ordered]@{
        status = $Status
        started_at = $LaunchStartedAt.ToString("o")
        completed_at = $completedAt.ToString("o")
        total_duration_ms = [int][Math]::Round(($completedAt - $LaunchStartedAt).TotalMilliseconds)
        site_mode = $SiteMode
        site_cache = [ordered]@{ fingerprint = $SiteFingerprint; reused = $SiteBuildReused; build_duration_ms = $SiteBuildDurationMs }
        phases = $PhaseDurations
        services = [ordered]@{
            sap_read = [ordered]@{ provider = "embedded"; healthy = $true }
            api = [ordered]@{ pid = $PlatformProcessId; url = $ApiUrl; healthy = $PlatformHealthy }
            site = [ordered]@{ pid = $SiteProcessId; url = $SiteUrl; healthy = $SiteHealthy }
        }
        failure = $Failure
        log_directory = $LogRoot
    }
}

function Save-AttemptState {
    param([object]$State, [switch]$Successful)
    $json = $State | ConvertTo-Json -Depth 8
    $json | Set-Content -LiteralPath $ResultPath -Encoding UTF8
    $json | Set-Content -LiteralPath $LastAttemptPath -Encoding UTF8
    if ($Successful) { $json | Set-Content -LiteralPath $LatestPath -Encoding UTF8 }
}

$PlatformPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$SiteRoot = Join-Path $ProjectRoot "site"
$AgentsRoot = Join-Path $ProjectRoot "agents"
$AstroCli = Join-Path $SiteRoot "node_modules\astro\bin\astro.mjs"
$PlatformStdout = Join-Path $LogRoot "platform.stdout.log"
$PlatformStderr = Join-Path $LogRoot "platform.stderr.log"
$SiteStdout = Join-Path $LogRoot "site.stdout.log"
$SiteStderr = Join-Path $LogRoot "site.stderr.log"
$NodeCommand = $null
$NpmCommand = $null
$NodeVersion = $null

try {
    Invoke-StartupPhase -Name "preflight" -Action {
        if ($Dev -and $RebuildSite) { throw "-RebuildSite cannot be combined with -Dev because development mode does not use the build cache." }
        if (-not (Test-Path -LiteralPath $PlatformPython -PathType Leaf)) { throw "SAPBusinessAgents virtual environment is missing. Follow the first-start commands in README.md." }
        $script:NodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
        $script:NpmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
        if ($null -eq $NodeCommand -or $null -eq $NpmCommand) { throw "Node.js and npm must be installed before the Web UI can start." }
        if (-not (Test-Path -LiteralPath $AstroCli -PathType Leaf)) { throw "Web UI dependencies are missing. Run 'npm ci' once under $SiteRoot." }
        $script:NodeVersion = (& $NodeCommand.Source --version).Trim()
    }

    $env:PUBLIC_SAPBA_API_URL = $ApiUrl
    $env:SAPBA_INTERNAL_API_URL = $ApiUrl
    Write-LauncherMessage "Using the Embedded GET-only SAP Provider."

    Invoke-StartupPhase -Name "port_detection" -Action {
        if ($Restart) {
            Stop-ExpectedListener -Name "Web UI" -Port $SitePort -ExpectedRoot $ProjectRoot
            Stop-ExpectedListener -Name "SAPBusinessAgents API" -Port $ApiPort -ExpectedRoot $ProjectRoot
        }
        if (Test-PortListening -Port $ApiPort) {
            $script:PlatformProcessId = Get-ListenerProcessId -Port $ApiPort
            if (Test-PlatformHealth) {
                $script:PlatformHealthy = $true
                Write-LauncherMessage "SAPBusinessAgents API is already healthy on port $ApiPort (PID $PlatformProcessId)."
            }
            else {
                Stop-ExpectedListener -Name "SAPBusinessAgents API" -Port $ApiPort -ExpectedRoot $ProjectRoot -Reason "the existing project process is unhealthy"
                $script:PlatformProcessId = $null
            }
        }
        if (Test-PortListening -Port $SitePort) {
            $script:SiteProcessId = Get-ListenerProcessId -Port $SitePort
            if (Test-SiteHealth) {
                $script:SiteHealthy = $true
                $script:SiteMode = Get-ExistingSiteMode -ProcessId $SiteProcessId
                Write-LauncherMessage "Web UI is already healthy on port $SitePort (PID $SiteProcessId)."
            }
            else {
                Stop-ExpectedListener -Name "Web UI" -Port $SitePort -ExpectedRoot $ProjectRoot -Reason "the existing project process is unhealthy"
                $script:SiteProcessId = $null
            }
        }
    }

    $ApiProcess = $null
    if (-not $PlatformHealthy) {
        $ApiProcess = Invoke-StartupPhase -Name "api_process_start" -Action {
            Start-LocalProcess -Name "SAPBusinessAgents API" -Port $ApiPort -FilePath $PlatformPython `
                -Arguments @("-m", "sap_business_agents_platform.cli", "--host", "127.0.0.1", "--port", [string]$ApiPort) `
                -WorkingDirectory $ProjectRoot -StdoutPath $PlatformStdout -StderrPath $PlatformStderr
        }
        $PlatformProcessId = $ApiProcess.Id
    }
    else { Invoke-StartupPhase -Name "api_process_start" -Action { Write-LauncherMessage "Reusing the healthy API process." } }

    $SiteProcess = $null
    $SiteDist = $null
    if (-not $SiteHealthy) {
        if ($Dev) {
            Invoke-StartupPhase -Name "site_fingerprint" -Action { Write-LauncherMessage "Development mode bypasses the Web UI build cache." }
            Invoke-StartupPhase -Name "site_build" -Action {
                Push-Location $SiteRoot
                try { Invoke-SiteCommand -Command $NpmCommand.Source -Arguments @("run", "catalog") }
                finally { Pop-Location }
            }
            $SiteProcess = Invoke-StartupPhase -Name "site_process_start" -Action {
                Start-LocalProcess -Name "Web UI" -Port $SitePort -FilePath $NodeCommand.Source `
                    -Arguments @($AstroCli, "dev", "--host", "127.0.0.1", "--port", [string]$SitePort) `
                    -WorkingDirectory $SiteRoot -StdoutPath $SiteStdout -StderrPath $SiteStderr
            }
        }
        else {
            $SiteFingerprint = Invoke-StartupPhase -Name "site_fingerprint" -Action {
                Get-SiteFingerprint -SiteRoot $SiteRoot -AgentsRoot $AgentsRoot -NodeVersion $NodeVersion
            }
            $SiteDist = Join-Path (Join-Path $SiteBuildRoot $SiteFingerprint) "dist"
            $cacheValid = Test-SiteBuild -DistPath $SiteDist
            if ($cacheValid -and -not $RebuildSite) {
                $SiteBuildReused = $true
                Invoke-StartupPhase -Name "site_build" -Action { Write-LauncherMessage "Reusing Web UI build cache $SiteFingerprint." }
            }
            else {
                $buildStarted = Get-Date
                Invoke-StartupPhase -Name "site_build" -Action {
                    if ($RebuildSite -and $cacheValid) { Write-LauncherMessage "Rebuilding Web UI cache $SiteFingerprint because -RebuildSite was requested." }
                    else { Write-LauncherMessage "Building Web UI cache $SiteFingerprint." }
                    $script:SiteDist = Build-SiteCache -Fingerprint $SiteFingerprint -SiteRoot $SiteRoot `
                        -NodePath $NodeCommand.Source -NpmPath $NpmCommand.Source -AstroPath $AstroCli
                    Remove-OldSiteBuilds -CurrentFingerprint $SiteFingerprint
                }
                $SiteBuildDurationMs = [int][Math]::Round(((Get-Date) - $buildStarted).TotalMilliseconds)
            }
            $SiteProcess = Invoke-StartupPhase -Name "site_process_start" -Action {
                $env:PUBLIC_SITE_BASE = "/"
                Start-LocalProcess -Name "Web UI" -Port $SitePort -FilePath $NodeCommand.Source `
                    -Arguments @($AstroCli, "preview", "--host", "127.0.0.1", "--port", [string]$SitePort, "--outDir", $SiteDist) `
                    -WorkingDirectory $SiteRoot -StdoutPath $SiteStdout -StderrPath $SiteStderr
            }
        }
        $SiteProcessId = $SiteProcess.Id
    }
    else {
        Invoke-StartupPhase -Name "site_fingerprint" -Action { Write-LauncherMessage "Reusing the healthy Web UI; fingerprint calculation was skipped." }
        Invoke-StartupPhase -Name "site_build" -Action { Write-LauncherMessage "Reusing the healthy Web UI; build was skipped." }
        Invoke-StartupPhase -Name "site_process_start" -Action { Write-LauncherMessage "Reusing the healthy Web UI process." }
    }

    Wait-ForServices -ApiProcess $ApiProcess -WebProcess $SiteProcess -ApiStdout $PlatformStdout `
        -ApiStderr $PlatformStderr -SiteStdout $SiteStdout -SiteStderr $SiteStderr

    Invoke-StartupPhase -Name "browser_open" -Action {
        if (-not $NoBrowser) { Write-LauncherMessage "Opening the Web UI in the default browser."; Start-Process $SiteUrl }
        else { Write-LauncherMessage "Browser launch was disabled." }
    }

    $totalDuration = [int][Math]::Round(((Get-Date) - $LaunchStartedAt).TotalMilliseconds)
    $PhaseDurations["total"] = $totalDuration
    Write-LauncherMessage "END total (${totalDuration} ms)"
    $state = New-AttemptState -Status "completed"
    Save-AttemptState -State $state -Successful
    Write-Host ""
    Write-Host "SAPBusinessAgents is ready." -ForegroundColor Green
    Write-Host "Web UI:    $SiteUrl"
    Write-Host "Local API: $ApiUrl"
    Write-Host "SAP Read:  embedded"
    Write-Host "Site mode: $SiteMode"
    Write-Host "Logs:      $LogRoot"
}
catch {
    $errorMessage = $_.Exception.Message
    Write-LauncherMessage "FAILED ${CurrentPhase}: $errorMessage" "ERROR" Red
    foreach ($process in @($StartedProcesses | Sort-Object Id -Descending)) {
        try {
            $process.Refresh()
            if (-not $process.HasExited) {
                Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
                Write-LauncherMessage "Stopped process $($process.Id) started by this failed launch." "WARN" Yellow
            }
        }
        catch { Write-LauncherMessage "Could not inspect or stop process $($process.Id) during failure cleanup." "WARN" Yellow }
    }
    foreach ($service in @($StartedServices | Sort-Object Port -Descending)) {
        try {
            $listenerProcessId = Get-ListenerProcessId -Port $service.Port
            if ($null -ne $listenerProcessId -and (Test-ProcessBelongsToProject -ProcessId $listenerProcessId -ExpectedRoot $ProjectRoot)) {
                Stop-Process -Id $listenerProcessId -ErrorAction SilentlyContinue
                Write-LauncherMessage "Stopped $($service.Name) listener $listenerProcessId started by this failed launch." "WARN" Yellow
            }
        }
        catch { Write-LauncherMessage "Could not clean up $($service.Name) listener on port $($service.Port)." "WARN" Yellow }
    }
    $PhaseDurations["total"] = [int][Math]::Round(((Get-Date) - $LaunchStartedAt).TotalMilliseconds)
    $failure = [ordered]@{ phase = $CurrentPhase; message = $errorMessage }
    $state = New-AttemptState -Status "failed" -Failure $failure
    Save-AttemptState -State $state
    throw
}
