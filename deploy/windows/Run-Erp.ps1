#Requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$ConfigPath)
. (Join-Path $PSScriptRoot 'Common.ps1')
$Config = Get-SunilConfiguration -ConfigPath $ConfigPath
Import-SunilEnvironment -Config $Config
Set-Location -LiteralPath $Config.AppRoot
$LogRoot = Join-Path $Config.DataRoot 'logs'
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$LogPath = Join-Path $LogRoot ('server-' + (Get-Date -Format 'yyyyMMdd') + '.log')
& $Config.PythonExe '-m' 'waitress' ('--listen=' + $Config.Listen) '--threads=4' '--max-request-body-size=12582912' 'config.wsgi:application' *>> $LogPath
exit $LASTEXITCODE
