# Use only after the user's explicit approval recorded in docs/LIVE_APPROVAL_JA.md.
param([ValidateSet('c0-provider-score-v1','cd-distribution-display-v1')][string]$AdoptionPolicy='c0-provider-score-v1')
$ErrorActionPreference = 'Stop'
$jevRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $jevRoot
$env:HF_HOME = Join-Path $jevRoot '.cache/huggingface'
$env:HF_HUB_OFFLINE = '1'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:TORCH_HOME = Join-Path $jevRoot '.cache/torch'
& (Join-Path $jevRoot '.venv/Scripts/python.exe') -m context_fields.server --local-models --approved-live-v02 --live-phase demo --adoption-policy $AdoptionPolicy
