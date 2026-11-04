; Inno Setup script for the ARM64 (Windows on ARM) build of q-edit. Built by build_arm64.ps1,
; which passes AppVersion, AppDir, FFmpegDir and WhisperDir. There is no q-edit.exe here: the
; shortcuts start clip_editor.py with the ARM64 Python in {app}\python.
#define AppName "q-edit"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
; the same AppId as the x64 build, so one replaces the other
AppId={{B6C3E0A2-6F3E-4E54-9C7B-3F0D7A1C2E11}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion} (ARM64)
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=arm64
ArchitecturesInstallIn64BitMode=arm64
OutputDir=Output
OutputBaseFilename=q-edit-Setup-{#AppVersion}-arm64
SetupIconFile=..\clip_editor.ico
UninstallDisplayIcon={app}\clip_editor.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#AppDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "{#FFmpegDir}\bin\*"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion
Source: "{#FFmpegDir}\LICENSE.txt"; DestDir: "{app}\ffmpeg"; DestName: "FFmpeg-LICENSE.txt"
; whisper.cpp ARM64 build with the Snapdragon (Adreno, OpenCL) GPU backend
Source: "{#WhisperDir}\whisper-cli.exe"; DestDir: "{app}\whisper"; Flags: ignoreversion
Source: "{#WhisperDir}\whisper.dll"; DestDir: "{app}\whisper"; Flags: ignoreversion
Source: "{#WhisperDir}\ggml*.dll"; DestDir: "{app}\whisper"; Flags: ignoreversion
Source: "{#WhisperDir}\libomp*.dll"; DestDir: "{app}\whisper"; Flags: ignoreversion
Source: "whisper-LICENSE.txt"; DestDir: "{app}\whisper"

[InstallDelete]
; what an x64 install left behind
Type: files; Name: "{app}\q-edit.exe"
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\whisper"
Type: files; Name: "{app}\Clip Editor.exe"
Type: files; Name: "{autoprograms}\Clip Editor.lnk"
Type: files; Name: "{autodesktop}\Clip Editor.lnk"

[UninstallDelete]
; Python's bytecode caches, written on first run
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\python"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\clip_editor.py"""; WorkingDir: "{app}"; IconFilename: "{app}\clip_editor.ico"; AppUserModelID: "q-edit"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\clip_editor.py"""; WorkingDir: "{app}"; IconFilename: "{app}\clip_editor.ico"; AppUserModelID: "q-edit"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\clip_editor.py"""; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
