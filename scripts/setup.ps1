[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$apiDirectory = Join-Path $projectRoot "services\platform-api"
$agentDirectory = Join-Path $projectRoot "services\edge-agent"
$studioDirectory = Join-Path $projectRoot "apps\studio"
$simulationDirectory = Join-Path $projectRoot "simulation-runtimes\mujoco-arm"
$windowsVisionDirectory = Join-Path $projectRoot "simulation-runtimes\windows-webcam-yolox-mujoco"

function Install-PythonService {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ServiceDirectory
    )

    $venvPython = Join-Path $ServiceDirectory ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Host "Creating Python virtual environment: $ServiceDirectory"
        Push-Location $ServiceDirectory
        try {
            python -m venv .venv
        }
        finally {
            Pop-Location
        }
    }

    Write-Host "Installing Python dependencies: $ServiceDirectory"
    Push-Location $ServiceDirectory
    try {
        & $venvPython -m pip install --constraint requirements-dev.lock --editable ".[dev]"
        if ($LASTEXITCODE -ne 0) {
            throw "Python dependency installation failed: $ServiceDirectory"
        }
    }
    finally {
        Pop-Location
    }
}

Get-Command python -ErrorAction Stop | Out-Null
Get-Command node -ErrorAction Stop | Out-Null
Get-Command npm.cmd -ErrorAction Stop | Out-Null

Install-PythonService -ServiceDirectory $apiDirectory
Install-PythonService -ServiceDirectory $agentDirectory

$simulationPython = Join-Path $simulationDirectory ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $simulationPython)) {
    Write-Host "Creating MuJoCo simulation virtual environment"
    python -m venv (Join-Path $simulationDirectory ".venv")
}
Write-Host "Installing MuJoCo runtime and Windows Simulation Agent"
& $simulationPython -m pip install --editable "${simulationDirectory}[dev]"
if ($LASTEXITCODE -ne 0) {
    throw "MuJoCo simulation dependency installation failed"
}
Write-Host "Installing Windows webcam and YOLOX CPU runtime dependencies"
& $simulationPython -m pip install --editable $windowsVisionDirectory
if ($LASTEXITCODE -ne 0) {
    throw "Windows webcam YOLOX dependency installation failed"
}
& $simulationPython -m pip install --no-deps --editable $agentDirectory
if ($LASTEXITCODE -ne 0) {
    throw "Simulation Agent installation failed"
}

Write-Host "Installing Web Studio dependencies"
Push-Location $studioDirectory
try {
    npm.cmd ci --cache .npm-cache
    if ($LASTEXITCODE -ne 0) {
        throw "Web Studio dependency installation failed"
    }
}
finally {
    Pop-Location
}

Write-Host "Setup completed. Run .\scripts\dev.cmd to start development." -ForegroundColor Green

