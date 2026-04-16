# deploy_fl_script.ps1
# Copies device_FL_Agent_Controller.py to the FL Studio Hardware path.
# Re-launches itself as Administrator if not already elevated.

$src = "$PSScriptRoot\fl_studio_script\device_FL_Agent_Controller.py"
$dst = "C:\Program Files\Image-Line\FL Studio 2024\System\Hardware specific\FL Agent Controller\device_FL Agent Controller.py"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

if (-not $isAdmin) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

Copy-Item -Path $src -Destination $dst -Force
Write-Host "Deployed: $src -> $dst"
