@echo off
setlocal

cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
set MPLCONFIGDIR=%CD%\var\matplotlib
set TRANSFORMERS_OFFLINE=1
set HF_DATASETS_OFFLINE=1

".\venv\Scripts\python.exe" "ai_server\finetuning\desktop_app.py"
