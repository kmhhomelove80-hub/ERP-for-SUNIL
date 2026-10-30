#Requires -Version 5.1
# Read-only preparation report. No passwords, IP addresses or machine IDs.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Windows 전용 점검입니다.' }
$OperatingSystem = Get-CimInstance Win32_OperatingSystem
$Computer = Get-CimInstance Win32_ComputerSystem
$Disks = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object {
    @{ Drive = $_.DeviceID; SizeGB = [Math]::Round($_.Size / 1GB,1); FreeGB = [Math]::Round($_.FreeSpace / 1GB,1) }
})
$Report = @{
    Windows = $OperatingSystem.Caption
    Version = $OperatingSystem.Version
    Build = $OperatingSystem.BuildNumber
    MemoryGB = [Math]::Round($Computer.TotalPhysicalMemory / 1GB,1)
    FixedDisks = $Disks
    PowerShell = $PSVersionTable.PSVersion.ToString()
    PythonLauncherFound = [bool](Get-Command py.exe -ErrorAction SilentlyContinue)
    PythonFound = [bool](Get-Command python.exe -ErrorAction SilentlyContinue)
    PostgreSQLClientOnPath = [bool](Get-Command psql.exe -ErrorAction SilentlyContinue)
}
$Report | ConvertTo-Json -Depth 4
