# INSTALL_CONTROLLER.ps1
#
# Copies the FL Agent Controller MIDI script to FL Studio's Hardware directory
# so it appears in Options > MIDI Settings > Input as a controller type.
#
# Usage:
#   .\INSTALL_CONTROLLER.ps1                        # auto-detect Hardware directory
#   .\INSTALL_CONTROLLER.ps1 -HardwareDir "C:\..."  # explicit path

param(
    [Parameter()]
    [string]$HardwareDir = ""
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$ControllerFile = Join-Path $Root "fl_studio_script\device_FL_Agent_Controller.py"

# ---------------------------------------------------------------------------
# Verify source file exists
# ---------------------------------------------------------------------------
if (-not (Test-Path $ControllerFile)) {
    Write-Host ""
    Write-Host "ERROR: Controller script not found at:" -ForegroundColor Red
    Write-Host "  $ControllerFile" -ForegroundColor Red
    Write-Host "Run this script from the fruity-loops-agent project root." -ForegroundColor Yellow
    exit 1
}

# ---------------------------------------------------------------------------
# Locate FL Studio Hardware directory
# ---------------------------------------------------------------------------
$RelativePath = "Documents\Image-Line\FL Studio\Settings\Hardware"

$CandidateRoots = @(
    $env:USERPROFILE,                                              # C:\Users\<name>
    (Join-Path $env:USERPROFILE "OneDrive"),                       # OneDrive-redirected Documents
    (Join-Path $env:USERPROFILE "OneDrive - Personal"),
    $env:OneDriveConsumer,                                         # set by OneDrive client
    $env:OneDrive,
    [System.Environment]::GetFolderPath("MyDocuments") | Split-Path -Parent  # actual My Documents root
)

$Found = ""

if ($HardwareDir -ne "") {
    # Explicit path supplied on command line
    $Found = $HardwareDir
} else {
    Write-Host ""
    Write-Host "==> Searching for FL Studio Hardware directory..." -ForegroundColor Cyan

    foreach ($root in ($CandidateRoots | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique)) {
        $candidate = Join-Path $root $RelativePath
        if (Test-Path $candidate) {
            $Found = $candidate
            Write-Host "    Found: $Found" -ForegroundColor Green
            break
        }
    }

    if (-not $Found) {
        # Last resort: search My Documents regardless of profile layout
        $MyDocs = [System.Environment]::GetFolderPath("MyDocuments")
        $candidate = Join-Path $MyDocs "Image-Line\FL Studio\Settings\Hardware"
        if (Test-Path $candidate) {
            $Found = $candidate
            Write-Host "    Found: $Found" -ForegroundColor Green
        }
    }

    if (-not $Found) {
        Write-Host "    Could not locate the Hardware directory automatically." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "    Enter the full path to FL Studio's Hardware folder" -ForegroundColor Gray
        Write-Host "    (e.g. C:\Users\YourName\Documents\Image-Line\FL Studio\Settings\Hardware)" -ForegroundColor Gray
        Write-Host "    or press Enter to abort:" -ForegroundColor Gray
        $HardwareDir = Read-Host "    Path"

        if (-not $HardwareDir) {
            Write-Host ""
            Write-Host "Aborted. Copy the file manually:" -ForegroundColor Yellow
            Write-Host "  Source : $ControllerFile" -ForegroundColor Gray
            Write-Host "  Target : <FL Studio>\Settings\Hardware\" -ForegroundColor Gray
            exit 0
        }
        $Found = $HardwareDir
    }
}

# ---------------------------------------------------------------------------
# Create target directory if it doesn't exist yet
# ---------------------------------------------------------------------------
if (-not (Test-Path $Found)) {
    Write-Host ""
    Write-Host "    Directory does not exist — creating it..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $Found -Force | Out-Null
    Write-Host "    Created: $Found" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Copy the controller script
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "==> Installing controller script..." -ForegroundColor Cyan

$Destination = Join-Path $Found "device_FL_Agent_Controller.py"
Copy-Item -Path $ControllerFile -Destination $Destination -Force

$InstalledSize = (Get-Item $Destination).Length
Write-Host "    Copied  : $ControllerFile" -ForegroundColor Green
Write-Host "    To      : $Destination" -ForegroundColor Green
Write-Host "    Size    : $InstalledSize bytes" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Post-install instructions
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "==> Installation complete!" -ForegroundColor Green
Write-Host ""
Write-Host "    Next steps in FL Studio:" -ForegroundColor Cyan
Write-Host "      1. Restart FL Studio (required to pick up the new script)." -ForegroundColor Gray
Write-Host "      2. Go to  Options > MIDI Settings > Input." -ForegroundColor Gray
Write-Host "      3. Select the 'FL Agent' port." -ForegroundColor Gray
Write-Host "      4. Set 'Controller type' to  FL Agent Controller." -ForegroundColor Gray
Write-Host "      5. Enable the port (checkbox on the left)." -ForegroundColor Gray
Write-Host ""
Write-Host "    SysEx protocol the script handles:" -ForegroundColor Cyan
Write-Host "      CMD 0x01  START recording" -ForegroundColor Gray
Write-Host "      CMD 0x02  STOP  recording" -ForegroundColor Gray
Write-Host "      CMD 0x03  SET   active channel rack channel  (DATA[0] = index)" -ForegroundColor Gray
Write-Host "      CMD 0x04  LIST  channels  (FL Studio replies with RSP_CHANNEL_ENTRY/DONE)" -ForegroundColor Gray
Write-Host ""
