#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "=== Nanobot Deploy Script ==="
echo "Script directory: $SCRIPT_DIR"

# 1. Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | grep -oP '\d+\.\d+')
MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
if [[ "$MAJOR" -lt 3 ]] || { [[ "$MAJOR" -eq 3 ]] && [[ "$MINOR" -lt 11 ]]; }; then
    echo "ERROR: Python >= 3.11 required, found $(python3 --version)"
    exit 1
fi
echo "[OK] Python $(python3 --version)"

# 2. Create venv
VENV_DIR="$HOME/nanobot/bot-env"
if [[ -d "$VENV_DIR" ]] && [[ -f "$VENV_DIR/bin/pip" ]]; then
    echo "[WARN] $VENV_DIR already exists with pip, skipping venv creation"
else
    # Remove broken venv if it exists (e.g. missing pip from previous failed attempt)
    if [[ -d "$VENV_DIR" ]]; then
        echo "[WARN] Removing incomplete venv (missing pip)"
        rm -rf "$VENV_DIR"
    fi
    mkdir -p "$HOME/nanobot"
    python3 -m venv "$VENV_DIR"
    # On Debian/Ubuntu, python3-venv may not be installed, leaving pip missing.
    # Try ensurepip to bootstrap it.
    if [[ ! -f "$VENV_DIR/bin/pip" ]]; then
        echo "[WARN] pip missing from venv, trying ensurepip..."
        "$VENV_DIR/bin/python3" -m ensurepip --upgrade 2>/dev/null || true
    fi
    if [[ ! -f "$VENV_DIR/bin/pip" ]]; then
        echo "ERROR: Could not create a working venv. Install python3-venv first:"
        echo "  sudo apt install python3-venv"
        exit 1
    fi
    echo "[OK] Created venv at $VENV_DIR"
fi

# 3. Install nanobot-ai at the exact pinned version
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install nanobot-ai==0.1.4.post6
echo "[OK] Installed nanobot-ai==0.1.4.post6"

# 4. Find site-packages
SITE_PKG=""
for candidate in "$VENV_DIR"/lib/python*/site-packages; do
    if [[ -d "$candidate" ]]; then
        SITE_PKG="$candidate"
        break
    fi
done
if [[ -z "$SITE_PKG" ]]; then
    echo "ERROR: Cannot find site-packages directory in $VENV_DIR"
    exit 1
fi
echo "[OK] Found site-packages: $SITE_PKG"

# 5. Apply custom patch
cd "$SITE_PKG"
patch -p0 < "$SCRIPT_DIR/nanobot-custom.patch"
echo "[OK] Applied custom patch"

# 6. Install systemd service
if [[ -f /etc/systemd/system/nanobot.service ]]; then
    echo "[WARN] nanobot.service already exists, skipping (manual update needed)"
else
    sudo cp "$SCRIPT_DIR/nanobot.service" /etc/systemd/system/nanobot.service
    sudo systemctl daemon-reload
    echo "[OK] Installed systemd service"
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Run the onboarding wizard:  $VENV_DIR/bin/nanobot onboard --wizard"
echo "  2. Enable the service:         sudo systemctl enable nanobot"
echo "  3. Start the service:          sudo systemctl start nanobot"
echo "  4. Check status:               sudo systemctl status nanobot"
echo "  5. View logs:                  sudo journalctl -u nanobot -f"
