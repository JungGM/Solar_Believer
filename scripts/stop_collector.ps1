$ErrorActionPreference = 'Stop'
$taskRepo = Split-Path -Parent $PSScriptRoot
$taskData = Join-Path $taskRepo 'data\continuous_collection'
New-Item -ItemType Directory -Path $taskData -Force | Out-Null
New-Item -ItemType File -Path (Join-Path $taskData 'STOP') -Force | Out-Null
$taskKmaData = Join-Path $taskRepo 'data\kma_hub'
New-Item -ItemType Directory -Path $taskKmaData -Force | Out-Null
New-Item -ItemType File -Path (Join-Path $taskKmaData 'STOP') -Force | Out-Null
Write-Output 'Stop requested. The collector will exit after its current request.'
