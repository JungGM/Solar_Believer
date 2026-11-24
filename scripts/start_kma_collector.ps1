$ErrorActionPreference = 'Stop'
$taskRepo = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRepo '.venv\Scripts\python.exe'
$taskData = Join-Path $taskRepo 'data\kma_hub'
$taskStatusPath = Join-Path $taskData 'collector_status.json'
if (Test-Path -LiteralPath $taskStatusPath) {
    $taskStatus = Get-Content -LiteralPath $taskStatusPath -Raw | ConvertFrom-Json
    $taskExisting = Get-Process -Id $taskStatus.pid -ErrorAction SilentlyContinue
    if ($taskExisting -and $taskExisting.ProcessName -eq 'python') {
        $taskAge = [Math]::Abs(($taskExisting.StartTime.ToUniversalTime() - [datetime]::Parse($taskStatus.started_at_utc).ToUniversalTime()).TotalSeconds)
        $taskCommand = (Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $taskExisting.Id)).CommandLine
        if ($taskAge -lt 60 -and ($taskCommand -match '--kma-hub' -or $taskCommand -match 'scripts.collect_kma_hub')) {
            Write-Output ('KMA Hub collector already running. PID=' + $taskExisting.Id)
            return
        }
    }
}
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Create .venv and install requirements.txt first.' }
$taskLogs = Join-Path $taskData 'logs'
New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
# Old STOP files are ignored by the new process; retain them for OneDrive sync.
$taskStamp = [datetime]::UtcNow.ToString('yyyyMMddTHHmmssfff')
$taskStdout = Join-Path $taskLogs ($taskStamp + '.stdout.log')
$taskStderr = Join-Path $taskLogs ($taskStamp + '.stderr.log')
$taskProcess = Start-Process -FilePath $taskPython -ArgumentList @('-u', 'api.py', '--kma-hub', '--forecast-only', '--history-first', '--start', '2026-06-01') -WorkingDirectory $taskRepo -WindowStyle Hidden -RedirectStandardOutput $taskStdout -RedirectStandardError $taskStderr -PassThru
@{pid=$taskProcess.Id; started_at_utc=[datetime]::UtcNow.ToString('o'); stdout=$taskStdout; stderr=$taskStderr} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskData 'launcher.json') -Encoding UTF8
Write-Output ('Started KMA Hub collector process. PID=' + $taskProcess.Id)
