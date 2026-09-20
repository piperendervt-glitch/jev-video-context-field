$ErrorActionPreference = 'Stop'
$jevRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $jevRoot
$env:HF_HOME = Join-Path $jevRoot '.cache/huggingface'
$env:HF_HUB_OFFLINE = '1'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:TORCH_HOME = Join-Path $jevRoot '.cache/torch'
& (Join-Path $jevRoot '.venv/Scripts/python.exe') -m context_fields.server --local-models
