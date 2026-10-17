$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { throw 'Install FFmpeg and add it to PATH first.' }
python -m venv --system-site-packages .venv
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 environment creation failed.' }
& '.venv\Scripts\python.exe' -m pip install -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& '.venv\Scripts\python.exe' -c 'import torch; assert torch.__version__.split("+")[0] == "2.6.0"'
if ($LASTEXITCODE -ne 0) {
    & '.venv\Scripts\python.exe' -m pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
    if ($LASTEXITCODE -ne 0) { throw 'CPU PyTorch installation failed.' }
}
Write-Output 'Environment ready. Follow README to prepare weights, audit data and train.'
