"""Exercise the launcher's real orchestration with fake owned processes only."""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Windows launcher orchestration')
@pytest.mark.parametrize('scenario', ['success', 'build_failure', 'candidate_failure', 'switch_failure', 'api_changed'])
def test_site_only_switch_keeps_api_and_restores_failed_site(tmp_path, scenario):
    script = r'''
$ErrorActionPreference='Stop'
$ast=[System.Management.Automation.Language.Parser]::ParseFile('__SOURCE__',[ref]$null,[ref]$null)
foreach($name in @('Switch-SiteOnly')) {
  $fn=$ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)[0]
  Invoke-Expression $fn.Extent.Text
}
$scenario='__SCENARIO__'
$root='__ROOT__'
$SiteBuildRoot=$root; $ProjectRoot=$root; $SiteRoot=$root; $AgentsRoot=$root
$ApiPort=8765; $SitePort=4321; $ApiUrl='http://offline:8765'; $SiteHealthUrl='http://offline:4321/'
$NodeCommand=[pscustomobject]@{Source='node'}; $NpmCommand=[pscustomobject]@{Source='npm'}
$AstroCli='astro'; $NodeVersion='test'; $LogRoot=$root; $SiteStdout='site.out'; $SiteStderr='site.err'
$RebuildSite=$false; $StartupTimeoutSeconds=5
$StartedProcesses=[System.Collections.ArrayList]::new(); $StartedServices=[System.Collections.ArrayList]::new()
$script:sitePid=20; $script:apiReads=0; $script:phase='old'; $script:stops=@(); $script:starts=@(); $script:pointers=@()
$old=[ordered]@{fingerprint='old';dist_path=(Join-Path $root 'old/dist');pid=20}
$old | ConvertTo-Json | Set-Content (Join-Path $root 'current.json')
function Test-PlatformHealth { return $true }
function Test-SiteHealth { return $true }
function Get-ListenerProcessId { param($Port)
  if($Port -eq 8765) { $script:apiReads++; if($scenario -eq 'api_changed' -and $script:apiReads -gt 1){return 11}; return 10 }
  return $script:sitePid
}
function Assert-PathWithinRoot { param($Path,$Root) }
function Test-SiteReleaseBuild { param($DistPath) return -not ($scenario -eq 'build_failure' -and $DistPath -match 'new') }
function Test-ProcessBelongsToProject { param($ProcessId,$ExpectedRoot) return $true }
function Get-SiteFingerprint {param($SiteRoot,$AgentsRoot,$NodeVersion) return 'new'}
function Build-SiteCache { throw 'simulated_build_failure' }
function Start-LocalProcess {param($Name,$Port,$FilePath,$Arguments,$WorkingDirectory,$StdoutPath,$StderrPath)
  $script:starts += $Name
  if($Name -eq 'Web UI candidate') {$id=30; $script:phase='probe'}
  elseif($Name -eq 'Web UI rollback') {$id=50; $script:sitePid=50; $script:phase='rollback'}
  else {$id=40; $script:sitePid=40; $script:phase='new'}
  $exited=($scenario -eq 'candidate_failure' -and $id -eq 30) -or ($scenario -eq 'switch_failure' -and $id -eq 40)
  $process=[pscustomobject]@{Id=$id;HasExited=$exited}
  $process | Add-Member ScriptMethod Refresh {}
  [void]$script:StartedProcesses.Add($process)
  [void]$script:StartedServices.Add([pscustomobject]@{Name=$Name;Port=$Port})
  return $process
}
function Test-SiteCandidate {param($Url)
  return -not (($scenario -eq 'candidate_failure' -and $script:phase -eq 'probe') -or ($scenario -eq 'switch_failure' -and $script:phase -eq 'new'))
}
function Stop-Process {param($Id) $script:stops += [int]$Id}
function Stop-ExpectedListener {param($Name,$Port,$ExpectedRoot)
  if($Port -eq 8765){throw 'API_MUST_NOT_STOP'}
  $script:stops += $script:sitePid; $script:sitePid=0
}
function Write-JsonAtomic {param($Path,$Value) $script:pointers += [pscustomobject]@{Path=$Path;Fingerprint=$Value.fingerprint;Pid=$Value.pid}}
function New-AttemptState {param($Status) return @{status=$Status}}
function Save-AttemptState {param($State,[switch]$Successful)}
function Write-LauncherMessage {param($Message,$Level,$Color)}
$failed=$false
try {Switch-SiteOnly} catch {$failed=$true}
@{failed=$failed;site=$script:sitePid;stops=$script:stops;starts=$script:starts;pointers=$script:pointers;tracked=$StartedServices.Count} | ConvertTo-Json -Depth 5 -Compress
'''
    script = script.replace('__SOURCE__', str(ROOT / 'scripts/Start-SAPBusinessAgents.ps1').replace("'", "''"))
    script = script.replace('__ROOT__', str(tmp_path).replace("'", "''")).replace('__SCENARIO__', scenario)
    path = tmp_path / 'launcher-test.ps1'
    path.write_text(script, encoding='utf-8-sig')
    result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(path)],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout.strip())
    assert 10 not in value['stops']
    assert not any('API' in name for name in value['starts'])
    if scenario == 'success':
        assert not value['failed'] and value['site'] == 40
        assert value['pointers'][-1]['Fingerprint'] == 'new'
    elif scenario == 'switch_failure':
        assert value['failed'] and value['site'] == 50 and value['tracked'] == 0
        assert value['pointers'][-1]['Fingerprint'] == 'old'
    else:
        assert value['failed'] and value['site'] == 20 and 20 not in value['stops']


def test_site_only_is_mutually_exclusive_and_never_prunes_builds():
    source = (ROOT / 'scripts/Start-SAPBusinessAgents.ps1').read_text(encoding='utf-8-sig')
    function = source.split('function Switch-SiteOnly {', 1)[1].split('\n$PlatformPython =', 1)[0]
    assert 'Remove-OldSiteBuilds' not in function
    assert 'Stop-ExpectedListener -Name "SAPBusinessAgents API"' not in function
    assert '$RestartSiteOnly -and ($Restart -or $Dev)' in source
