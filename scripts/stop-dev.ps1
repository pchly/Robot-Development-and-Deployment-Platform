[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$stateFile = Join-Path $projectRoot ".run\processes.json"

if (-not (Test-Path -LiteralPath $stateFile)) {
    Write-Host "No running development environment was found."
    exit 0
}

$state = Get-Content -Raw -Encoding utf8 $stateFile | ConvertFrom-Json
foreach ($record in @($state.processes)) {
    $process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        Write-Host "$($record.name) is already stopped."
        continue
    }

    $actualStartedAt = $process.StartTime.ToUniversalTime().ToString("o")
    if ($actualStartedAt -ne $record.started_at) {
        Write-Warning "Skipped PID $($record.pid): start time does not match."
        continue
    }

    Stop-Process -Id $process.Id -Force
    Write-Host "Stopped $($record.name) (PID $($record.pid))."
}

Remove-Item -LiteralPath $stateFile -Force
Write-Host "Development environment stopped." -ForegroundColor Green

