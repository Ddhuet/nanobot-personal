#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

NANOBOT_VERSION="0.1.4.post6"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/src/nanobot"
REQUIREMENTS_FILE="$SCRIPT_DIR/environment/requirements-runtime.txt"
UNIT_TEMPLATE="$SCRIPT_DIR/deployment/nanobot.service.template"
VENV_DIR="${NANOBOT_VENV:-$HOME/nanobot/bot-env}"
BACKUP_ROOT="${NANOBOT_BACKUP_DIR:-$HOME/nanobot/source-backups}"
INSTALL_SYSTEMD=0
ALLOW_PYTHON_MISMATCH=0

usage() {
    cat <<'EOF'
Usage: ./setup.sh [options]

Install the pinned nanobot environment when absent and deploy this repository's
src/nanobot tree. The script never starts or enables the service.

Options:
  --venv PATH                       Override the virtualenv destination
  --install-systemd                 Install the system unit when none exists
  --allow-python-version-mismatch   Permit a Python minor other than 3.12
  -h, --help                        Show this help

Environment:
  NANOBOT_PYTHON       Python used to create a missing virtualenv
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
        --install-systemd)
            INSTALL_SYSTEMD=1
            shift
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
    echo "ERROR: run this script as the intended nanobot user, not as root." >&2
    echo "It invokes sudo only when --install-systemd is requested." >&2
    exit 1
fi

if [[ ! -d "$SOURCE_DIR" || ! -f "$SOURCE_DIR/__init__.py" ]]; then
    echo "ERROR: repository source is incomplete: $SOURCE_DIR" >&2
    exit 1
fi

if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
    echo "ERROR: dependency snapshot is missing: $REQUIREMENTS_FILE" >&2
    exit 1
fi

if command -v systemctl >/dev/null 2>&1 &&
   systemctl is-active --quiet nanobot.service 2>/dev/null; then
    echo "ERROR: nanobot.service is active." >&2
    echo "Stop it deliberately before deploying: sudo systemctl stop nanobot" >&2
    exit 1
fi

PYTHON_BIN="${NANOBOT_PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
    if command -v python3.12 >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v python3.12)"
    elif command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v python3)"
    else
        echo "ERROR: Python 3 was not found." >&2
        exit 1
    fi
fi

if [[ -x "$VENV_DIR/bin/python" ]]; then
    RUNTIME_PYTHON="$VENV_DIR/bin/python"
else
    if [[ -e "$VENV_DIR" ]]; then
        echo "ERROR: $VENV_DIR exists but is not a usable virtualenv." >&2
        echo "Nothing was removed. Move it aside manually after inspection." >&2
        exit 1
    fi

    CREATOR_VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    if [[ "$CREATOR_VERSION" != "3.12" && "$ALLOW_PYTHON_MISMATCH" -ne 1 ]]; then
        echo "ERROR: the preserved deployment used Python 3.12; found $CREATOR_VERSION." >&2
        echo "Install Python 3.12 or pass --allow-python-version-mismatch after testing." >&2
        exit 1
    fi

    "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || {
        echo "ERROR: nanobot requires Python 3.11 or newer." >&2
        exit 1
    }

    install -d -- "$(dirname -- "$VENV_DIR")"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    RUNTIME_PYTHON="$VENV_DIR/bin/python"
    echo "Created virtualenv: $VENV_DIR"
fi

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

if [[ -z "$INSTALLED_VERSION" ]]; then
    echo "Installing the exact captured dependency versions into the new virtualenv..."
    "$RUNTIME_PYTHON" -m pip install --requirement "$REQUIREMENTS_FILE"
elif [[ "$INSTALLED_VERSION" != "$NANOBOT_VERSION" ]]; then
    echo "ERROR: expected nanobot-ai==$NANOBOT_VERSION, found $INSTALLED_VERSION." >&2
    echo "The script will not replace or upgrade an unexpected installation." >&2
    exit 1
else
    echo "Found nanobot-ai==$INSTALLED_VERSION; pip installation is not needed."
fi

SITE_PACKAGES="$("$RUNTIME_PYTHON" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
TARGET_DIR="$SITE_PACKAGES/nanobot"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
STAGE_DIR="$SITE_PACKAGES/.nanobot-stage-$STAMP-$$"
BACKUP_DIR="$BACKUP_ROOT/nanobot-$STAMP"

if [[ "$SITE_PACKAGES" != "$VENV_DIR"/* ]]; then
    echo "ERROR: refusing unexpected site-packages path: $SITE_PACKAGES" >&2
    exit 1
fi

if [[ -e "$STAGE_DIR" || -e "$BACKUP_DIR" ]]; then
    echo "ERROR: staging or backup destination already exists." >&2
    exit 1
fi

install -d -m 0700 -- "$BACKUP_DIR"
install -d -- "$STAGE_DIR"
cp -a -- "$SOURCE_DIR/." "$STAGE_DIR/"

echo "Compiling staged Python files as a syntax check..."
if ! "$RUNTIME_PYTHON" -m compileall -q "$STAGE_DIR"; then
    echo "ERROR: source compilation failed; deployed code was not changed." >&2
    rm -rf -- "$STAGE_DIR"
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

DEPLOYED_FILE="$("$RUNTIME_PYTHON" -c 'import nanobot; print(nanobot.__file__)')"
echo "Deployed customized source: $DEPLOYED_FILE"
if ((HAD_TARGET)); then
    echo "Previous source backup: $BACKUP_DIR/nanobot"
else
    echo "No previous package directory existed; backup directory: $BACKUP_DIR"
fi

if ((INSTALL_SYSTEMD)); then
    if [[ ! -f "$UNIT_TEMPLATE" ]]; then
        echo "ERROR: systemd template is missing: $UNIT_TEMPLATE" >&2
        exit 1
    fi

    if [[ -e /etc/systemd/system/nanobot.service ]]; then
        echo "System unit already exists; it was not overwritten:"
        echo "  /etc/systemd/system/nanobot.service"
    else
        RUN_USER="$(id -un)"
        RUN_GROUP="$(id -gn)"
        WORK_DIR="$(dirname -- "$VENV_DIR")"
        TMP_UNIT="$(mktemp)"
        trap 'rm -f -- "$TMP_UNIT"' EXIT

        escape_sed() {
            printf '%s' "$1" | sed 's/[&|\\]/\\&/g'
        }

        sed \
            -e "s|@USER@|$(escape_sed "$RUN_USER")|g" \
            -e "s|@GROUP@|$(escape_sed "$RUN_GROUP")|g" \
            -e "s|@HOME@|$(escape_sed "$HOME")|g" \
            -e "s|@WORKDIR@|$(escape_sed "$WORK_DIR")|g" \
            -e "s|@VENV@|$(escape_sed "$VENV_DIR")|g" \
            "$UNIT_TEMPLATE" > "$TMP_UNIT"

        sudo install -o root -g root -m 0644 "$TMP_UNIT" \
            /etc/systemd/system/nanobot.service
        sudo systemctl daemon-reload
        echo "Installed systemd unit. It was not enabled or started."
    fi
fi

echo
echo "Setup/deployment complete."
echo "Review status before starting: systemctl status nanobot --no-pager"
echo "Start deliberately: sudo systemctl start nanobot"
