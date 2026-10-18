param([int]$Port, [string]$Bind, [string]$ModelDirectory, [string]$EnvironmentDirectory = '.venv')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
$env:PYTHONIOENCODING = 'utf-8'
$environmentPython = Join-Path $EnvironmentDirectory 'Scripts/python.exe'
$runtime = if (Test-Path -LiteralPath $environmentPython) { $environmentPython } else { throw 'Run scripts/setup.ps1 for the selected EnvironmentDirectory first.' }
$launchArguments = @()
if ($PSBoundParameters.ContainsKey('Port')) { $launchArguments += @('--port', "$Port") }
if ($PSBoundParameters.ContainsKey('Bind')) { $launchArguments += @('--host', $Bind) }
if ($PSBoundParameters.ContainsKey('ModelDirectory')) { $launchArguments += @('--model-dir', $ModelDirectory) }
& $runtime -m cashframe.launcher @launchArguments
exit $LASTEXITCODE
