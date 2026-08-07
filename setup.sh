#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

NANOBOT_VERSION="0.1.4.post6"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REQUIREMENTS_FILE="$SCRIPT_DIR/environment/requirements-runtime.txt"
UNIT_TEMPLATE="$SCRIPT_DIR/deployment/nanobot.service.template"
DEPLOY_SCRIPT="$SCRIPT_DIR/deploy.sh"
VENV_DIR="${NANOBOT_VENV:-$HOME/nanobot/bot-env}"
INSTALL_SYSTEMD=0
ALLOW_PYTHON_MISMATCH=0

usage() {
    cat <<'EOF'
Usage: ./setup.sh [options]

Create/recover the pinned nanobot environment, then call deploy.sh to install
this repository's source. The script never starts or enables the service.

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

if [[ ! -x "$DEPLOY_SCRIPT" ]]; then
    echo "ERROR: deployment helper is missing or not executable: $DEPLOY_SCRIPT" >&2
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

DEPLOY_ARGS=(--venv "$VENV_DIR")
if ((ALLOW_PYTHON_MISMATCH)); then
    DEPLOY_ARGS+=(--allow-python-version-mismatch)
fi
"$DEPLOY_SCRIPT" "${DEPLOY_ARGS[@]}"

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
echo "Initial setup/recovery complete."
echo "Review status before starting: systemctl status nanobot --no-pager"
echo "Start deliberately: sudo systemctl start nanobot"
