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
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=Output
OutputBaseFilename=FL_Agent_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#AppExeName}

; ============================================================================
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

; ============================================================================
[Files]
Source: "dist\FL Agent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "fl_studio_script\device_FL_Agent_Controller.py"; DestDir: "{app}\fl_studio_script"; Flags: ignoreversion

; ============================================================================
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

; ============================================================================
[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName} now"; Flags: nowait postinstall skipifsilent
