# setup.ps1 - one-time setup for the YouTube bot (Windows)
#
# How to run (in VS Code: Terminal -> New Terminal, inside the youtube-bot folder):
#     powershell -ExecutionPolicy Bypass -File .\setup.ps1
#
# It installs: Python (if missing), FFmpeg (if missing), a virtual environment,
# all Python packages, the Kokoro voice files, and the VS Code extensions.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # makes downloads much faster
Set-Location $PSScriptRoot

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    OK  $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    !!  $msg" -ForegroundColor Yellow }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" } }

# ---------------------------------------------------------------- Python
Step "Checking Python"
$ver = $null
try {
    $ver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -ne 0) { $ver = $null }
} catch { $ver = $null }

if (-not $ver) {
    Warn "Python not found. Installing Python 3.12 with winget..."
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    Write-Host "`nPython installed. CLOSE this terminal, open a NEW one, and run setup.ps1 again." -ForegroundColor Yellow
    exit 0
}
$parts = $ver.Split(".")
if ([int]$parts[0] -lt 3 -or [int]$parts[1] -lt 10) { throw "Python $ver is too old. Install Python 3.10 - 3.13." }
if ([int]$parts[1] -ge 14) { Warn "Python $ver is very new; if a package fails to install, use Python 3.12." }
Ok "Python $ver"

# ---------------------------------------------------------------- venv + packages
Step "Creating virtual environment (.venv)"
if (-not (Test-Path ".venv")) { & python -m venv .venv; Check "venv creation" }
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
Ok ".venv ready"

Step "Installing Python packages (this can take a few minutes)"
& $venvPy -m pip install --upgrade pip
Check "pip upgrade"
& $venvPy -m pip install -r requirements.txt
Check "package install"
Ok "Packages installed"

# ---------------------------------------------------------------- FFmpeg
Step "Checking FFmpeg"
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Warn "FFmpeg not found. Installing with winget..."
    winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Ok "FFmpeg found"
} else {
    Warn "FFmpeg installed but not visible yet. Close and reopen VS Code before running the bot."
}

# ---------------------------------------------------------------- Kokoro files
Step "Kokoro voice files"
$base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
function Get-File($name) {
    if (Test-Path $name) { Ok "$name already here"; return }
    Write-Host "    downloading $name ..."
    Invoke-WebRequest -Uri "$base/$name" -OutFile $name
    Ok "$name downloaded"
}
Get-File "kokoro-v1.0.onnx"
Get-File "voices-v1.0.bin"

# ---------------------------------------------------------------- folders + .env + .gitignore
Step "Folders and files"
New-Item -ItemType Directory -Force -Path "assets", "output", ".vscode" | Out-Null
Ok "assets/ (put background.mp4 and music.mp3 here) and output/ ready"

if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") { Copy-Item ".env.example" ".env"; Warn ".env created from .env.example - fill in your keys." }
} else { Ok ".env exists" }

if (-not (Test-Path ".gitignore")) {
    [System.IO.File]::WriteAllText("$PSScriptRoot\.gitignore", ".env`n.venv/`noutput/`ntoken.json`nclient_secret.json`n*.onnx`n*.bin`n__pycache__/`n")
    Ok ".gitignore created (keeps your keys out of git)"
}

# ---------------------------------------------------------------- VS Code
Step "VS Code extensions and settings"
$settings = @'
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/Scripts/python.exe",
  "python.terminal.activateEnvironment": true,
  "files.associations": { ".env*": "dotenv" }
}
'@
$launch = @'
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Bot: build video only (no upload)",
      "type": "debugpy",
      "request": "launch",
      "program": "${workspaceFolder}/bot.py",
      "args": ["--no-upload"],
      "console": "integratedTerminal"
    },
    {
      "name": "Bot: build and upload",
      "type": "debugpy",
      "request": "launch",
      "program": "${workspaceFolder}/bot.py",
      "console": "integratedTerminal"
    }
  ]
}
'@
[System.IO.File]::WriteAllText("$PSScriptRoot\.vscode\settings.json", $settings)
[System.IO.File]::WriteAllText("$PSScriptRoot\.vscode\launch.json", $launch)
Ok ".vscode settings written (uses the .venv automatically, F5 runs the bot)"

if (Get-Command code -ErrorAction SilentlyContinue) {
    foreach ($ext in @("ms-python.python", "ms-python.vscode-pylance", "ms-python.debugpy", "mikestead.dotenv")) {
        & code --install-extension $ext --force | Out-Null
        Ok "extension $ext"
    }
} else {
    Warn "The 'code' command was not found. In VS Code press Ctrl+Shift+P and run 'Shell Command: Install code command in PATH', then rerun this script. Or install these by hand: Python, Pylance, Python Debugger, DotENV."
}

# ---------------------------------------------------------------- verify
Step "Final check"
& $venvPy -c "import requests, numpy, soundfile, dotenv, supabase, kokoro_onnx, googleapiclient; print('    All Python packages import correctly')"
Check "package import test"

Write-Host "`nDONE." -ForegroundColor Green
Write-Host "Next: fill in .env, put background.mp4 + music.mp3 in assets/, then run:"
Write-Host "    .\.venv\Scripts\python.exe bot.py --no-upload"