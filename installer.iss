; Absolute minimum — used to diagnose CI compilation failures.
; No preprocessor defines, no [Code], no [Tasks].
[Setup]
AppName=FL Agent
AppVersion=1.0.0
DefaultDirName={autopf}\FL Agent
DefaultGroupName=FL Agent
OutputDir=Output
OutputBaseFilename=FL_Agent_Setup
Compression=zip
WizardStyle=modern
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\FL Agent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "fl_studio_script\device_FL_Agent_Controller.py"; DestDir: "{app}\fl_studio_script"; Flags: ignoreversion

[Icons]
Name: "{group}\FL Agent"; Filename: "{app}\FL Agent.exe"
Name: "{group}\Uninstall FL Agent"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\FL Agent.exe"; Description: "Launch FL Agent now"; Flags: nowait postinstall skipifsilent
