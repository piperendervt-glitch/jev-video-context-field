$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
if ((Get-Location).Path -ne $taskRoot) { throw 'workspace_mismatch' }
$taskOut = Join-Path $taskRoot 'artifacts\human-debug-h12\20260920'
$listener = @(Get-NetTCPConnection -LocalPort 8876 -State Listen -ErrorAction SilentlyContinue)
if ($listener.Count -ne 1) { throw 'expected_one_existing_debug_listener' }
$prior = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener[0].OwningProcess)"
if ($prior.CommandLine -notmatch '-m context_fields\.debug_server --port 8876') { throw 'not_expected_debug_backend' }
$boot = Invoke-RestMethod 'http://127.0.0.1:8876/api/bootstrap'
if (-not $boot.debug -or $boot.live_enabled) { throw 'not_non_live_debug' }
$expectedBuild = 'ea3090fbb6178b56bb397f8fc5994ac97997de726d502e9ca2093d70853daedf'
if (Test-Path -LiteralPath (Join-Path $taskOut 'startup.json')) {
    $previousStart = Get-Content -Raw -Encoding utf8 (Join-Path $taskOut 'startup.json') | ConvertFrom-Json
    $expectedBuild = $previousStart.build
    Copy-Item -LiteralPath (Join-Path $taskOut 'startup.json') -Destination (Join-Path $taskOut "startup-$($previousStart.pid).json")
}
if ($boot.build -ne $expectedBuild) { throw 'unexpected_prior_build' }
$before = @{pid=$prior.ProcessId; executable=$prior.ExecutablePath; command=$prior.CommandLine; build=$boot.build; debug=$boot.debug; live_enabled=$boot.live_enabled; user_unsaved_notes='none_or_saved'}
$before | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 (Join-Path $taskOut 'startup-before.json')
$before | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 (Join-Path $taskOut "startup-before-$($prior.ProcessId).json")
$null = Invoke-RestMethod 'http://127.0.0.1:8876/api/debug/shutdown' -Method Post -ContentType 'application/json' -Body '{}' -Headers @{'X-Session-Token'=$boot.token}
$deadline = (Get-Date).AddSeconds(20)
do {
    Start-Sleep -Milliseconds 200
    $remaining = @(Get-NetTCPConnection -LocalPort 8876 -State Listen -ErrorAction SilentlyContinue)
} while ($remaining.Count -and (Get-Date) -lt $deadline)
if ($remaining.Count) { throw 'normal_shutdown_did_not_release_port_no_force_attempted' }
foreach ($streamName in @('stdout','stderr')) {
    $priorLog = Join-Path $taskOut "viewer-$streamName.log"
    if (Test-Path -LiteralPath $priorLog) { Copy-Item -LiteralPath $priorLog -Destination (Join-Path $taskOut "viewer-$($prior.ProcessId)-$streamName.log") }
}
$launched = Start-Process -FilePath (Join-Path $taskRoot '.venv\Scripts\python.exe') -ArgumentList '-X','utf8','-m','context_fields.debug_server','--port','8876' -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskOut 'viewer-stdout.log') -RedirectStandardError (Join-Path $taskOut 'viewer-stderr.log') -PassThru
$deadline = (Get-Date).AddSeconds(20)
$ready = $false
do {
    Start-Sleep -Milliseconds 250
    try { $current = Invoke-RestMethod 'http://127.0.0.1:8876/api/bootstrap'; $ready = $true } catch { }
} while (-not $ready -and (Get-Date) -lt $deadline)
if (-not $ready -or -not $current.debug -or $current.live_enabled) { throw 'debug_start_not_verified' }
$owner = @(Get-NetTCPConnection -LocalPort 8876 -State Listen)[0].OwningProcess
$actual = Get-CimInstance Win32_Process -Filter "ProcessId=$owner"
@{pid=$owner; launcher_pid=$launched.Id; command=$actual.CommandLine; executable=$actual.ExecutablePath; build=$current.build; debug=$current.debug; live_enabled=$current.live_enabled; normal_shutdown=$true; prior_pid=$prior.ProcessId} | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 (Join-Path $taskOut 'startup.json')
Write-Output "H1.2 debug ready at http://127.0.0.1:8876/ PID $owner"
