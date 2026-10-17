param([int]$Port = 8000, [string]$Bind = '127.0.0.1')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
$env:PYTHONIOENCODING = 'utf-8'
if (-not $env:CASHFRAME_API_KEY) {
    $keyPath = Join-Path (Get-Location) 'local\api-key.txt'
    if (-not (Test-Path -LiteralPath $keyPath)) {
        New-Item -ItemType Directory -Force -Path (Split-Path $keyPath) | Out-Null
        $bytes = New-Object byte[] 32
        [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
        [IO.File]::WriteAllText($keyPath, [Convert]::ToBase64String($bytes))
    }
    $env:CASHFRAME_API_KEY = [IO.File]::ReadAllText($keyPath).Trim()
}
$runtime = if (Test-Path -LiteralPath '.venv\Scripts\python.exe') { '.venv\Scripts\python.exe' } else { 'python' }
& $runtime -m cashframe.cli serve --host $Bind --port $Port
exit $LASTEXITCODE
