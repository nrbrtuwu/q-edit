; Inno Setup script for q-edit. Built by build.ps1, which passes AppVersion and FFmpegDir.
#define AppName "q-edit"
#define AppExe "q-edit.exe"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{B6C3E0A2-6F3E-4E54-9C7B-3F0D7A1C2E11}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; installs for the current user without an admin prompt (can switch to all users in the wizard)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Output
OutputBaseFilename=q-edit-Setup-{#AppVersion}
SetupIconFile=..\clip_editor.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\{#AppName}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "{#FFmpegDir}\bin\*"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion
Source: "{#FFmpegDir}\LICENSE.txt"; DestDir: "{app}\ffmpeg"; DestName: "FFmpeg-LICENSE.txt"

; before 1.1.1 the app was called "Clip Editor"; same AppId, so an upgrade lands here
[InstallDelete]
Type: files; Name: "{app}\Clip Editor.exe"
Type: files; Name: "{autoprograms}\Clip Editor.lnk"
Type: files; Name: "{autodesktop}\Clip Editor.lnk"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
