param(
    [switch]$Quiet
)

$ErrorActionPreference = 'Continue'
$devPorts = @(5173, 8000, 8010, 8011)

function Get-CommandMatchedProcesses {
    Get-CimInstance Win32_Process | Where-Object {
        $cmd = $_.CommandLine
        if (-not $cmd) { return $false }
        if ($_.ProcessId -eq $PID) { return $false }
        return (
            $cmd -match 'manage\.py\s+runserver' -or
            $cmd -match 'uvicorn\s+ai_server\.main:app' -or
            ($_.Name -match 'node|npm' -and ($cmd -match 'vite' -or $cmd -match 'npm.*run.*dev'))
        )
    }
}

function Get-ListeningPortPids {
    $pids = @()
    $lines = netstat -ano -p tcp | Select-String 'LISTENING'
    foreach ($line in $lines) {
        $text = $line.ToString().Trim()
        if ($text -match '^TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)$') {
            $port = [int]$Matches[1]
            $pidValue = [int]$Matches[2]
            if ($devPorts -contains $port -and $pidValue -ne $PID) {
                $pids += $pidValue
            }
        }
    }
    return $pids | Select-Object -Unique
}

$targetPids = @()
$targetPids += @(Get-CommandMatchedProcesses | Select-Object -ExpandProperty ProcessId)
$targetPids += @(Get-ListeningPortPids)
$targetPids = $targetPids | Where-Object { $_ -and $_ -ne $PID } | Select-Object -Unique

if (-not $targetPids.Count) {
    if (-not $Quiet) { Write-Host 'No dev server processes found.' }
    exit 0
}

foreach ($targetPid in $targetPids) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $targetPid" -ErrorAction SilentlyContinue
    if (-not $process) { continue }
    if (-not $Quiet) {
        Write-Host "Stopping PID $($process.ProcessId): $($process.CommandLine)"
    }
    try {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
    } catch {
        Write-Warning "Failed to stop PID $($process.ProcessId): $($_.Exception.Message)"
    }
}

Start-Sleep -Seconds 1
if (-not $Quiet) {
    Write-Host 'Stopped matching dev server processes and dev port listeners.'
}
