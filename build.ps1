# build.ps1 — Local build script for FL Agent
#
# Replicates the GitHub Actions pipeline on a developer Windows machine.
#
# Usage:
#   .\build.ps1                   # build with default version 1.0.0
#   .\build.ps1 -Version 1.2.3    # build with specific version
#   .\build.ps1 -Clean            # wipe previous build artifacts first
#   .\build.ps1 -SkipInstaller    # only run PyInstaller (no Inno Setup)
#
# Requirements:
#   - Python 3.11+  on PATH
#   - Inno Setup 6  at "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
#     Download: https://jrsoftware.org/isdl.php

param(
    [string] $Version       = "1.0.0",
    [switch] $Clean,
    [switch] $SkipInstaller
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

# ─── Helpers ──────────────────────────────────────────────────────────────────
function Step($msg)  { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)    { Write-Host "    $msg"   -ForegroundColor Green }
function Warn($msg)  { Write-Host "    $msg"   -ForegroundColor Yellow }
function Fail($msg)  { Write-Host "`nERROR: $msg" -ForegroundColor Red; exit 1 }

# ─── 0. Clean ─────────────────────────────────────────────────────────────────
if ($Clean) {
    Step "Cleaning previous build artifacts..."
    @("build", "dist", "Output") | ForEach-Object {
        $p = Join-Path $Root $_
        if (Test-Path $p) {
            Remove-Item $p -Recurse -Force
            Ok "Removed: $_"
        }
    }
}

# ─── 1. Python virtual environment ────────────────────────────────────────────
Step "Checking Python virtual environment..."

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Fail "Python not found on PATH. Install Python 3.11+ from https://python.org"
}

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$VenvPip    = Join-Path $Root ".venv\Scripts\pip.exe"

if (-not (Test-Path $VenvPython)) {
    Warn "Creating .venv ..."
    python -m venv (Join-Path $Root ".venv")
}

Step "Installing / updating dependencies..."
& $VenvPip install -r (Join-Path $Root "requirements.txt") -q
& $VenvPip install "pyinstaller>=6.0" -q
Ok "Dependencies ready."

# ─── 2. PyInstaller ───────────────────────────────────────────────────────────
Step "Building executables with PyInstaller (version $Version)..."

$specFile = Join-Path $Root "fl_agent.spec"
if (-not (Test-Path $specFile)) { Fail "fl_agent.spec not found at $specFile" }

& $VenvPython -m PyInstaller $specFile --clean --noconfirm
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller build failed (exit $LASTEXITCODE)." }

# ─── 3. Verify & assemble dist folder ─────────────────────────────────────────
Step "Verifying build output..."

$AppDir  = Join-Path $Root "dist\FL Agent"
$GuiExe  = Join-Path $AppDir "FL Agent.exe"
$McpExe  = Join-Path $Root "dist\fl_mcp_server.exe"

if (-not (Test-Path $GuiExe)) { Fail "FL Agent.exe not found in dist\FL Agent\" }
if (-not (Test-Path $McpExe)) { Fail "fl_mcp_server.exe not found in dist\" }

# Copy the standalone MCP server into the GUI app folder so Inno Setup
# can bundle both in a single installer.
Copy-Item $McpExe (Join-Path $AppDir "fl_mcp_server.exe") -Force
Ok "MCP server copied into app folder."

# ─── 4. Inno Setup ────────────────────────────────────────────────────────────
if ($SkipInstaller) {
    Warn "Skipping Inno Setup (-SkipInstaller flag set)."
    Ok "PyInstaller build at: dist\FL Agent\"
    exit 0
}

Step "Looking for Inno Setup 6..."
$IsccExe = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

if (-not (Test-Path $IsccExe)) {
    Warn "Inno Setup 6 not found at: $IsccExe"
    Warn "Download from https://jrsoftware.org/isdl.php"
    Warn ""
    Warn "Re-run without -SkipInstaller once Inno Setup is installed,"
    Warn "or run from the GitHub Actions pipeline which installs it automatically."
    exit 0
}

Step "Compiling installer (version $Version)..."
$IssFile = Join-Path $Root "installer.iss"
& $IsccExe /DAppVersion=$Version $IssFile
if ($LASTEXITCODE -ne 0) { Fail "Inno Setup compilation failed (exit $LASTEXITCODE)." }

# ─── 5. Report ────────────────────────────────────────────────────────────────
$Output = Join-Path $Root "Output\FL_Agent_Setup.exe"
if (-not (Test-Path $Output)) { Fail "Installer not found at: $Output" }

$SizeMB = [math]::Round((Get-Item $Output).Length / 1MB, 1)

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Installer built successfully!" -ForegroundColor Green
Write-Host "  $Output ($SizeMB MB)" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
