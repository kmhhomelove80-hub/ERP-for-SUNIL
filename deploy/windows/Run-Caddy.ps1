#Requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$ConfigPath)
. (Join-Path $PSScriptRoot 'Common.ps1')
$Config = Get-SunilConfiguration -ConfigPath $ConfigPath
# The proxy does not need the application secret or database password.
[Environment]::SetEnvironmentVariable('ERP_PUBLIC_HOST', [string]$Config.Environment.ERP_PUBLIC_HOST, 'Process')
if (-not (Test-Path -LiteralPath $Config.CaddyExe -PathType Leaf)) { throw 'Caddy 실행 파일 경로를 확인하세요.' }
$LogRoot = Join-Path $Config.DataRoot 'logs'
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$LogPath = Join-Path $LogRoot ('proxy-' + (Get-Date -Format 'yyyyMMdd') + '.log')
& $Config.CaddyExe 'run' '--config' $Config.CaddyConfig '--adapter' 'caddyfile' *>> $LogPath
exit $LASTEXITCODE
