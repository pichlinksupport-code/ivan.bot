#!/usr/bin/env bash
# setup.sh - one-time setup for the YouTube bot (macOS / Linux)
#
# Run in the VS Code terminal, inside the youtube-bot folder:
#     bash setup.sh

set -euo pipefail
cd "$(dirname "$0")"

step() { printf "\n\033[36m==> %s\033[0m\n" "$1"; }
ok()   { printf "    \033[32mOK\033[0m  %s\n" "$1"; }
warn() { printf "    \033[33m!!\033[0m  %s\n" "$1"; }

OS="$(uname -s)"

# ---------------------------------------------------------------- system tools
step "Checking Python and FFmpeg"
if [ "$OS" = "Darwin" ]; then
  command -v brew >/dev/null || { echo "Install Homebrew first: https://brew.sh"; exit 1; }
  command -v python3 >/dev/null || brew install python@3.12
  command -v ffmpeg  >/dev/null || brew install ffmpeg
else
  if ! command -v python3 >/dev/null || ! command -v ffmpeg >/dev/null || ! python3 -m venv --help >/dev/null 2>&1; then
    warn "Installing python3, venv, pip and ffmpeg with apt (needs sudo)..."
    sudo apt-get update
    sudo apt-get install -y python3 python3-venv python3-pip ffmpeg curl
  fi
fi
ok "python3 $(python3 --version | cut -d' ' -f2) and ffmpeg found"

# ---------------------------------------------------------------- venv + packages
step "Creating virtual environment (.venv) and installing packages (a few minutes)"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
ok "Packages installed"

# ---------------------------------------------------------------- Kokoro files
step "Kokoro voice files"
BASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
for f in kokoro-v1.0.onnx voices-v1.0.bin; do
  if [ -f "$f" ]; then ok "$f already here"; else
    echo "    downloading $f ..."
    curl -L --fail -o "$f" "$BASE/$f"
    ok "$f downloaded"
  fi
done

# ---------------------------------------------------------------- folders + .env + .gitignore
step "Folders and files"
mkdir -p assets output .vscode
ok "assets/ (put background.mp4 and music.mp3 here) and output/ ready"

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  warn ".env created from .env.example - fill in your keys."
fi

if [ ! -f .gitignore ]; then
  printf ".env\n.venv/\noutput/\ntoken.json\nclient_secret.json\n*.onnx\n*.bin\n__pycache__/\n" > .gitignore
  ok ".gitignore created (keeps your keys out of git)"
fi

# ---------------------------------------------------------------- VS Code
step "VS Code extensions and settings"
cat > .vscode/settings.json <<'EOF'
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
  "python.terminal.activateEnvironment": true,
  "files.associations": { ".env*": "dotenv" }
}
EOF
cat > .vscode/launch.json <<'EOF'
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
EOF
ok ".vscode settings written (uses the .venv automatically, F5 runs the bot)"

if command -v code >/dev/null; then
  for ext in ms-python.python ms-python.vscode-pylance ms-python.debugpy mikestead.dotenv; do
    code --install-extension "$ext" --force >/dev/null && ok "extension $ext"
  done
else
  warn "'code' command not found. In VS Code press Ctrl/Cmd+Shift+P, run 'Shell Command: Install code command in PATH', then rerun. Or install by hand: Python, Pylance, Python Debugger, DotENV."
fi

# ---------------------------------------------------------------- verify
step "Final check"
.venv/bin/python -c "import requests, numpy, soundfile, dotenv, supabase, kokoro_onnx, googleapiclient; print('    All Python packages import correctly')"

printf "\n\033[32mDONE.\033[0m\nNext: fill in .env, put background.mp4 + music.mp3 in assets/, then run:\n    .venv/bin/python bot.py --no-upload\n"