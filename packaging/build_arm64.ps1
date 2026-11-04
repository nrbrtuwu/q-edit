# Builds packaging\Output\q-edit-Setup-<version>-arm64.exe for Windows on ARM (Snapdragon PCs).
# PyInstaller can only package the Python it runs on, and an x64 PC can't run ARM64 Python, so
# this build ships an ARM64 Python runtime plus clip_editor.py instead of a q-edit.exe; the
# shortcuts run pythonw.exe clip_editor.py. An "installed" marker file makes the script keep its
# settings in %APPDATA%\q-edit like the x64 build.
# First-time setup (downloads into packaging\arm64_dl):
#   python-3.14.9-arm64.exe                     (python.org; unpacked by arm64_python.py)
#   pillow-13.0.0-cp314-cp314-win_arm64.whl     (PyPI)
#   ffmpeg-n8.1-latest-winarm64-gpl-shared-8.1.zip, unzipped into arm64_dl\ffmpeg (BtbN)
#   whisper-bin-win-opencl-adreno-arm64.zip, unzipped into arm64_dl\whisper (whisper.cpp b5454)
#   tkinterdnd2 comes from packaging\.venv (it ships a win-arm64 tkdnd)
$ErrorActionPreference = "Continue"
$here = $PSScriptRoot
$root = Split-Path $here
$dl = "$here\arm64_dl"
$py = Join-Path $here ".venv\Scripts\python.exe"   # the x64 build Python, for the helper scripts

$version = (Select-String -Path "$root\clip_editor.py" -Pattern '^APP_VERSION = "(.+)"').Matches[0].Groups[1].Value
$pyexe = Get-ChildItem "$dl\python-*-arm64.exe" | Select-Object -First 1
$wheel = Get-ChildItem "$dl\pillow-*-win_arm64.whl" | Select-Object -First 1
$ffmpeg = Get-ChildItem "$dl\ffmpeg" -Directory | Where-Object { Test-Path "$($_.FullName)\bin\ffmpeg.exe" } | Select-Object -First 1
$whisper = "$dl\whisper\Release"
if (-not $pyexe) { throw "No python-*-arm64.exe in packaging\arm64_dl" }
if (-not $wheel) { throw "No Pillow win_arm64 wheel in packaging\arm64_dl" }
if (-not $ffmpeg) { throw "No ARM64 FFmpeg in packaging\arm64_dl\ffmpeg" }
if (-not (Test-Path "$whisper\whisper-cli.exe")) { throw "No ARM64 whisper-cli in packaging\arm64_dl\whisper\Release" }
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup not found (winget install JRSoftware.InnoSetup)" }

Write-Host "Building q-edit $version for ARM64 with $($pyexe.Name), $($ffmpeg.Name)"
$runtime = "$dl\python_arm64"
if (-not (Test-Path "$runtime\pythonw.exe")) {
    & $py "$here\arm64_python.py" $pyexe.FullName $runtime
    if ($LASTEXITCODE) { throw "unpacking the ARM64 Python failed" }
}
& $py "$here\make_icon.py"
if ($LASTEXITCODE) { throw "icon failed" }

$app = "$here\dist_arm64\q-edit"
Remove-Item -Recurse -Force "$here\dist_arm64" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $app | Out-Null
Copy-Item -Recurse $runtime "$app\python"
# x64-only pieces the ARM64 Python installer carries: a C++ runtime DLL ARM64 Python doesn't load
# and a Tcl build tool
Remove-Item -Force "$app\python\vcruntime140_1.dll" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$app\python\tcl\nmake" -ErrorAction SilentlyContinue
$site = "$app\python\Lib\site-packages"
New-Item -ItemType Directory -Force $site | Out-Null
& $py -m zipfile -e $wheel.FullName $site   # a wheel is a zip
if ($LASTEXITCODE) { throw "unpacking Pillow failed" }
$dnd = "$here\.venv\Lib\site-packages\tkinterdnd2"
Copy-Item -Recurse $dnd "$site\tkinterdnd2"
Get-ChildItem "$site\tkinterdnd2\tkdnd" -Directory | Where-Object { $_.Name -ne "win-arm64" } |
    Remove-Item -Recurse -Force   # only the ARM64 tkdnd
Get-ChildItem $site -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Copy-Item "$root\clip_editor.py", "$root\clip_editor.ico" $app
Set-Content -Path "$app\installed" -Value "q-edit $version ARM64: settings live in %APPDATA%\q-edit"

& $py "$here\check_arch.py" arm64 $app $ffmpeg.FullName $whisper
if ($LASTEXITCODE) { throw "some program files are not ARM64" }
& $iscc "/DAppVersion=$version" "/DAppDir=$app" "/DFFmpegDir=$($ffmpeg.FullName)" "/DWhisperDir=$whisper" "$here\installer-arm64.iss"
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Get-Item "$here\Output\q-edit-Setup-$version-arm64.exe" | Select-Object Name, @{n="MB"; e={[math]::Round($_.Length / 1MB, 1)}}
