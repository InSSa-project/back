$ErrorActionPreference = "Stop"

$BackRoot = Split-Path -Parent $PSScriptRoot
Set-Location $BackRoot

$env:PYTHONIOENCODING = "utf-8"
$env:MPLCONFIGDIR = Join-Path $BackRoot "var\matplotlib"
$env:TRANSFORMERS_OFFLINE = "1"
$env:HF_DATASETS_OFFLINE = "1"

& ".\venv\Scripts\python.exe" "ai_server\finetuning\desktop_app.py"
