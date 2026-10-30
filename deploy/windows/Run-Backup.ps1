#Requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$ConfigPath)
. (Join-Path $PSScriptRoot 'Common.ps1')
$Config = Get-SunilConfiguration -ConfigPath $ConfigPath
Import-SunilEnvironment -Config $Config
Set-Location -LiteralPath $Config.AppRoot
$LogRoot = Join-Path $Config.DataRoot 'logs'
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$LogPath = Join-Path $LogRoot ('backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
& $Config.PythonExe 'manage.py' 'backup_to_storage' '--directory' $Config.BackupDirectory '--storage' $Config.NasDirectory '--pg-bin' $Config.PgBin *>> $LogPath
exit $LASTEXITCODE
