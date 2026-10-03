#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${NANOBOT_PYTHON:-python3}" "$SCRIPT_DIR/scripts/personal_upgrade.py" deploy "$@"
