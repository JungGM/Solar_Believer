$ErrorActionPreference = 'Stop'
$taskRepo = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRepo '.venv\Scripts\python.exe'
$taskData = Join-Path $taskRepo 'data\noaa_gfs'
Push-Location $taskRepo
try {
    & $taskPython -m scripts.start_gfs_postgres
    if ($LASTEXITCODE -ne 0) { throw 'GFS PostgreSQL startup failed.' }
} finally { Pop-Location }
$taskExisting = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {
    $_.CommandLine -like '*-m scripts.collect_gfs_history*' -and $_.ExecutablePath -eq $taskPython
}
if ($taskExisting) {
    Write-Output ('GFS collector already running. PID=' + ($taskExisting.ProcessId -join ','))
    return
}
$taskLogs = Join-Path $taskData 'logs'
New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
$taskStamp = [datetime]::UtcNow.ToString('yyyyMMddTHHmmssfff')
$taskStdout = Join-Path $taskLogs ($taskStamp + '.stdout.log')
$taskStderr = Join-Path $taskLogs ($taskStamp + '.stderr.log')
$taskOldHost = $env:POSTGRES_HOST
$taskOldPort = $env:POSTGRES_PORT
$taskOldDb = $env:POSTGRES_DB
try {
    $env:POSTGRES_HOST = '127.0.0.1'
    $env:POSTGRES_PORT = '55433'
    $env:POSTGRES_DB = 'solar_gfs'
    $taskProcess = Start-Process -FilePath $taskPython -ArgumentList @('-u', '-m', 'scripts.collect_gfs_history', '--start', '2022-01-01') -WorkingDirectory $taskRepo -WindowStyle Hidden -RedirectStandardOutput $taskStdout -RedirectStandardError $taskStderr -PassThru
} finally {
    $env:POSTGRES_HOST = $taskOldHost
    $env:POSTGRES_PORT = $taskOldPort
    $env:POSTGRES_DB = $taskOldDb
}
@{pid=$taskProcess.Id; started_at_utc=[datetime]::UtcNow.ToString('o'); stdout=$taskStdout; stderr=$taskStderr} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskData 'launcher.json') -Encoding UTF8
Write-Output ('Started NOAA GFS history collector. PID=' + $taskProcess.Id)
