#!/data/data/com.termux/files/usr/bin/bash
# Pull the latest code, refresh Ubuntu dependencies and restart PM2.
set -euo pipefail

PULL=1
for arg in "$@"; do
    case "$arg" in
        --no-pull) PULL=0 ;;
        -h|--help) echo "Usage: bash termux/update.sh [--no-pull]"; exit 0 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PATHS_FILE="$SCRIPT_DIR/paths.env"

say()  { printf '\033[1;36m[hp-ungu]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[hp-ungu]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[hp-ungu]\033[0m %s\n' "$*" >&2; exit 1; }

cd "$PROJECT_DIR"

PREVIOUS_SHA="$(git rev-parse HEAD 2>/dev/null || echo "")"
say "current revision: ${PREVIOUS_SHA:-unknown}"

if [ "$PULL" -eq 1 ]; then
    git fetch --all --prune
    git pull --ff-only
fi

if [ ! -f "$PATHS_FILE" ]; then
    die "termux/paths.env is missing. Run: bash termux/install.sh --repair"
fi
# shellcheck disable=SC1090
set -a; . "$PATHS_FILE"; set +a

DISTRO="${HP_UNGU_DISTRO:-ubuntu}"
UBUNTU_PROJECT_DIR="${UBUNTU_PROJECT_DIR:-/root/hp-ungu-telegram-bot}"

say "refreshing Ubuntu dependencies"
proot-distro login "$DISTRO" -- /bin/bash -lc \
    "cd '$UBUNTU_PROJECT_DIR' && .venv/bin/python -m pip install -r requirements.txt"

say "restarting PM2 processes"
pm2 restart hp-ungu-bridge --update-env
pm2 restart hp-ungu-ubuntu-bot --update-env
pm2 save

sleep 4
bash "$SCRIPT_DIR/healthcheck.sh" || warn "health check reported problems"

cat <<EOF
$(say "update complete")

Rollback if needed:
  cd $PROJECT_DIR
  git checkout ${PREVIOUS_SHA:-<previous-sha>}
  bash termux/update.sh --no-pull
EOF
