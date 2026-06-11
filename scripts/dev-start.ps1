param(
    [switch]$NoFrontend
)

$ErrorActionPreference = 'Stop'

$backDir = Resolve-Path (Join-Path $PSScriptRoot '..')
$projectDir = Resolve-Path (Join-Path $backDir '..')
$frontDir = Join-Path $projectDir 'front'
$logDir = Join-Path $backDir 'var\logs'
$djangoPython = Join-Path $backDir 'venv\Scripts\python.exe'
$aiPython = Join-Path $backDir 'venv\Scripts\python.exe'
$npmCmd = 'npm.cmd'

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

Write-Host 'Stopping old dev servers first...'
& (Join-Path $PSScriptRoot 'dev-stop.ps1') -Quiet

if (-not (Test-Path $djangoPython)) {
    throw "Django Python not found: $djangoPython"
}
if (-not (Test-Path $aiPython)) {
    throw "AI Python not found: $aiPython"
}
if (-not (Test-Path (Join-Path $frontDir 'package.json'))) {
    throw "Frontend package.json not found: $frontDir"
}

$envFile = Join-Path $backDir '.env'
if (Test-Path $envFile) {
    $aiBaseUrl = (Get-Content $envFile | Where-Object { $_ -match '^AI_SERVER_BASE_URL=' } | Select-Object -First 1)
    if ($aiBaseUrl -and $aiBaseUrl -notmatch 'localhost:8010|127\.0\.0\.1:8010') {
        Write-Warning "AI_SERVER_BASE_URL is not 8010: $aiBaseUrl"
    }
}

Write-Host 'Starting AI server on http://127.0.0.1:8010 ...'
Start-Process `
    -FilePath $aiPython `
    -ArgumentList @('-m','uvicorn','ai_server.main:app','--host','127.0.0.1','--port','8010') `
    -WorkingDirectory $backDir `
    -RedirectStandardOutput (Join-Path $logDir 'dev-ai.out.log') `
    -RedirectStandardError (Join-Path $logDir 'dev-ai.err.log') `
    -WindowStyle Hidden

Start-Sleep -Seconds 2

Write-Host 'Starting Django server on http://127.0.0.1:8000 ...'
Start-Process `
    -FilePath $djangoPython `
    -ArgumentList @('manage.py','runserver','127.0.0.1:8000') `
    -WorkingDirectory $backDir `
    -RedirectStandardOutput (Join-Path $logDir 'dev-django.out.log') `
    -RedirectStandardError (Join-Path $logDir 'dev-django.err.log') `
    -WindowStyle Hidden

if (-not $NoFrontend) {
    Write-Host 'Starting Vue dev server on http://127.0.0.1:5173 ...'
    Start-Process `
        -FilePath $npmCmd `
        -ArgumentList @('run','dev','--','--host','127.0.0.1','--port','5173') `
        -WorkingDirectory $frontDir `
        -RedirectStandardOutput (Join-Path $logDir 'dev-front.out.log') `
        -RedirectStandardError (Join-Path $logDir 'dev-front.err.log') `
        -WindowStyle Hidden
}

Start-Sleep -Seconds 5
& (Join-Path $PSScriptRoot 'dev-status.ps1')

Write-Host ''
Write-Host 'Dev servers requested:'
Write-Host '- Frontend: http://127.0.0.1:5173'
Write-Host '- Django:   http://127.0.0.1:8000'
Write-Host '- AI:       http://127.0.0.1:8010'
Write-Host ''
Write-Host "Logs: $logDir"
