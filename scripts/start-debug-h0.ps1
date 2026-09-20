$ErrorActionPreference = 'Stop'
$jevDebugRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $jevDebugRoot
& (Join-Path $jevDebugRoot '.venv/Scripts/python.exe') -m context_fields.debug_server --port 8876
