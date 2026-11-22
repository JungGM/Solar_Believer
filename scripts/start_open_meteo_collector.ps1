$ErrorActionPreference = 'Stop'
$taskRepo = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRepo '.venv\Scripts\python.exe'
$taskData = Join-Path $taskRepo 'data\open_meteo'
$taskStatusPath = Join-Path $taskData 'collector_status.json'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Create .venv and install backend/requirements.txt first.' }
& $taskPython -c "from scripts.postgres_store import connect_postgres; c=connect_postgres(); c.close(); print('PostgreSQL connection verified.')"
if ($LASTEXITCODE -ne 0) { throw 'Start the configured PostgreSQL database first.' }
if (Get-Command docker -ErrorAction SilentlyContinue) {
    $taskContainer = & docker compose ps --status running -q open-meteo-collector 2>$null
    if ($LASTEXITCODE -eq 0 -and $taskContainer) {
        Write-Output 'Open-Meteo collector is already running in Docker.'
        return
    }
}
if (Test-Path -LiteralPath $taskStatusPath) {
    $taskStatus = Get-Content -LiteralPath $taskStatusPath -Raw | ConvertFrom-Json
    $taskExisting = Get-Process -Id $taskStatus.pid -ErrorAction SilentlyContinue
    if ($taskExisting -and $taskExisting.ProcessName -eq 'python') {
        $taskCommand = (Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $taskExisting.Id)).CommandLine
        if ($taskCommand -match 'scripts.collect_open_meteo') {
            Write-Output ('Open-Meteo collector already running. PID=' + $taskExisting.Id)
            return
        }
    }
}
$taskLogs = Join-Path $taskData 'logs'
New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
$taskStamp = [datetime]::UtcNow.ToString('yyyyMMddTHHmmssfff')
$taskStdout = Join-Path $taskLogs ($taskStamp + '.stdout.log')
$taskStderr = Join-Path $taskLogs ($taskStamp + '.stderr.log')
$taskProcess = Start-Process -FilePath $taskPython -ArgumentList @('-u', '-m', 'scripts.collect_open_meteo') -WorkingDirectory $taskRepo -WindowStyle Hidden -RedirectStandardOutput $taskStdout -RedirectStandardError $taskStderr -PassThru
@{pid=$taskProcess.Id; started_at_utc=[datetime]::UtcNow.ToString('o'); stdout=$taskStdout; stderr=$taskStderr} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskData 'launcher.json') -Encoding UTF8
Write-Output ('Started Open-Meteo history-first collector. PID=' + $taskProcess.Id)
