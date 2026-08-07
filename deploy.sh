#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

NANOBOT_VERSION="0.1.4.post6"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/src/nanobot"
VENV_DIR="${NANOBOT_VENV:-$HOME/nanobot/bot-env}"
BACKUP_ROOT="${NANOBOT_BACKUP_DIR:-$HOME/nanobot/source-backups}"
ALLOW_PYTHON_MISMATCH=0

usage() {
    cat <<'EOF'
Usage: ./deploy.sh [options]

Deploy src/nanobot into an existing pinned nanobot virtualenv. This script does
not run pip and does not start, stop, enable, or restart systemd.

Options:
  --venv PATH                       Override the virtualenv destination
  --allow-python-version-mismatch   Permit a Python minor other than 3.12
  -h, --help                        Show this help

Environment:
  NANOBOT_VENV         Alternative to --venv
  NANOBOT_BACKUP_DIR   Directory for pre-deployment source backups
EOF
}

while (($#)); do
    case "$1" in
        --venv)
            if (($# < 2)); then
                echo "ERROR: --venv requires a path" >&2
                exit 2
            fi
            VENV_DIR="$2"
            shift 2
            ;;
        --allow-python-version-mismatch)
            ALLOW_PYTHON_MISMATCH=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if ((EUID == 0)); then
    echo "ERROR: run deploy.sh as the nanobot user, not as root." >&2
    exit 1
fi

if [[ ! -d "$SOURCE_DIR" || ! -f "$SOURCE_DIR/__init__.py" ]]; then
    echo "ERROR: repository source is incomplete: $SOURCE_DIR" >&2
    exit 1
fi

if command -v systemctl >/dev/null 2>&1 &&
   systemctl is-active --quiet nanobot.service 2>/dev/null; then
    echo "ERROR: nanobot.service is active." >&2
    echo "Stop it deliberately before deploying: sudo systemctl stop nanobot" >&2
    exit 1
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    echo "ERROR: no usable virtualenv Python at $VENV_DIR/bin/python" >&2
    echo "Use setup.sh for a first installation or recovery." >&2
    exit 1
fi

RUNTIME_PYTHON="$VENV_DIR/bin/python"
RUNTIME_VERSION="$("$RUNTIME_PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$RUNTIME_VERSION" != "3.12" && "$ALLOW_PYTHON_MISMATCH" -ne 1 ]]; then
    echo "ERROR: virtualenv uses Python $RUNTIME_VERSION, not the preserved Python 3.12." >&2
    echo "Pass --allow-python-version-mismatch only after testing." >&2
    exit 1
fi

INSTALLED_VERSION="$("$RUNTIME_PYTHON" - <<'PY'
from importlib.metadata import PackageNotFoundError, version
try:
    print(version("nanobot-ai"))
except PackageNotFoundError:
    pass
PY
)"

if [[ "$INSTALLED_VERSION" != "$NANOBOT_VERSION" ]]; then
    echo "ERROR: expected nanobot-ai==$NANOBOT_VERSION, found ${INSTALLED_VERSION:-nothing}." >&2
    echo "deploy.sh never installs, upgrades, or replaces distribution metadata." >&2
    exit 1
fi

SITE_PACKAGES="$("$RUNTIME_PYTHON" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
TARGET_DIR="$SITE_PACKAGES/nanobot"

if [[ "$SITE_PACKAGES" != "$VENV_DIR"/* ]]; then
    echo "ERROR: refusing unexpected site-packages path: $SITE_PACKAGES" >&2
    exit 1
fi

if [[ -d "$TARGET_DIR" ]] &&
   diff -qr --exclude='__pycache__' --exclude='*.pyc' \
       "$SOURCE_DIR" "$TARGET_DIR" >/dev/null; then
    echo "Deployed package already matches repository source; nothing to do."
    exit 0
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$$"
STAGE_DIR="$SITE_PACKAGES/.nanobot-stage-$STAMP"
BACKUP_DIR="$BACKUP_ROOT/nanobot-$STAMP"

if [[ -e "$STAGE_DIR" || -e "$BACKUP_DIR" ]]; then
    echo "ERROR: staging or backup destination already exists." >&2
    exit 1
fi

install -d -m 0700 -- "$BACKUP_DIR"
install -d -- "$STAGE_DIR"
cp -a -- "$SOURCE_DIR/." "$STAGE_DIR/"

echo "Compiling staged Python files as a syntax check..."
if ! "$RUNTIME_PYTHON" -m compileall -q "$STAGE_DIR"; then
    echo "ERROR: compilation failed; deployed code was not changed." >&2
    mv -- "$STAGE_DIR" "$BACKUP_DIR/rejected-stage"
    exit 1
fi

HAD_TARGET=0
if [[ -d "$TARGET_DIR" ]]; then
    HAD_TARGET=1
    mv -- "$TARGET_DIR" "$BACKUP_DIR/nanobot"
fi

if ! mv -- "$STAGE_DIR" "$TARGET_DIR"; then
    echo "ERROR: deployment move failed; attempting rollback." >&2
    if ((HAD_TARGET)) && [[ ! -e "$TARGET_DIR" ]]; then
        mv -- "$BACKUP_DIR/nanobot" "$TARGET_DIR"
    fi
    exit 1
fi

if ! DEPLOYED_FILE="$("$RUNTIME_PYTHON" -c 'import nanobot; print(nanobot.__file__)')"; then
    echo "ERROR: deployed package failed its import check; attempting rollback." >&2
    mv -- "$TARGET_DIR" "$BACKUP_DIR/failed-deployment"
    if ((HAD_TARGET)); then
        mv -- "$BACKUP_DIR/nanobot" "$TARGET_DIR"
    fi
    exit 1
fi

echo "Deployed customized source: $DEPLOYED_FILE"
if ((HAD_TARGET)); then
    echo "Previous source backup: $BACKUP_DIR/nanobot"
else
    echo "No previous package directory existed; backup directory: $BACKUP_DIR"
fi
echo "deploy.sh did not change systemd state."
