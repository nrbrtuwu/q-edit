# Builds packaging\Output\q-edit-Setup-<version>.exe
#   1. clip_editor.py -> "q-edit.exe" folder (PyInstaller, in packaging\.venv)
#   2. that folder + FFmpeg -> one installer (Inno Setup)
# First-time setup:
#   python -m venv packaging\.venv
#   packaging\.venv\Scripts\python -m pip install pyinstaller pillow tkinterdnd2
#   winget install JRSoftware.InnoSetup --scope user
#   FFmpeg shared build unzipped into packaging\ffmpeg_dl (from github.com/BtbN/FFmpeg-Builds)
# not "Stop": PyInstaller logs to stderr, which PowerShell 5.1 would treat as an error.
# Failures are caught with $LASTEXITCODE checks below.
$ErrorActionPreference = "Continue"
$here = $PSScriptRoot
$root = Split-Path $here
$py = Join-Path $here ".venv\Scripts\python.exe"

$version = (Select-String -Path "$root\clip_editor.py" -Pattern '^APP_VERSION = "(.+)"').Matches[0].Groups[1].Value
$ffmpeg = Get-ChildItem "$here\ffmpeg_dl" -Directory | Where-Object { Test-Path "$($_.FullName)\bin\ffmpeg.exe" } | Select-Object -First 1
if (-not $ffmpeg) { throw "No FFmpeg build found in packaging\ffmpeg_dl" }
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup not found (winget install JRSoftware.InnoSetup)" }

Write-Host "Building q-edit $version with $($ffmpeg.Name)"
& $py "$here\make_icon.py"
if ($LASTEXITCODE) { throw "icon failed" }
# fonts\ holds TikTok Sans (SIL OFL) for the TikTok panel's on-video text
& $py -m PyInstaller --noconfirm --clean --windowed --name "q-edit" `
    --icon "$root\clip_editor.ico" --add-data "$root\clip_editor.ico;." --add-data "$root\fonts;fonts" `
    --collect-data tkinterdnd2 `
    --distpath "$here\dist" --workpath "$here\build" --specpath "$here" "$root\clip_editor.py"
if ($LASTEXITCODE) { throw "PyInstaller failed" }
& $iscc "/DAppVersion=$version" "/DFFmpegDir=$($ffmpeg.FullName)" "$here\installer.iss"
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Get-Item "$here\Output\q-edit-Setup-$version.exe" | Select-Object Name, @{n="MB"; e={[math]::Round($_.Length / 1MB, 1)}}
