[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$projectRoot = Split-Path -Parent $PSScriptRoot

Push-Location $projectRoot
try {
    git rev-parse --is-inside-work-tree *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Not a Git repository: $projectRoot"
    }

    git lfs version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Git LFS is not installed. Run: git lfs install"
    }

    $requiredFiles = @(
        @{ Path = "algorithm-packages/yolox/dist/yolox_nano-fp16.engine"; Lfs = $true },
        @{ Path = "artifact-packages/container-import-smoke/dist/container-import-smoke-0.1.0-docker-save.tar"; Lfs = $true },
        @{ Path = "artifact-packages/mock-camera-mujoco-calibration/dist/mock-camera-to-mujoco-arm-v1.json"; Lfs = $false },
        @{ Path = "artifact-packages/mujoco-arm-runtime/dist/robot_mujoco_arm_runtime-0.4.0-py3-none-any.whl"; Lfs = $true },
        @{ Path = "artifact-packages/windows-webcam-yolox-mujoco-runtime/dist/robot_windows_webcam_yolox_mujoco_runtime-0.2.0-py3-none-any.whl"; Lfs = $true },
        @{ Path = "hardware-adapters/realsense/dist/robot_hardware_realsense-0.1.0-py3-none-any.whl"; Lfs = $true },
        @{ Path = "hardware-adapters/windows-webcam/dist/robot_hardware_windows_webcam-0.1.2-py3-none-any.whl"; Lfs = $true },
        @{ Path = "simulation-runtimes/windows-webcam-yolox-mujoco/models/yolox_nano.onnx"; Lfs = $true }
    )

    foreach ($entry in $requiredFiles) {
        $path = $entry.Path
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Required baseline resource is missing: $path"
        }

        git check-ignore -q -- $path
        if ($LASTEXITCODE -eq 0) {
            throw "Required baseline resource is ignored by .gitignore: $path"
        }

        if ($entry.Lfs) {
            $filter = git check-attr filter -- $path
            if ($filter -notmatch "filter: lfs$") {
                throw "Binary resource is not tracked by Git LFS: $path"
            }
        }
    }

    $candidates = @(git ls-files --cached --others --exclude-standard)
    $forbidden = $candidates | Where-Object {
        $_ -match '(^|/)(\.venv|node_modules|\.run|\.data|__pycache__)(/|$)' -or
        ($_ -match '(^|/)\.env($|\.)' -and $_ -ne '.env.example') -or
        $_ -match '\.(sqlite3|sqlite3-shm|sqlite3-wal|db|log|pem|key|p12)$'
    }
    if ($forbidden) {
        throw "Forbidden repository candidates found:`n$($forbidden -join "`n")"
    }

    $apiPython = Join-Path $projectRoot "services/platform-api/.venv/Scripts/python.exe"
    if (-not (Test-Path -LiteralPath $apiPython -PathType Leaf)) {
        throw "Platform API environment is missing. Run: .\scripts\setup.cmd"
    }

    & $apiPython tools/validate_asset_packages.py
    if ($LASTEXITCODE -ne 0) {
        throw "Manifest or artifact digest validation failed."
    }

    Write-Host ""
    Write-Host "Repository check passed" -ForegroundColor Green
    Write-Host "Candidate files: $($candidates.Count)"
    Write-Host "Next: run 'git add .', then review 'git status' and 'git lfs ls-files'."
}
finally {
    Pop-Location
}
