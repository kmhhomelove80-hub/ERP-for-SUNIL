#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-SunilConfiguration {
    param([Parameter(Mandatory=$true)][string]$ConfigPath)
    if ($env:OS -ne 'Windows_NT') { throw 'Windows 전용 실행 스크립트입니다.' }
    $Config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($Property in $Config.Environment.PSObject.Properties) {
        if ($Property.Name -notmatch '^ERP_[A-Z0-9_]+$') { throw '환경 변수 이름을 확인하세요.' }
        if ($Property.Name -in @('ERP_SECRET_KEY','ERP_DB_PASSWORD')) { throw '비밀값은 runtime.json 대신 secrets.clixml에 보관하세요.' }
    }
    return $Config
}

function Import-SunilEnvironment {
    param([Parameter(Mandatory=$true)]$Config)
    foreach ($Property in $Config.Environment.PSObject.Properties) {
        [Environment]::SetEnvironmentVariable($Property.Name, [string]$Property.Value, 'Process')
    }
    $Vault = Import-Clixml -LiteralPath $Config.SecretsPath
    $CurrentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if ($Vault.OwnerSid -ne $CurrentSid) { throw '비밀값을 준비한 Windows 계정으로 실행해야 합니다.' }
    foreach ($Name in @('ERP_SECRET_KEY','ERP_DB_PASSWORD')) {
        $Credential = [System.Management.Automation.PSCredential]::new('unused', $Vault.Secrets[$Name])
        [Environment]::SetEnvironmentVariable($Name, $Credential.GetNetworkCredential().Password, 'Process')
    }
    if (-not (Test-Path -LiteralPath $Config.PythonExe -PathType Leaf)) { throw 'Python 가상환경 경로를 확인하세요.' }
}
