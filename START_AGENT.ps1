# START_AGENT.ps1
# Sets up the environment and launches the FL Agent in one of three modes:
#
#   .\START_AGENT.ps1                # auto-detect (see below)
#   .\START_AGENT.ps1 openrouter     # Python agent via OpenRouter  (needs OPENROUTER_API_KEY)
#   .\START_AGENT.ps1 claude         # Claude Code CLI              (needs ANTHROPIC_API_KEY)
#   .\START_AGENT.ps1 copilot        # GitHub Copilot CLI if available, otherwise VS Code Copilot
#
# Auto-detect priority:
#   1. OPENROUTER_API_KEY is set  → openrouter
#   2. claude CLI found on PATH   → claude
#   3. fallback                   → copilot

param(
    [Parameter(Position=0)]
    [ValidateSet("auto","openrouter","claude","copilot")]
    [string]$Mode = "auto"
)1

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

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
# Step 2 — Install FL Studio controller script
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "==> Installing FL Studio controller script..." -ForegroundColor Cyan

$ControllerSrc = Join-Path $Root "fl_studio_script\device_FL Agent Controller.py"
$IniSrc        = Join-Path $Root "fl_studio_script\FL Agent Controller.ini"
$HardwareDir   = Join-Path $env:USERPROFILE "Documents\Image-Line\FL Studio\Settings\Hardware"
$DeviceDir     = Join-Path $HardwareDir "FL Agent Controller"

