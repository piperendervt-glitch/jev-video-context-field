param([switch]$UserNotesSaved, [switch]$ResumeStopped)
$ErrorActionPreference = 'Stop'
if (-not $UserNotesSaved -and -not $ResumeStopped) { throw 'user_unsaved_notes_confirmation_required' }
$migrationOld = 'C:\dev\jev-video-context-field-po'
$migrationNew = 'C:\dev\jev-video-context-field'
if ((Get-Location).Path -ne 'C:\dev') { throw 'run_from_parent_C_dev' }
if ((Resolve-Path -LiteralPath $migrationOld).Path -ne $migrationOld -or (Test-Path -LiteralPath $migrationNew)) { throw 'root_or_target_conflict' }
if ((Get-Item -LiteralPath $migrationOld).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'reparse_root' }
$migrationOut = Join-Path $migrationOld 'artifacts\local-private\p1-checkpoint-rename-20260920-v1'
$migrationEnv = Get-Content -Raw -Encoding UTF8 (Join-Path $migrationOut 'environment-before.json') | ConvertFrom-Json
$migrationBasePython = Join-Path $migrationEnv.base 'python.exe'
$migrationListeners = @(Get-NetTCPConnection -LocalPort 8876 -State Listen -ErrorAction SilentlyContinue)
if ($ResumeStopped) {
    if ($migrationListeners.Count) { throw 'resume_requires_no_listener' }
    $migrationStopRecord = Get-Content -Raw -Encoding UTF8 (Join-Path $migrationOut 'stop-process.json') | ConvertFrom-Json
    if ($migrationStopRecord.user_notes -ne 'none_or_saved') { throw 'saved_notes_confirmation_missing' }
    & (Join-Path $migrationOld '.venv\Scripts\python.exe') -X utf8 (Join-Path $migrationOld 'scripts\rename_migration.py') check-stopped
    if ($LASTEXITCODE) { throw 'stopped_assets_changed_reconcile_before_move' }
} else {
if ($migrationListeners.Count -ne 1) { throw 'expected_one_viewer' }
$migrationProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($migrationListeners[0].OwningProcess)"
if ($migrationProcess.CommandLine -notmatch '-m context_fields\.debug_server --port 8876') { throw 'wrong_process' }
$migrationBoot = Invoke-RestMethod 'http://127.0.0.1:8876/api/bootstrap'
if (-not $migrationBoot.debug -or $migrationBoot.live_enabled) { throw 'not_debug' }
@{pid=$migrationProcess.ProcessId;command=$migrationProcess.CommandLine;build=$migrationBoot.build;user_notes='none_or_saved'} | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $migrationOut 'stop-process.json')
$null=Invoke-RestMethod 'http://127.0.0.1:8876/api/debug/shutdown' -Method Post -ContentType 'application/json' -Body '{}' -Headers @{'X-Session-Token'=$migrationBoot.token}
$migrationDeadline=(Get-Date).AddSeconds(20)
do { Start-Sleep -Milliseconds 200; $migrationRemaining=@(Get-NetTCPConnection -LocalPort 8876 -State Listen -ErrorAction SilentlyContinue) } while ($migrationRemaining.Count -and (Get-Date) -lt $migrationDeadline)
if ($migrationRemaining.Count) { throw 'normal_stop_failed_no_force' }
& (Join-Path $migrationOld '.venv\Scripts\python.exe') -X utf8 (Join-Path $migrationOld 'scripts\rename_migration.py') stopped
if ($LASTEXITCODE) { throw 'stopped_snapshot_failed' }
}
# Both absolute paths were checked above; move only this explicitly named root.
Move-Item -LiteralPath $migrationOld -Destination $migrationNew
if ((Test-Path -LiteralPath $migrationOld) -or -not (Test-Path -LiteralPath $migrationNew)) { throw 'rename_not_verified' }
$migrationVenv=Join-Path $migrationNew '.venv'
$migrationVenvBackup=Join-Path $migrationNew '.venv.pre-migration'
if ((Resolve-Path -LiteralPath $migrationVenv).Path -ne $migrationVenv -or (Test-Path -LiteralPath $migrationVenvBackup)) { throw 'venv_backup_conflict' }
Move-Item -LiteralPath $migrationVenv -Destination $migrationVenvBackup
$migrationOut=Join-Path $migrationNew 'artifacts\local-private\p1-checkpoint-rename-20260920-v1'
@{stage='renamed';old_root=$migrationOld;new_root=$migrationNew} | ConvertTo-Json -Compress | Add-Content -Encoding UTF8 (Join-Path $migrationOut 'migration.jsonl')
& $migrationBasePython -m venv (Join-Path $migrationNew '.venv')
if ($LASTEXITCODE) { throw 'venv_creation_failed_old_env_retained' }
$migrationPython=Join-Path $migrationNew '.venv\Scripts\python.exe'
& $migrationPython -m pip --isolated --disable-pip-version-check install --no-input --no-index --find-links (Join-Path $migrationOut 'wheels') -r (Join-Path $migrationOut 'requirements-exact.txt') *> (Join-Path $migrationOut 'venv-install.log')
if ($LASTEXITCODE) { throw 'venv_install_failed_old_env_retained' }
& $migrationPython -X utf8 (Join-Path $migrationNew 'scripts\rename_migration.py') paths
if ($LASTEXITCODE) { throw 'path_setup_failed' }
& $migrationPython -X utf8 (Join-Path $migrationNew 'scripts\rename_migration.py') verify
if ($LASTEXITCODE) { throw 'preservation_verification_failed' }
Write-Output 'Renamed and rebuilt offline. Next: guarded regression, history audit, normal push, debug Viewer.'
