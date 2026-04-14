; ============================================================================
; installer.iss — FL Agent  ·  Inno Setup 6 installer script
;
; Produces:  Output\FL_Agent_Setup.exe
;
; Pass version on the ISCC command line:
;   ISCC /DAppVersion=1.2.3 installer.iss
; ============================================================================

; Default version if not provided via command-line define
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

#define AppName      "FL Agent"
#define AppPublisher "FL Agent Project"
#define AppURL       "https://github.com/mbexte/fruity-loops-agent"
#define AppExeName   "FL Agent.exe"
; Unique application GUID — do NOT change after first release
#define AppId        "{{7C3A2B1F-D4E5-4F6A-8B9C-0D1E2F3A4B5C}"

; ============================================================================
[Setup]
; ─── Identity ────────────────────────────────────────────────────────────────
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases

; ─── Install paths ───────────────────────────────────────────────────────────
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes

; ─── Output ──────────────────────────────────────────────────────────────────
OutputDir=Output
OutputBaseFilename=FL_Agent_Setup
SetupIconFile=

; ─── Compression ─────────────────────────────────────────────────────────────
Compression=lzma2/ultra64
SolidCompression=yes

; ─── Appearance ──────────────────────────────────────────────────────────────
WizardStyle=modern

; ─── Permissions ─────────────────────────────────────────────────────────────
; "lowest" = install per-user by default; dialog allows elevation if needed
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; ─── Platform ────────────────────────────────────────────────────────────────
ArchitecturesInstallIn64BitMode=x64compatible

; ─── Uninstaller ─────────────────────────────────────────────────────────────
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}

; ============================================================================
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

; ============================================================================
[Tasks]
; Desktop shortcut
Name: "desktopicon"; \
  Description: "Create a &desktop shortcut"; \
  GroupDescription: "Shortcuts:"; \
  Flags: unchecked

; FL Studio controller script
Name: "flstudio"; \
  Description: "Install FL Studio MIDI controller script"; \
  GroupDescription: "FL Studio Integration:";

; Claude Code MCP registration
Name: "configureclaude"; \
  Description: "Register MCP server with Claude Code (if installed)"; \
  GroupDescription: "Agent Configuration:"; \
  Flags: unchecked

; VS Code / GitHub Copilot MCP config
Name: "configurevscode"; \
  Description: "Write VS Code MCP config for GitHub Copilot"; \
  GroupDescription: "Agent Configuration:"; \
  Flags: unchecked

; ============================================================================
[Files]
; Main application (PyInstaller onedir output)
Source: "dist\FL Agent\*"; \
  DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

; FL Studio controller script staged to {tmp} for the Pascal code section
Source: "fl_studio_script\device_FL_Agent_Controller.py"; \
  DestDir: "{tmp}"; \
  Flags: dontcopy

; ============================================================================
[Icons]
Name: "{group}\{#AppName}";              Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}";    Filename: "{uninstallexe}"
Name: "{commondesktop}\{#AppName}";      Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

; ============================================================================
[Run]
Filename: "{app}\{#AppExeName}"; \
  Description: "Launch {#AppName} now"; \
  Flags: nowait postinstall skipifsilent

; ============================================================================
[Code]

var
  ApiPage: TInputQueryWizardPage;

// ---------------------------------------------------------------------------
// Locate FL Studio's Hardware folder (where device scripts live)
// ---------------------------------------------------------------------------
function FindFLStudioHardwareDir: string;
var
  Candidate: string;
begin
  Candidate := ExpandConstant('{userdocs}\Image-Line\FL Studio\Settings\Hardware');
  if DirExists(Candidate) then
    Result := Candidate
  else
    Result := '';
end;

// ---------------------------------------------------------------------------
// Write a UTF-8 string to a file (Inno Setup 6 helper)
// ---------------------------------------------------------------------------
procedure WriteFile(const FileName, Content: string);
var
  Lines: TArrayOfString;
begin
  SetArrayLength(Lines, 1);
  Lines[0] := Content;
  SaveStringsToFile(FileName, Lines, False);
end;

