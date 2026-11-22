$ErrorActionPreference = 'Stop'
$taskRepo = Split-Path -Parent $PSScriptRoot
$taskData = Join-Path $taskRepo 'data\continuous_collection'
New-Item -ItemType Directory -Path $taskData -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $taskData 'STOP'), '')
$taskKmaData = Join-Path $taskRepo 'data\kma_hub'
New-Item -ItemType Directory -Path $taskKmaData -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $taskKmaData 'STOP'), '')
& (Join-Path $PSScriptRoot 'stop_open_meteo_collector.ps1')
Write-Output 'Stop requested. The collector will exit after its current request.'
