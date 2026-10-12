#!/data/data/com.termux/files/usr/bin/bash
# Launch the Ubuntu bot through proot-distro under PM2.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$PROJECT_DIR/.env"
    set +a
fi

if [ -f "$SCRIPT_DIR/paths.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$SCRIPT_DIR/paths.env"
    set +a
fi

DISTRO="${HP_UNGU_DISTRO:-ubuntu}"
UBUNTU_PROJECT_DIR="${UBUNTU_PROJECT_DIR:-/root/hp-ungu-telegram-bot}"

if ! command -v proot-distro >/dev/null 2>&1; then
    echo "[hp-ungu] proot-distro is not installed in Termux." >&2
    exit 127
fi

# Guard: reject Termux paths. Only Ubuntu-side paths are valid here.
case "$UBUNTU_PROJECT_DIR" in
    /root/*|/home/*|/opt/*|/srv/*)
        ;;
    *)
        echo "[hp-ungu] FATAL: UBUNTU_PROJECT_DIR='$UBUNTU_PROJECT_DIR'" >&2
        echo "[hp-ungu] is not an Ubuntu path. Fix termux/paths.env." >&2
        exit 2
        ;;
esac

if ! proot-distro login "$DISTRO" -- /bin/bash -lc \
        "[ -x '$UBUNTU_PROJECT_DIR/.venv/bin/python' ]"; then
    echo "[hp-ungu] FATAL: $UBUNTU_PROJECT_DIR/.venv/bin/python not found." >&2
    echo "[hp-ungu] Create the venv: see README or run setup_ubuntu_inner.sh." >&2
    exit 2
fi

echo "[hp-ungu] entering $DISTRO at $UBUNTU_PROJECT_DIR"

exec proot-distro login "$DISTRO" -- /bin/bash -lc \
    "cd '$UBUNTU_PROJECT_DIR' && exec .venv/bin/python -m ubuntu.bot"