// ---------------------------------------------------------------------------
// Escape backslashes for embedding a Windows path in JSON
// ---------------------------------------------------------------------------
function JsonPath(const S: string): string;
begin
  Result := StringReplace(S, '\', '\\', [rfReplaceAll]);
end;

// ---------------------------------------------------------------------------
// Wizard initialisation — add API-key input page
// ---------------------------------------------------------------------------
procedure InitializeWizard;
begin
  ApiPage := CreateInputQueryPage(
    wpSelectTasks,
    'API Keys (Optional)',
    'Configure your AI assistant',
    'Enter an API key so FL Agent can talk to an AI model right away.'      + #13#10 +
    'You can skip this and set the environment variable manually later.'    + #13#10#13#10 +
    'Tip: get a free key at openrouter.ai — supports Claude, GPT-4, and more.');

  ApiPage.Add('OpenRouter API Key  (OPENROUTER_API_KEY):', True);
  ApiPage.Add('Anthropic API Key   (ANTHROPIC_API_KEY): ', True);
end;

// ---------------------------------------------------------------------------
// Post-install actions
// ---------------------------------------------------------------------------
procedure CurStepChanged(CurStep: TSetupStep);
var
  HardwareDir, ControllerDst : string;
  AppPath, McpExePath         : string;
  ClaudeDir, ClaudeSettings   : string;
  VsCodeDir, VsCodeMcp        : string;
  JsonContent                 : string;
begin
  if CurStep <> ssPostInstall then Exit;

  AppPath    := ExpandConstant('{app}');
  McpExePath := AppPath + '\fl_mcp_server.exe';

  // ── 1. FL Studio controller script ────────────────────────────────────────
  if IsTaskSelected('flstudio') then
  begin
    HardwareDir := FindFLStudioHardwareDir;
    if HardwareDir <> '' then
    begin
      ExtractTemporaryFile('device_FL_Agent_Controller.py');
      ControllerDst := HardwareDir + '\device_FL_Agent_Controller.py';
      FileCopy(ExpandConstant('{tmp}\device_FL_Agent_Controller.py'),
               ControllerDst, False);
    end
    else
    begin
      MsgBox(
        'FL Studio Hardware folder not found:'                              + #13#10 +
        ExpandConstant('{userdocs}\Image-Line\FL Studio\Settings\Hardware') + #13#10#13#10 +
        'After installing FL Studio, copy this file there manually:'        + #13#10 +
        AppPath + '\fl_studio_script\device_FL_Agent_Controller.py'         + #13#10#13#10 +
        'Then restart FL Studio, go to Options > MIDI Settings > Input,'    + #13#10 +
        'enable the "FL Agent" port, and set its controller to'             + #13#10 +
        '"FL Agent Controller".',
        mbInformation, MB_OK);
    end;
  end;

  // ── 2. Persist API keys in the user-level environment ─────────────────────
  if ApiPage.Values[0] <> '' then
    RegWriteStringValue(HKEY_CURRENT_USER, 'Environment',
                        'OPENROUTER_API_KEY', ApiPage.Values[0]);
  if ApiPage.Values[1] <> '' then
    RegWriteStringValue(HKEY_CURRENT_USER, 'Environment',
                        'ANTHROPIC_API_KEY', ApiPage.Values[1]);

  // ── 3. Register MCP server with Claude Code ────────────────────────────────
  if IsTaskSelected('configureclaude') then
  begin
    ClaudeDir      := ExpandConstant('{userappdata}') + '\.claude';
    ClaudeSettings := ClaudeDir + '\settings.json';

    if not DirExists(ClaudeDir) then
      CreateDir(ClaudeDir);

    // Write only when the file is absent to avoid stomping on user config.
    if not FileExists(ClaudeSettings) then
    begin
      JsonContent :=
        '{'                                                                  + #13#10 +
        '  "mcpServers": {'                                                  + #13#10 +
        '    "fl-agent": {'                                                  + #13#10 +
        '      "command": "' + JsonPath(McpExePath) + '",'                  + #13#10 +
        '      "args": []'                                                   + #13#10 +
        '    }'                                                              + #13#10 +
        '  }'                                                                + #13#10 +
        '}';
      WriteFile(ClaudeSettings, JsonContent);
    end;
  end;

  // ── 4. Write VS Code / GitHub Copilot MCP config ──────────────────────────
  if IsTaskSelected('configurevscode') then
  begin
    VsCodeDir := AppPath + '\.vscode';
    VsCodeMcp := VsCodeDir + '\mcp.json';

    if not DirExists(VsCodeDir) then
      CreateDir(VsCodeDir);

    JsonContent :=
      '{'                                                                    + #13#10 +
      '  "servers": {'                                                       + #13#10 +
      '    "fl-agent": {'                                                    + #13#10 +
      '      "type": "stdio",'                                               + #13#10 +
      '      "command": "' + JsonPath(McpExePath) + '",'                    + #13#10 +
      '      "args": []'                                                     + #13#10 +
      '    }'                                                                + #13#10 +
      '  }'                                                                  + #13#10 +
      '}';
    WriteFile(VsCodeMcp, JsonContent);
  end;
end;

// Required stub — keep default behaviour
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
end;
