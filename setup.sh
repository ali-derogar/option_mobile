#!/bin/bash
# ── Setup script for Options BeeWare project ───────────────────────────────
set -e
cd "$(dirname "$0")"

echo "=== Options — BeeWare Setup ==="

# ── Step 1: system dependencies ───────────────────────────────────────────
echo ""
echo "[1/3] Checking system dependencies..."

MISSING_PKGS=()
for pkg in libcairo2-dev libgirepository-2.0-dev; do
    dpkg -s "$pkg" &>/dev/null || MISSING_PKGS+=("$pkg")
done

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo "Installing system packages: ${MISSING_PKGS[*]}"
    if command -v sudo &>/dev/null; then
        sudo apt-get install -y "${MISSING_PKGS[@]}"
    else
        echo "ERROR: sudo not available. Please install manually:"
        echo "  apt-get install -y ${MISSING_PKGS[*]}"
        exit 1
    fi
else
    echo "System dependencies OK."
fi

# ── Step 2: create virtualenv ─────────────────────────────────────────────
echo ""
echo "[2/3] Creating virtual environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv --system-site-packages
fi

# ── Step 3: install Python packages ──────────────────────────────────────
echo ""
echo "[3/3] Installing Python packages..."
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Test on desktop:"
echo "  source .venv/bin/activate && briefcase dev"
echo ""
echo "Build Android APK:"
echo "  source .venv/bin/activate"
echo "  briefcase create android"
echo "  briefcase build android"
echo "  briefcase run android"
