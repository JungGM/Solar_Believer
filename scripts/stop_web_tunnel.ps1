$ErrorActionPreference = 'Stop'
$runtime = Join-Path (Split-Path $PSScriptRoot -Parent) 'data\web-tunnel'
$stateFile = Join-Path $runtime 'state.json'
$exe = Join-Path $env:LOCALAPPDATA 'SolarBeliever\cloudflared\cloudflared.exe'
if (Test-Path $stateFile) {
    $state = Get-Content $stateFile -Raw | ConvertFrom-Json
    $process = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
    if ($process -and $process.Path -eq $exe -and $process.StartTime.ToUniversalTime().Ticks -eq $state.startedTicks) {
        Stop-Process -Id $process.Id
    }
    Remove-Item -LiteralPath $stateFile
}
Remove-Item -LiteralPath (Join-Path $runtime 'url.txt') -ErrorAction SilentlyContinue
Write-Output 'Web tunnel stopped.'
