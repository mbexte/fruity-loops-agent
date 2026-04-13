# START_AGENT.ps1
# Sets up the Python environment, registers the MCP server with Claude Code,
# and launches the Claude Code CLI from the repo root.

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

# ---------------------------------------------------------------------------
# Step 0 — Ensure Claude Code CLI is installed
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "==> Checking Claude Code CLI..." -ForegroundColor Cyan

if (-not (Get-Command "claude" -ErrorAction SilentlyContinue)) {
    Write-Host "    'claude' not found. Attempting to install via npm..." -ForegroundColor Yellow

    if (-not (Get-Command "npm" -ErrorAction SilentlyContinue)) {
        Write-Host ""
        Write-Host "ERROR: Neither 'claude' nor 'npm' was found on PATH." -ForegroundColor Red
        Write-Host ""
        Write-Host "Install Node.js (which includes npm) from https://nodejs.org/, then re-run this script." -ForegroundColor Yellow
        Write-Host "Or install Claude Code manually: npm install -g @anthropic-ai/claude-code" -ForegroundColor Yellow
        exit 1
    }

    npm install -g @anthropic-ai/claude-code
    if (-not (Get-Command "claude" -ErrorAction SilentlyContinue)) {
        Write-Host ""
        Write-Host "ERROR: Installation appeared to succeed but 'claude' is still not on PATH." -ForegroundColor Red
        Write-Host "Close and reopen PowerShell, then run this script again." -ForegroundColor Yellow
        exit 1
    }
    Write-Host "    Claude Code installed." -ForegroundColor Green
} else {
    Write-Host "    OK ($(claude --version 2>$null))" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Step 1 — Python virtual environment + dependencies
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "==> Checking Python virtual environment..." -ForegroundColor Cyan

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$VenvPip    = Join-Path $Root ".venv\Scripts\pip.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "    Creating .venv..." -ForegroundColor Yellow
    python -m venv (Join-Path $Root ".venv")
}

Write-Host "    Installing / updating dependencies..." -ForegroundColor Yellow
& $VenvPip install -r (Join-Path $Root "requirements.txt") -q

Write-Host "    OK" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 2 — Write MCP server config into .claude\settings.json
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "==> Registering MCP server with Claude Code..." -ForegroundColor Cyan

$ClaudeDir      = Join-Path $Root ".claude"
$SettingsFile   = Join-Path $ClaudeDir "settings.json"
$ServerScript   = Join-Path $Root "fl_mcp_server.py"

# Ensure .claude directory exists
if (-not (Test-Path $ClaudeDir)) {
    New-Item -ItemType Directory -Path $ClaudeDir | Out-Null
}

# Read existing settings or start fresh
if (Test-Path $SettingsFile) {
    $Settings = Get-Content $SettingsFile -Raw | ConvertFrom-Json
} else {
    $Settings = [PSCustomObject]@{}
}

# Build the fl-agent server entry
$ServerEntry = [PSCustomObject]@{
    command = $VenvPython
    args    = @($ServerScript)
}

# Merge: add/overwrite only mcpServers.fl-agent
if (-not ($Settings.PSObject.Properties.Name -contains "mcpServers")) {
    $Settings | Add-Member -NotePropertyName "mcpServers" -NotePropertyValue ([PSCustomObject]@{})
}
$Settings.mcpServers | Add-Member -NotePropertyName "fl-agent" -NotePropertyValue $ServerEntry -Force

# Write back with indentation
$Settings | ConvertTo-Json -Depth 10 | Set-Content $SettingsFile -Encoding UTF8

Write-Host "    Written: $SettingsFile" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 3 — Launch Claude Code
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "==> Launching Claude Code..." -ForegroundColor Cyan
Write-Host "    MCP server 'fl-agent' will be loaded automatically." -ForegroundColor Gray
Write-Host "    Make sure loopMIDI is running with a port named 'FL Agent'." -ForegroundColor Gray
Write-Host ""

Set-Location $Root
claude
