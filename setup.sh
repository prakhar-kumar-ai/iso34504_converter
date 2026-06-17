#!/usr/bin/env bash
# One-time setup for the ISO 34504 -> SAGA converter.
set -e
cd "$(dirname "$0")"

echo "=== ISO 34504 -> SAGA converter setup ==="

# 1. Python check
if ! command -v python3 >/dev/null; then
    echo "ERROR: python3 not found. Install Python 3.9+ first."
    exit 1
fi
echo "Python: $(python3 --version)"

# 2. tkinter check (needed for the GUI only)
if python3 -c "import tkinter" 2>/dev/null; then
    echo "tkinter: OK"
else
    echo "WARNING: tkinter not available - GUI won't work (CLI still fine)."
    echo "  Ubuntu/Debian: sudo apt install python3-tk"
    echo "  macOS (brew):  brew install python-tk"
fi

# 3. Dependencies
echo "Installing dependencies..."
python3 -m pip install --user -r requirements.txt

# 4. .env
if [ ! -f .env ]; then
    cp .env.example .env
    echo ".env: created"
else
    echo ".env: already exists"
fi

echo ""
echo "Setup done. Next steps:"
echo "  GUI:  python3 gui.py   (asks for your Anthropic API key on first Convert)"
echo "  CLI:  paste your key into .env, then:"
echo "        python3 converter.py --input /path/to/yaml_folder --verify -v"
