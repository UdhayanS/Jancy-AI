# Installs the built app to %LOCALAPPDATA%\Programs\Jancy with Start Menu + Desktop shortcuts.
# Usage:  .\install.ps1            install / update
#         .\install.ps1 -Uninstall
param([switch]$Uninstall)
$ErrorActionPreference = "Stop"

$target   = Join-Path $env:LOCALAPPDATA "Programs\Jancy"
$startLnk = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Jancy.lnk"
$deskLnk  = Join-Path ([Environment]::GetFolderPath("Desktop")) "Jancy.lnk"

Get-Process Jancy -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

if ($Uninstall) {
    Remove-Item $target, $startLnk, $deskLnk -Recurse -Force -ErrorAction SilentlyContinue
    Remove-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name Jancy -ErrorAction SilentlyContinue
    Write-Host "Jancy uninstalled. (Settings remain in $env:APPDATA\Jancy)"
    return
}

$source = Join-Path $PSScriptRoot "dist\Jancy"
if (-not (Test-Path "$source\Jancy.exe")) { throw "Build first: .\build.ps1" }

if (Test-Path $target) { Remove-Item $target -Recurse -Force }
Copy-Item $source $target -Recurse

$shell = New-Object -ComObject WScript.Shell
foreach ($lnk in @($startLnk, $deskLnk)) {
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = "$target\Jancy.exe"
    $s.WorkingDirectory = $target
    $s.Description = "Jancy voice dictation and assistant"
    $s.Save()
}
Write-Host "Installed to $target" -ForegroundColor Green
Start-Process "$target\Jancy.exe"
