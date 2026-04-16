; ============================================================================
; installer.iss — FL Agent  ·  Inno Setup 6 installer script
;
; Produces:  Output\FL_Agent_Setup.exe
;
; Pass version on the ISCC command line:
;   ISCC /DAppVersion=1.2.3 installer.iss
; ============================================================================

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

#define AppName      "FL Agent"
#define AppPublisher "FL Agent Project"
#define AppURL       "https://github.com/mbexte/fruity-loops-agent"
#define AppExeName   "FL Agent.exe"

; ============================================================================
[Setup]
AppId={{7C3A2B1F-D4E5-4F6A-8B9C-0D1E2F3A4B5C}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
OutputDir=Output
OutputBaseFilename=FL_Agent_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}

; ============================================================================
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

; ============================================================================
[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "flstudio"; Description: "Install FL Studio MIDI controller script"; GroupDescription: "FL Studio Integration:"
Name: "configureclaude"; Description: "Register MCP server with Claude Code (if installed)"; GroupDescription: "Agent Configuration:"; Flags: unchecked
Name: "configurevscode"; Description: "Write VS Code MCP config for GitHub Copilot"; GroupDescription: "Agent Configuration:"; Flags: unchecked

; ============================================================================
[Files]
Source: "dist\FL Agent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "fl_studio_script\device_FL_Agent_Controller.py"; DestDir: "{app}\fl_studio_script"; Flags: ignoreversion

; ============================================================================
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

; ============================================================================
[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName} now"; Flags: nowait postinstall skipifsilent

; ============================================================================
[Code]

var
  ApiPage: TInputQueryWizardPage;

// Search Program Files for "FL Studio <year>\System\Hardware specific\FL Agent Controller".
// Returns the folder path if found, or '' if not installed there.
function FindFLAgentControllerDir(): string;
var
  Base: string;
  Versions: TArrayOfString;
  i: Integer;
  Candidate: string;
begin
  Result := '';
  Base := ExpandConstant('{pf}') + '\Image-Line\';
  SetArrayLength(Versions, 7);
  Versions[0] := 'FL Studio 2026';
  Versions[1] := 'FL Studio 2025';
  Versions[2] := 'FL Studio 2024';
  Versions[3] := 'FL Studio 21';
  Versions[4] := 'FL Studio 20';
  Versions[5] := 'FL Studio 12';
  Versions[6] := 'FL Studio';
  for i := 0 to GetArrayLength(Versions) - 1 do
  begin
    Candidate := Base + Versions[i] + '\System\Hardware specific\FL Agent Controller';
    if DirExists(Candidate) then
    begin
      Result := Candidate;
      Exit;
    end;
  end;
end;

// Returns the best available destination directory for the MIDI script:
//   1. Program Files  …\Hardware specific\FL Agent Controller   (preferred)
//   2. User documents …\Image-Line\FL Studio\Settings\Hardware  (fallback)
//   3. ''  (not found)
function FindFLStudioHardwareDir(): string;
var
  Candidate: string;
begin
  Candidate := FindFLAgentControllerDir();
  if Candidate <> '' then
  begin
    Result := Candidate;
    Exit;
  end;
  Candidate := ExpandConstant('{userdocs}') + '\Image-Line\FL Studio\Settings\Hardware';
  if DirExists(Candidate) then
    Result := Candidate
  else
    Result := '';
end;

function EscapeJsonPath(S: string): string;
var
  P: Integer;
begin
  Result := S;
  P := Pos('\', Result);
  while P > 0 do
  begin
    Delete(Result, P, 1);
    Insert('\\', Result, P);
    P := Pos('\', Result);
  end;
end;

procedure InitializeWizard();
begin
  ApiPage := CreateInputQueryPage(
    wpSelectTasks,
    'API Keys (Optional)',
    'Configure your AI assistant',
    'Enter an API key so FL Agent can start composing right away.' + #13#10 +
    'You can skip this and set the environment variable manually later.' + #13#10#13#10 +
    'Get a free key at openrouter.ai — supports Claude, GPT-4, and more.');
  ApiPage.Add('OpenRouter API Key (OPENROUTER_API_KEY):', True);
  ApiPage.Add('Anthropic API Key  (ANTHROPIC_API_KEY):', True);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  HardwareDir: string;
  AppPath: string;
  McpExePath: string;
  ClaudeDir: string;
  ClaudeSettings: string;
  VsCodeDir: string;
  VsCodeMcp: string;
  JsonContent: string;
begin
  if CurStep <> ssPostInstall then Exit;

  AppPath    := ExpandConstant('{app}');
  McpExePath := AppPath + '\fl_mcp_server.exe';

  // 1. FL Studio controller script
  if IsTaskSelected('flstudio') then
  begin
    HardwareDir := FindFLStudioHardwareDir();
    if HardwareDir <> '' then
    begin
      if not DirExists(HardwareDir) then
        ForceDirectories(HardwareDir);
      FileCopy(AppPath + '\fl_studio_script\device_FL_Agent_Controller.py',
               HardwareDir + '\device_FL Agent Controller.py', False);
    end
    else
      MsgBox(
        'FL Studio Hardware folder not found.' + #13#10#13#10 +
        'Copy this file there manually after installing FL Studio:' + #13#10 +
        AppPath + '\fl_studio_script\device_FL_Agent_Controller.py' + #13#10#13#10 +
        'Then restart FL Studio, open Options > MIDI Settings > Input,' + #13#10 +
        'enable the "FL Agent" port and set its controller to' + #13#10 +
        '"FL Agent Controller".',
        mbInformation, MB_OK);
  end;

  // 2. Persist API keys to user environment
  if ApiPage.Values[0] <> '' then
    RegWriteStringValue(HKEY_CURRENT_USER, 'Environment',
                        'OPENROUTER_API_KEY', ApiPage.Values[0]);
  if ApiPage.Values[1] <> '' then
    RegWriteStringValue(HKEY_CURRENT_USER, 'Environment',
                        'ANTHROPIC_API_KEY', ApiPage.Values[1]);

  // 3. Register MCP server with Claude Code
  if IsTaskSelected('configureclaude') then
  begin
    ClaudeDir      := ExpandConstant('{userappdata}') + '\.claude';
    ClaudeSettings := ClaudeDir + '\settings.json';
    if not DirExists(ClaudeDir) then CreateDir(ClaudeDir);
    if not FileExists(ClaudeSettings) then
    begin
      JsonContent :=
        '{' + #13#10 +
        '  "mcpServers": {' + #13#10 +
        '    "fl-agent": {' + #13#10 +
        '      "command": "' + EscapeJsonPath(McpExePath) + '",' + #13#10 +
        '      "args": []' + #13#10 +
        '    }' + #13#10 +
        '  }' + #13#10 +
        '}';
      SaveStringToFile(ClaudeSettings, JsonContent, False);
    end;
  end;

  // 4. Write VS Code / Copilot MCP config
  if IsTaskSelected('configurevscode') then
  begin
    VsCodeDir := AppPath + '\.vscode';
    VsCodeMcp := VsCodeDir + '\mcp.json';
    if not DirExists(VsCodeDir) then CreateDir(VsCodeDir);
    JsonContent :=
      '{' + #13#10 +
      '  "servers": {' + #13#10 +
      '    "fl-agent": {' + #13#10 +
      '      "type": "stdio",' + #13#10 +
      '      "command": "' + EscapeJsonPath(McpExePath) + '",' + #13#10 +
      '      "args": []' + #13#10 +
      '    }' + #13#10 +
      '  }' + #13#10 +
      '}';
    SaveStringToFile(VsCodeMcp, JsonContent, False);
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
end;
