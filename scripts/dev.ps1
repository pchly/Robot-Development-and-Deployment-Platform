[CmdletBinding()]
param(
    [switch]$WithoutAgent,
    [switch]$SimulationAgent,
    [switch]$Lan
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$apiDirectory = Join-Path $projectRoot "services\platform-api"
$agentDirectory = Join-Path $projectRoot "services\edge-agent"
$studioDirectory = Join-Path $projectRoot "apps\studio"
$simulationDirectory = Join-Path $projectRoot "simulation-runtimes\mujoco-arm"
$runtimeDirectory = Join-Path $projectRoot ".run"
$stateFile = Join-Path $runtimeDirectory "processes.json"

$apiPython = Join-Path $apiDirectory ".venv\Scripts\python.exe"
$agentPython = Join-Path $agentDirectory ".venv\Scripts\python.exe"
$simulationPython = Join-Path $simulationDirectory ".venv\Scripts\python.exe"
$viteEntry = Join-Path $studioDirectory "node_modules\vite\bin\vite.js"
$listenAddress = if ($Lan) { "0.0.0.0" } else { "127.0.0.1" }

foreach ($requiredFile in @($apiPython, $viteEntry)) {
    if (-not (Test-Path -LiteralPath $requiredFile)) {
        throw "Missing development dependency: $requiredFile`nRun .\scripts\setup.cmd first."
    }
}
if ($WithoutAgent -and $SimulationAgent) {
    throw "-WithoutAgent and -SimulationAgent cannot be used together."
}
$selectedAgentPython = if ($SimulationAgent) { $simulationPython } else { $agentPython }
if (-not $WithoutAgent -and -not (Test-Path -LiteralPath $selectedAgentPython)) {
    throw "Missing Edge Agent environment: $selectedAgentPython`nRun .\scripts\setup.cmd first."
}

if (Test-Path -LiteralPath $stateFile) {
    $oldState = Get-Content -Raw -Encoding utf8 $stateFile | ConvertFrom-Json
    $stillRunning = @(
        $oldState.processes | Where-Object {
            Get-Process -Id $_.pid -ErrorAction SilentlyContinue
        }
    )
    if ($stillRunning.Count -gt 0) {
        throw "The development environment is already running. Run .\scripts\stop-dev.cmd first."
    }
}

New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
$startedProcesses = @()

function Start-ManagedProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList,
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory
    )

    $stdoutPath = Join-Path $runtimeDirectory "$Name.out.log"
    $stderrPath = Join-Path $runtimeDirectory "$Name.err.log"
    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -WindowStyle Hidden `
        -PassThru

    $script:startedProcesses += [PSCustomObject]@{
        name = $Name
        process = $process
        stdout = $stdoutPath
        stderr = $stderrPath
    }
    return $process
}

function Stop-NewProcesses {
    foreach ($entry in $script:startedProcesses) {
        if (-not $entry.process.HasExited) {
            Stop-Process -Id $entry.process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

try {
    $apiProcess = Start-ManagedProcess `
        -Name "platform-api" `
        -FilePath $apiPython `
        -ArgumentList @("-m", "uvicorn", "robot_platform_api.main:app", "--host", $listenAddress, "--port", "8765") `
        -WorkingDirectory $apiDirectory

    $apiReady = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if ($apiProcess.HasExited) {
            throw "Platform API failed to start. See .run\platform-api.err.log."
        }
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:8765/health" -TimeoutSec 1 | Out-Null
            $apiReady = $true
            break
        }
        catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not $apiReady) {
        throw "Platform API was not ready within 10 seconds. See .run\platform-api.err.log."
    }

    if (-not $WithoutAgent) {
        $agentArguments = @(
            "-m", "robot_edge_agent",
            "--server-url", "http://127.0.0.1:8765"
        )
        if ($SimulationAgent) {
            $agentArguments += @(
                "--agent-id", "simulation-windows-01",
                "--name", "WindowsMuJoCoSimulationNode",
                "--capability", "simulation.mujoco",
                "--capability", "inference.onnxruntime.cpu",
                "--data-root", (Join-Path $runtimeDirectory "simulation-agent"),
                "--self-restart"
            )
        }
        else {
            $agentArguments += @(
                "--agent-id", "edge-dev-01",
                "--name", "LocalDevelopmentNode"
            )
        }
        Start-ManagedProcess `
            -Name $(if ($SimulationAgent) { "simulation-agent" } else { "edge-agent" }) `
            -FilePath $selectedAgentPython `
            -ArgumentList $agentArguments `
            -WorkingDirectory $(if ($SimulationAgent) { $simulationDirectory } else { $agentDirectory }) | Out-Null
    }

    $nodeExecutable = (Get-Command node -ErrorAction Stop).Source
    Start-ManagedProcess `
        -Name "studio" `
        -FilePath $nodeExecutable `
        -ArgumentList @($viteEntry, "--host", $listenAddress, "--port", "5173") `
        -WorkingDirectory $studioDirectory | Out-Null

    Start-Sleep -Milliseconds 750
    foreach ($entry in $startedProcesses) {
        if ($entry.process.HasExited) {
            throw "$($entry.name) exited immediately. See $($entry.stderr)."
        }
    }

    $state = [PSCustomObject]@{
        created_at = [DateTime]::UtcNow.ToString("o")
        processes = @(
            $startedProcesses | ForEach-Object {
                [PSCustomObject]@{
                    name = $_.name
                    pid = $_.process.Id
                    started_at = $_.process.StartTime.ToUniversalTime().ToString("o")
                    stdout = $_.stdout
                    stderr = $_.stderr
                }
            }
        )
    }
    $state | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 $stateFile
}
catch {
    Stop-NewProcesses
    throw
}

Write-Host "Development environment started." -ForegroundColor Green
Write-Host "Web Studio:  http://127.0.0.1:5173"
Write-Host "Platform API: http://127.0.0.1:8765"
Write-Host "API docs:     http://127.0.0.1:8765/docs"
Write-Host "Runtime logs: $runtimeDirectory"
Write-Host "Stop command: .\scripts\stop-dev.cmd"
if ($SimulationAgent) {
    Write-Host "Simulation:   Windows MuJoCo Agent (simulation-windows-01)"
}
if ($Lan) {
    $lanAddresses = @(
        Get-NetIPConfiguration |
            Where-Object { $_.IPv4DefaultGateway -ne $null } |
            ForEach-Object { $_.IPv4Address.IPAddress } |
            Where-Object { $_ -notlike "198.18.*" }
    )
    foreach ($address in $lanAddresses) {
        Write-Host "LAN API:      http://${address}:8765"
        Write-Host "LAN Studio:   http://${address}:5173"
    }
    Write-Warning "LAN mode has no authentication. Use it only on a trusted development network."
}

