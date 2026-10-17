param([int]$Port, [string]$Bind, [string]$ModelDirectory)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
$env:PYTHONIOENCODING = 'utf-8'
$runtime = if (Test-Path -LiteralPath '.venv\Scripts\python.exe') { '.venv\Scripts\python.exe' } else { 'python' }
$launchArguments = @()
if ($PSBoundParameters.ContainsKey('Port')) { $launchArguments += @('--port', "$Port") }
if ($PSBoundParameters.ContainsKey('Bind')) { $launchArguments += @('--host', $Bind) }
if ($PSBoundParameters.ContainsKey('ModelDirectory')) { $launchArguments += @('--model-dir', $ModelDirectory) }
& $runtime -m cashframe.launcher @launchArguments
exit $LASTEXITCODE