if (Test-Path $HardwareDir) {
    if (-not (Test-Path $DeviceDir)) { New-Item -ItemType Directory -Path $DeviceDir | Out-Null }
    Copy-Item $ControllerSrc $DeviceDir -Force
    Copy-Item $IniSrc        $HardwareDir -Force
    Write-Host "    Installed → $DeviceDir" -ForegroundColor Green
    Write-Host "    Installed → $HardwareDir\FL Agent Controller.ini" -ForegroundColor Green
    Write-Host "    Restart FL Studio if it is already open, then go to" -ForegroundColor Gray
    Write-Host "    Options > MIDI Settings > Input, enable 'FL Agent'," -ForegroundColor Gray
    Write-Host "    and set its Controller type to 'FL Agent Controller'." -ForegroundColor Gray
} else {
    Write-Host "    WARNING: FL Studio Hardware directory not found at:" -ForegroundColor Yellow
    Write-Host "    $HardwareDir" -ForegroundColor Yellow
    Write-Host "    Copy fl_studio_script\device_FL Agent Controller.py and" -ForegroundColor Yellow
    Write-Host "    fl_studio_script\FL Agent Controller.ini there manually." -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Step 3 — Resolve mode + write config
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "==> Resolving agent mode..." -ForegroundColor Cyan

$Resolved = $Mode
if ($Resolved -eq "auto") {
    if ($env:OPENROUTER_API_KEY) {
        $Resolved = "openrouter"
    } elseif (Get-Command "claude" -ErrorAction SilentlyContinue) {
        $Resolved = "claude"
    } else {
        $Resolved = "copilot"
    }
}
Write-Host "    Mode: $Resolved" -ForegroundColor Green

$ClaudeDir    = Join-Path $Root ".claude"
$SettingsFile = Join-Path $ClaudeDir "settings.json"
$VsCodeDir    = Join-Path $Root ".vscode"
$McpJsonFile  = Join-Path $VsCodeDir "mcp.json"
$ServerScript = Join-Path $Root "fl_mcp_server.py"

if ($Resolved -eq "claude") {
    # Write MCP server entry into .claude\settings.json
    Write-Host ""
    Write-Host "==> Registering MCP server with Claude Code..." -ForegroundColor Cyan

    if (-not (Test-Path $ClaudeDir)) { New-Item -ItemType Directory -Path $ClaudeDir | Out-Null }

    if (Test-Path $SettingsFile) {
        $Settings = Get-Content $SettingsFile -Raw | ConvertFrom-Json
    } else {
        $Settings = [PSCustomObject]@{}
    }

    $ServerEntry = [PSCustomObject]@{ command = $VenvPython; args = @($ServerScript) }

    if (-not ($Settings.PSObject.Properties.Name -contains "mcpServers")) {
        $Settings | Add-Member -NotePropertyName "mcpServers" -NotePropertyValue ([PSCustomObject]@{})
    }
    $Settings.mcpServers | Add-Member -NotePropertyName "fl-agent" -NotePropertyValue $ServerEntry -Force
    $Settings | ConvertTo-Json -Depth 10 | Set-Content $SettingsFile -Encoding UTF8

    Write-Host "    Written: $SettingsFile" -ForegroundColor Green

} elseif ($Resolved -eq "copilot") {
    # Write .vscode/mcp.json for VS Code native MCP support (VS Code 1.99+)
    Write-Host ""
    Write-Host "==> Writing VS Code MCP config for Copilot..." -ForegroundColor Cyan

    if (-not (Test-Path $VsCodeDir)) { New-Item -ItemType Directory -Path $VsCodeDir | Out-Null }

    $McpConfig = [PSCustomObject]@{
        servers = [PSCustomObject]@{
            "fl-agent" = [PSCustomObject]@{
                type    = "stdio"
                command = $VenvPython
                args    = @($ServerScript)
            }
        }
    }
    $McpConfig | ConvertTo-Json -Depth 10 | Set-Content $McpJsonFile -Encoding UTF8

    Write-Host "    Written: $McpJsonFile" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Step 4 — Launch
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "    Make sure loopMIDI is running with a port named 'FL Agent'." -ForegroundColor Gray
Write-Host ""

Set-Location $Root

switch ($Resolved) {
    "openrouter" {
        Write-Host "==> Launching OpenRouter agent with GUI (gui.py)..." -ForegroundColor Cyan
        Write-Host "    Use the chat window to compose music. Preview before sending to FL Studio." -ForegroundColor Gray
        & $VenvPython (Join-Path $Root "gui.py")
    }
    "claude" {
        # Ensure the claude CLI is present
        if (-not (Get-Command "claude" -ErrorAction SilentlyContinue)) {
            Write-Host "    'claude' not found. Installing via npm..." -ForegroundColor Yellow
            if (-not (Get-Command "npm" -ErrorAction SilentlyContinue)) {
                Write-Host ""
                Write-Host "ERROR: 'npm' not found. Install Node.js from https://nodejs.org/" -ForegroundColor Red
                exit 1
            }
            npm install -g @anthropic-ai/claude-code
        }
        Write-Host "==> Launching Claude Code..." -ForegroundColor Cyan
        claude
    }
    "copilot" {
        # Check if gh copilot is available
        try {
            $null = & gh copilot --version 2>$null
            $CopilotAvailable = $true
        } catch {
            $CopilotAvailable = $false
        }

        if ($CopilotAvailable) {
            Write-Host "==> Launching GitHub Copilot CLI..." -ForegroundColor Cyan
            Write-Host "    Use the CLI to interact with the agent and its MCP tools." -ForegroundColor Gray
            Write-Host "    Type your music requests to generate FL Studio patterns." -ForegroundColor Gray
            Write-Host ""
            & gh copilot
            return
        }

        # If copilot not found, try to install GitHub CLI and copilot extension
        $GhCli = Get-Command "gh" -ErrorAction SilentlyContinue
        if (-not $GhCli) {
            Write-Host "    GitHub CLI not found. Installing via winget..." -ForegroundColor Yellow
            $installResult = winget install --id GitHub.cli --source winget --accept-package-agreements --accept-source-agreements 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Host "    GitHub CLI installed successfully." -ForegroundColor Green
                # Refresh PATH
                $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
                $GhCli = Get-Command "gh" -ErrorAction SilentlyContinue
            } else {
                Write-Host "    winget installation failed. Output: $installResult" -ForegroundColor Red
                Write-Host "    Please install GitHub CLI manually from https://cli.github.com/" -ForegroundColor Yellow
            }
        }

        if ($GhCli) {
            Write-Host "    Installing GitHub Copilot extension..." -ForegroundColor Yellow
            $extResult = & gh extension install github/gh-copilot 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Host "    Copilot extension installed successfully." -ForegroundColor Green

                # Now check if copilot is available
                try {
                    $null = & gh copilot --version 2>$null
                    $CopilotAvailable = $true
                } catch {
                    $CopilotAvailable = $false
                }

                if ($CopilotAvailable) {
                    Write-Host "==> Launching GitHub Copilot CLI..." -ForegroundColor Cyan
                    Write-Host "    Use the CLI to interact with the agent and its MCP tools." -ForegroundColor Gray
                    Write-Host "    Type your music requests to generate FL Studio patterns." -ForegroundColor Gray
                    Write-Host ""
                    & gh copilot
                    return
                } else {
                    Write-Host "    Copilot extension installed but 'gh copilot' command not available." -ForegroundColor Yellow
                }
            } else {
                Write-Host "    Failed to install copilot extension. Output: $extResult" -ForegroundColor Red
            }
        }

        # Final fallback to VS Code
        if (-not (Get-Command "code" -ErrorAction SilentlyContinue)) {
            Write-Host ""
            Write-Host "ERROR: Unable to install or find GitHub Copilot CLI, and VS Code not found." -ForegroundColor Red
            Write-Host "Please install GitHub Copilot CLI manually or install VS Code with GitHub Copilot extension." -ForegroundColor Yellow
            exit 1
        }

        Write-Host "==> Launching VS Code (GitHub Copilot mode)..." -ForegroundColor Cyan
        Write-Host "    Open Copilot Chat in Agent mode to use the fl-agent MCP tools." -ForegroundColor Gray
        code .
    }
}
