param([double]$IntervalMinutes = 5, [switch]$GenerationOnly)
$ErrorActionPreference = 'Stop'
$taskRepo = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRepo '.venv\Scripts\python.exe'
$taskData = Join-Path $taskRepo 'data\continuous_collection'
$taskLogs = Join-Path $taskData 'logs'
$taskStatusPath = Join-Path $taskData 'collector_status.json'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Create .venv and install requirements.txt first.'
}
& $taskPython -c "from scripts.postgres_store import connect_postgres; c=connect_postgres(); c.close(); print('PostgreSQL connection verified.')"
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL is unavailable. Start the configured PostgreSQL database first.' }
if (-not $GenerationOnly) { & (Join-Path $PSScriptRoot 'start_kma_collector.ps1') }
if (Test-Path -LiteralPath $taskStatusPath) {
    $taskStatus = Get-Content -LiteralPath $taskStatusPath -Raw | ConvertFrom-Json
    $taskExisting = Get-Process -Id $taskStatus.pid -ErrorAction SilentlyContinue
    if ($taskExisting -and $taskExisting.ProcessName -eq 'python') {
        $taskAge = [Math]::Abs(($taskExisting.StartTime.ToUniversalTime() - [datetime]::Parse($taskStatus.started_at_utc).ToUniversalTime()).TotalSeconds)
        if ($taskAge -lt 60) {
            $taskCommand = (Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $taskExisting.Id)).CommandLine
            if (($taskCommand -match 'api\.py' -and $taskCommand -match '--continuous') -or $taskCommand -match 'maintain_public_data\.py') {
                Write-Output ('Collector already running. PID=' + $taskExisting.Id)
                return
            }
        }
    }
}
New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
# Old STOP files are ignored by the new process; retain them for OneDrive sync.
$taskStamp = [datetime]::UtcNow.ToString('yyyyMMddTHHmmssfff')
$taskStdout = Join-Path $taskLogs ($taskStamp + '.stdout.log')
$taskStderr = Join-Path $taskLogs ($taskStamp + '.stderr.log')
$taskInterval = $IntervalMinutes.ToString([Globalization.CultureInfo]::InvariantCulture)
$taskProcess = Start-Process -FilePath $taskPython -ArgumentList @('-u', 'api.py', '--continuous', '--interval-minutes', $taskInterval) -WorkingDirectory $taskRepo -WindowStyle Hidden -RedirectStandardOutput $taskStdout -RedirectStandardError $taskStderr -PassThru
$taskLaunch = @{pid=$taskProcess.Id; started_at_utc=[datetime]::UtcNow.ToString('o'); stdout=$taskStdout; stderr=$taskStderr; interval_minutes=$IntervalMinutes}
$taskLaunch | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskData 'launcher.json') -Encoding UTF8
Write-Output ('Started collector process. PID=' + $taskProcess.Id)
