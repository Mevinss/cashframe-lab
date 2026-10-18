param([string]$Python311, [string]$EnvironmentDirectory = '.venv')
$ErrorActionPreference = 'Stop'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { throw 'Install FFmpeg and add it to PATH first.' }
if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) { throw 'Install FFprobe and add it to PATH first.' }
if (-not $Python311) {
    if (Get-Command py -ErrorAction SilentlyContinue) { $Python311 = (& py -3.11 -c 'import sys; print(sys.executable)').Trim() }
    elseif (Test-Path -LiteralPath "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe") { $Python311 = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { $Python311 = (Get-Command python).Source }
    else { throw 'Install Python 3.11 or pass -Python311 with its executable path.' }
}
& $Python311 -c 'import sys; assert sys.version_info[:2] == (3, 11)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 is required.' }
$environmentPython = Join-Path $EnvironmentDirectory 'Scripts/python.exe'
if (-not (Test-Path -LiteralPath $environmentPython)) {
    & $Python311 -m venv $EnvironmentDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 environment creation failed.' }
}
& $environmentPython -c 'import sys, site; assert sys.version_info[:2] == (3, 11); assert sys.prefix != sys.base_prefix; assert not site.ENABLE_USER_SITE'
if ($LASTEXITCODE -ne 0) { throw 'Selected environment must be an isolated Python 3.11 environment. Stop the service before replacing it.' }
& $environmentPython -m pip install -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
@'
from importlib.metadata import version, PackageNotFoundError
import sys
try:
    sys.exit(0 if version('torch').split('+')[0] == '2.6.0' else 1)
except PackageNotFoundError:
    sys.exit(1)
'@ | & $environmentPython -
if ($LASTEXITCODE -ne 0) {
    & $environmentPython -m pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu --constraint requirements.lock
    if ($LASTEXITCODE -ne 0) { throw 'CPU PyTorch installation failed.' }
}
& $environmentPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Installed dependency versions are inconsistent.' }
Write-Output 'Environment ready. Follow README to prepare weights, audit data and train.'
