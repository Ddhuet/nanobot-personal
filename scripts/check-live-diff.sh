#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="${NANOBOT_VENV:-$HOME/nanobot/bot-env}"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    echo "ERROR: no usable virtualenv Python at $VENV_DIR/bin/python" >&2
    exit 2
fi

SITE_PACKAGES="$("$VENV_DIR/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
LIVE_PACKAGE="$SITE_PACKAGES/nanobot"

if [[ ! -d "$LIVE_PACKAGE" ]]; then
    echo "ERROR: deployed package not found at $LIVE_PACKAGE" >&2
    exit 2
fi

echo "Repository: $REPO_DIR/src/nanobot"
echo "Deployed:   $LIVE_PACKAGE"
diff -qr --exclude='__pycache__' --exclude='*.pyc' \
    "$REPO_DIR/src/nanobot" "$LIVE_PACKAGE"
