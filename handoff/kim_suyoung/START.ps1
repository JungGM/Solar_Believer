$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskConfig = Join-Path $PSScriptRoot 'local_config.json'
if (Test-Path -LiteralPath $taskConfig) {
    $taskOutput = (Get-Content -LiteralPath $taskConfig -Raw | ConvertFrom-Json).output
} else {
    $taskOutput = Read-Host 'Output folder (example D:\MeteoData)'
    if ([string]::IsNullOrWhiteSpace($taskOutput)) { throw 'Output folder is required' }
    @{output=$taskOutput} | ConvertTo-Json | Set-Content -LiteralPath $taskConfig -Encoding utf8
}
if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 collector.py --output $taskOutput --db }
elseif (Get-Command python -ErrorAction SilentlyContinue) { & python collector.py --output $taskOutput --db }
else { throw 'Install Python 3.10 or newer, then run START.ps1 again.' }
if ($LASTEXITCODE -ne 0) { throw 'Collector failed. Read the error above.' }
