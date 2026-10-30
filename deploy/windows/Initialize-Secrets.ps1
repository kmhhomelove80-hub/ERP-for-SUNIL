#Requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$ConfigPath)
. (Join-Path $PSScriptRoot 'Common.ps1')
$Config = Get-SunilConfiguration -ConfigPath $ConfigPath
if (Test-Path -LiteralPath $Config.SecretsPath) { throw '기존 비밀 파일을 덮어쓰지 않습니다.' }
$SecretRoot = Split-Path -Parent $Config.SecretsPath
New-Item -ItemType Directory -Force -Path $SecretRoot | Out-Null
$CurrentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
& icacls.exe $SecretRoot '/inheritance:r' '/grant:r' ('*' + $CurrentUser.User.Value + ':(OI)(CI)F') '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw '비밀 디렉터리 ACL 설정 실패. 관리자에게 확인하세요.' }
$Bytes = New-Object byte[] 64
$Generator = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $Generator.GetBytes($Bytes) } finally { $Generator.Dispose() }
$AppKey = ConvertTo-SecureString -String ([Convert]::ToBase64String($Bytes)) -AsPlainText -Force
$DbPassword = Read-Host 'PostgreSQL sunil 계정 암호(화면에 표시되지 않음)' -AsSecureString
$Vault = @{ OwnerSid = $CurrentUser.User.Value; Secrets = @{ ERP_SECRET_KEY = $AppKey; ERP_DB_PASSWORD = $DbPassword } }
$Vault | Export-Clixml -LiteralPath $Config.SecretsPath -Encoding UTF8
Write-Host '현재 Windows 사용자/컴퓨터의 DPAPI 비밀 파일을 만들었습니다. 실제 값은 출력하지 않았습니다.'
