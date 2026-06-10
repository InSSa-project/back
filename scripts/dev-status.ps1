$ErrorActionPreference = 'Continue'

$backDir = Resolve-Path (Join-Path $PSScriptRoot '..')
$envFile = Join-Path $backDir '.env'
$devPorts = @(5173, 8000, 8010, 8011)

function Get-LiveListeningPorts {
    $items = @()
    $lines = netstat -ano -p tcp | Select-String 'LISTENING'
    foreach ($line in $lines) {
        $text = $line.ToString().Trim()
        if ($text -match '^TCP\s+(\S+):(\d+)\s+\S+\s+LISTENING\s+(\d+)$') {
            $address = $Matches[1]
            $port = [int]$Matches[2]
            $pidValue = [int]$Matches[3]
            if (-not ($devPorts -contains $port)) { continue }
            $process = Get-CimInstance Win32_Process -Filter "ProcessId = $pidValue" -ErrorAction SilentlyContinue
            if ($process) {
                $items += [pscustomobject]@{
                    Port = $port
                    PID = $pidValue
                    Name = $process.Name
                    CommandLine = $process.CommandLine
                }
            }
        }
    }
    return $items | Sort-Object Port, PID -Unique
}

Write-Host '=== INSSA dev server status ==='
Write-Host ''

if (Test-Path $envFile) {
    Write-Host '[AI env]'
    Get-Content $envFile | Select-String -Pattern '^AI_SERVER_ENABLED=|^AI_SERVER_BASE_URL='
    Write-Host ''
}

Write-Host '[Live listening dev ports]'
$ports = @(Get-LiveListeningPorts)
if ($ports.Count) {
    $ports | Format-Table Port,PID,Name,CommandLine -AutoSize
} else {
    Write-Host 'No matching live listening ports.'
}
Write-Host ''

Write-Host '[Matching dev process tree]'
$processes = Get-CimInstance Win32_Process | Where-Object {
    $cmd = $_.CommandLine
    if (-not $cmd) { return $false }
    return (
        $cmd -match 'manage\.py\s+runserver' -or
        $cmd -match 'uvicorn\s+ai_server\.main:app' -or
        ($_.Name -match 'node|npm' -and ($cmd -match 'vite' -or $cmd -match 'npm.*run.*dev'))
    )
} | Select-Object ProcessId,Name,CommandLine

if ($processes) {
    $processes | Format-List
} else {
    Write-Host 'No matching dev server processes.'
}

Write-Host 'Expected live ports: 5173, 8000, 8010. Port 8011 should be absent.'
Write-Host 'Note: Django runserver, uvicorn --reload, and npm/vite can show parent/child processes.'
