$ErrorActionPreference = 'Stop'
$taskRepo = Split-Path -Parent $PSScriptRoot
$taskData = Join-Path $taskRepo 'data\open_meteo'
New-Item -ItemType Directory -Path $taskData -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $taskData 'STOP'), '')
if (Get-Command docker -ErrorAction SilentlyContinue) {
    $taskContainer = & docker compose ps --status running -q open-meteo-collector 2>$null
    if ($LASTEXITCODE -eq 0 -and $taskContainer) {
        & docker compose stop -t 90 open-meteo-collector
    }
}
Write-Output 'Open-Meteo stop requested; the current response will finish saving first.'
