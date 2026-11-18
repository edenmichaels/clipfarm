# Build the ClipFarm Windows installer (NSIS .exe) from a fresh clone.
# Requirements (one-time): Python 3.11/3.12, Node 20+, Rust (rustup), and the
# "Desktop development with C++" workload from Visual Studio Build Tools.
# Usage (PowerShell, from repo root):   ./scripts/build_windows.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== 1/5 Python engine ==" -ForegroundColor Cyan
Set-Location backend
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\pip install -r requirements.txt

Write-Host "== 2/5 FFmpeg + font ==" -ForegroundColor Cyan
if (-not (Test-Path bin\ffmpeg.exe)) {
  # Static GPL build from BtbN (includes NVENC/AMF/QSV encoders and libass).
  $ProgressPreference = "SilentlyContinue"
  Invoke-WebRequest "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" -OutFile ffmpeg.zip
  Expand-Archive ffmpeg.zip -DestinationPath ffmpeg_tmp -Force
  Get-ChildItem ffmpeg_tmp -Recurse -Include ffmpeg.exe, ffprobe.exe | Copy-Item -Destination bin\
  Remove-Item ffmpeg.zip, ffmpeg_tmp -Recurse -Force
}
if (-not (Test-Path assets\fonts\Anton-Regular.ttf)) {
  # Anton is SIL Open Font License — free to bundle.
  Invoke-WebRequest "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf" -OutFile assets\fonts\Anton-Regular.ttf
}

$env:PATH = "$PWD\bin;$env:PATH"
.\.venv\Scripts\python -m pytest -q

Write-Host "== 3/5 Freeze engine with PyInstaller ==" -ForegroundColor Cyan
$triple = (rustc -vV | Select-String "host:").ToString().Split(" ")[1]
.\.venv\Scripts\pyinstaller --noconfirm --onefile --name "clipfarm-engine-$triple" `
  --add-data "clipfarm/schema.sql;clipfarm" `
  --collect-submodules uvicorn --collect-data cv2 --collect-all faster_whisper --collect-all ctranslate2 `
  --collect-submodules yt_dlp --collect-all keyring --hidden-import win32ctypes.core `
  --noconsole run_engine.py
New-Item -ItemType Directory -Force ..\app\src-tauri\binaries | Out-Null
Copy-Item "dist\clipfarm-engine-$triple.exe" ..\app\src-tauri\binaries\ -Force
Set-Location ..

Write-Host "== 4/5 Frontend deps + icons ==" -ForegroundColor Cyan
Set-Location app
npm install --no-audit --no-fund
if (-not (Test-Path src-tauri\icons\icon.ico)) { npx tauri icon app-icon.png }

Write-Host "== 5/5 Tauri build (NSIS installer) ==" -ForegroundColor Cyan
npx tauri build
Write-Host "Installer: app\src-tauri\target\release\bundle\nsis\" -ForegroundColor Green
