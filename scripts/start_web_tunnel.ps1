param([int]$Port = 3000)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$runtime = Join-Path $repo 'data\web-tunnel'
$exe = Join-Path $env:LOCALAPPDATA 'SolarBeliever\cloudflared\cloudflared.exe'
$stateFile = Join-Path $runtime 'state.json'
$urlFile = Join-Path $runtime 'url.txt'
$logFile = Join-Path $runtime 'tunnel.log'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
if (Test-Path $stateFile) {
    $state = Get-Content $stateFile -Raw | ConvertFrom-Json
    $existing = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
    if ($existing -and $existing.Path -eq $exe -and $existing.StartTime.ToUniversalTime().Ticks -eq $state.startedTicks) {
        if ($state.port -ne $Port) { throw 'Tunnel already running on another port. Stop it first.' }
        if (Test-Path $urlFile) { Get-Content $urlFile; exit 0 }
        throw 'Tunnel is still starting. Check data/web-tunnel/tunnel.log.'
    }
}
Invoke-WebRequest "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 10 | Out-Null
if (!(Test-Path $exe)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $exe -Parent) | Out-Null
    Invoke-WebRequest 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile $exe
}
Remove-Item -LiteralPath $urlFile -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $logFile -ErrorAction SilentlyContinue
$process = Start-Process -FilePath $exe -ArgumentList @('tunnel', '--no-autoupdate', '--url', "http://127.0.0.1:$Port", '--logfile', ('"' + $logFile + '"')) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime 'stdout.log') -RedirectStandardError (Join-Path $runtime 'stderr.log')
@{ pid = $process.Id; startedTicks = $process.StartTime.ToUniversalTime().Ticks; port = $Port } | ConvertTo-Json | Set-Content $stateFile
for ($attempt = 0; $attempt -lt 45; $attempt++) {
    Start-Sleep -Seconds 1
    if ($process.HasExited) { throw "Tunnel exited. See $runtime/stderr.log" }
    if (Test-Path $logFile) {
        $match = [regex]::Match((Get-Content $logFile -Raw), 'https://[a-z0-9-]+\.trycloudflare\.com')
        if ($match.Success) {
            Set-Content -LiteralPath $urlFile -Value $match.Value
            Write-Output $match.Value
            exit 0
        }
    }
}
throw "No URL yet. Check $logFile. The tunnel process remains running."
