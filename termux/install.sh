#!/data/data/com.termux/files/usr/bin/bash
# hp-ungu installer — runs in NATIVE Termux.
set -euo pipefail

REPAIR=0
for arg in "$@"; do
    case "$arg" in
        --repair) REPAIR=1 ;;
        -h|--help) echo "Usage: bash termux/install.sh [--repair]"; exit 0 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_DIR/.env"
PATHS_FILE="$SCRIPT_DIR/paths.env"

BRIDGE_NAME="hp-ungu-bridge"
BOT_NAME="hp-ungu-ubuntu-bot"
BOOT_DIR="$HOME/.termux/boot"
BOOT_SCRIPT="$BOOT_DIR/10-hp-ungu.sh"

say()  { printf '\033[1;36m[hp-ungu]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[hp-ungu]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[hp-ungu]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 0. sanity
if [ -z "${PREFIX:-}" ] || [[ "$PREFIX" != *"com.termux"* ]]; then
    die "This script must run inside Termux."
fi
[ -d "$PROJECT_DIR/termux" ] || die "Project layout looks wrong: $PROJECT_DIR"

say "project directory: $PROJECT_DIR"

# ---------------------------------------------------- 1. Termux dependencies
need_pkg() {
    local pkg="$1" cmd="$2"
    if command -v "$cmd" >/dev/null 2>&1; then
        say "found $cmd"
        return 0
    fi
    if [ "$REPAIR" -eq 1 ]; then
        say "installing $pkg"
        pkg install -y "$pkg"
    else
        warn "missing $cmd (package: $pkg). Installing automatically."
        pkg install -y "$pkg"
    fi
}

say "checking native Termux dependencies"
need_pkg python python3
need_pkg termux-api termux-battery-status
need_pkg proot-distro proot-distro
need_pkg openssh sshd
need_pkg git git
need_pkg nodejs node

if ! command -v pm2 >/dev/null 2>&1; then
    say "installing pm2"
    npm install -g pm2
fi

# --------------------------------------------------------- 2. Ubuntu distro
say "detecting installed Ubuntu distributions"
ROOTFS_BASE="${PREFIX}/var/lib/proot-distro/installed-rootfs"
DISTROS=()
if [ -d "$ROOTFS_BASE" ]; then
    while IFS= read -r line; do
        [ -n "$line" ] && DISTROS+=("$line")
    done < <(ls -1 "$ROOTFS_BASE" 2>/dev/null || true)
fi

if [ "${#DISTROS[@]}" -eq 0 ]; then
    die "No proot-distro distribution found. Install one first:
    pkg install proot-distro && proot-distro install ubuntu"
fi

DISTRO="${HP_UNGU_DISTRO:-}"
if [ -z "$DISTRO" ]; then
    for candidate in "${DISTROS[@]}"; do
        if [[ "$candidate" == *ubuntu* ]]; then DISTRO="$candidate"; break; fi
    done
fi
[ -n "$DISTRO" ] || DISTRO="${DISTROS[0]}"
say "using distribution: $DISTRO"

# ------------------------------------------------------------ 3. .env setup
if [ ! -f "$ENV_FILE" ]; then
    say "creating .env from .env.example"
    cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
fi

# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

if [ -z "${TERMUX_BRIDGE_TOKEN:-}" ] || [[ "${TERMUX_BRIDGE_TOKEN}" == REPLACE_WITH* ]]; then
    say "generating a strong bridge token"
    NEW_TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
    python3 - "$ENV_FILE" "$NEW_TOKEN" <<'PY'
import sys, re
path, token = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
text = re.sub(r"^TERMUX_BRIDGE_TOKEN=.*$", f"TERMUX_BRIDGE_TOKEN={token}",
              text, flags=re.M)
open(path, "w", encoding="utf-8").write(text)
PY
    chmod 600 "$ENV_FILE"
    set -a; . "$ENV_FILE"; set +a
    say "token written to .env (never printed)"
fi

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [[ "${TELEGRAM_BOT_TOKEN}" == REPLACE_WITH* ]]; then
    printf 'Enter your Telegram bot token: '
    read -r BOT_TOKEN
    [ -n "$BOT_TOKEN" ] || die "A bot token is required."
    python3 - "$ENV_FILE" "$BOT_TOKEN" <<'PY'
import sys, re
path, token = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
text = re.sub(r"^TELEGRAM_BOT_TOKEN=.*$", f"TELEGRAM_BOT_TOKEN={token}",
              text, flags=re.M)
open(path, "w", encoding="utf-8").write(text)
PY
fi

if [ -z "${TELEGRAM_ADMIN_IDS:-}" ] || [[ "${TELEGRAM_ADMIN_IDS}" == "100000001" ]]; then
    printf 'Enter your numeric Telegram user ID: '
    read -r ADMIN_ID
    [ -n "$ADMIN_ID" ] || die "An admin ID is required."
    python3 - "$ENV_FILE" "$ADMIN_ID" <<'PY'
import sys, re
path, admin = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
text = re.sub(r"^TELEGRAM_ADMIN_IDS=.*$", f"TELEGRAM_ADMIN_IDS={admin}",
              text, flags=re.M)
open(path, "w", encoding="utf-8").write(text)
PY
fi

chmod 600 "$ENV_FILE" 2>/dev/null || true
say ".env configured"

# --------------------------------------------- 4. locate project in Ubuntu
say "probing for the project directory inside Ubuntu"
CANDIDATES=(
    "/root/$(basename "$PROJECT_DIR")"
    "/data/data/com.termux/files/home/$(basename "$PROJECT_DIR")"
    "/home/$(basename "$PROJECT_DIR")"
    "/opt/$(basename "$PROJECT_DIR")"
    "$PROJECT_DIR"
)

UBUNTU_PROJECT_DIR=""
for candidate in "${CANDIDATES[@]}"; do
    if proot-distro login "$DISTRO" -- /bin/bash -lc "[ -f '$candidate/requirements.txt' ]" >/dev/null 2>&1; then
        UBUNTU_PROJECT_DIR="$candidate"
        break
    fi
done

if [ -z "$UBUNTU_PROJECT_DIR" ]; then
    warn "Could not find the project inside Ubuntu by probing."
    warn "proot-distro may not bind Termux home by default on this device."
    warn "Falling back to copying the project into the Ubuntu rootfs."
    UBUNTU_PROJECT_DIR="/root/$(basename "$PROJECT_DIR")"
    proot-distro login "$DISTRO" -- /bin/bash -lc "mkdir -p '$UBUNTU_PROJECT_DIR'"
    tar -C "$PROJECT_DIR" -cf - \
        --exclude='.git' --exclude='data' --exclude='.venv' --exclude='__pycache__' . \
        | proot-distro login "$DISTRO" -- /bin/bash -lc "tar -C '$UBUNTU_PROJECT_DIR' -xf -"
fi

say "Ubuntu project directory: $UBUNTU_PROJECT_DIR"
cat > "$PATHS_FILE" <<EOF
# Generated by termux/install.sh — do not commit.
HP_UNGU_DISTRO=$DISTRO
UBUNTU_PROJECT_DIR=$UBUNTU_PROJECT_DIR
EOF
chmod 600 "$PATHS_FILE"

# --------------------------------------------- 5. Ubuntu dependency install
say "installing Ubuntu dependencies and building the virtual environment"
cp "$PROJECT_DIR/.env" "$PROJECT_DIR/.env"   # no-op guard, keeps shared tree
proot-distro login "$DISTRO" -- /bin/bash -lc \
    "cp -f '$UBUNTU_PROJECT_DIR/.env.example' '$UBUNTU_PROJECT_DIR/.env' 2>/dev/null || true"

# The shared .env lives in the Termux tree. If the project was copied instead,
# push the real .env into Ubuntu.
if [[ "$UBUNTU_PROJECT_DIR" != "$PROJECT_DIR" ]]; then
    TMP_ENV="$(mktemp)"
    cat "$ENV_FILE" > "$TMP_ENV"
    proot-distro login "$DISTRO" -- /bin/bash -lc "cat > '$UBUNTU_PROJECT_DIR/.env'" < "$TMP_ENV"
    rm -f "$TMP_ENV"
fi

proot-distro login "$DISTRO" -- /bin/bash -lc \
    "bash '$UBUNTU_PROJECT_DIR/termux/setup_ubuntu_inner.sh' '$UBUNTU_PROJECT_DIR'"

# ------------------------------------------------------- 6. PM2 processes
say "configuring PM2"
chmod +x "$PROJECT_DIR"/termux/*.sh "$PROJECT_DIR"/termux/boot/*.sh 2>/dev/null || true

export HP_UNGU_ENV="$ENV_FILE"

pm2 delete "$BRIDGE_NAME" >/dev/null 2>&1 || true
pm2 delete "$BOT_NAME" >/dev/null 2>&1 || true

pm2 start "$PROJECT_DIR/termux/launch_bridge.sh" \
    --name "$BRIDGE_NAME" --interpreter bash --cwd "$PROJECT_DIR"

pm2 start "$PROJECT_DIR/termux/launch_ubuntu_bot.sh" \
    --name "$BOT_NAME" --interpreter bash --cwd "$PROJECT_DIR"

pm2 save

# ------------------------------------------------------------ 7. boot hook
say "installing Termux:Boot script"
mkdir -p "$BOOT_DIR"
if [ -f "$BOOT_SCRIPT" ]; then
    say "existing boot script backed up to $BOOT_SCRIPT.bak"
    cp "$BOOT_SCRIPT" "$BOOT_SCRIPT.bak"
fi
cp "$PROJECT_DIR/termux/boot/start.sh" "$BOOT_SCRIPT"
chmod +x "$BOOT_SCRIPT"

# ------------------------------------------------------------ 8. health
say "waiting for services to settle"
sleep 6
bash "$PROJECT_DIR/termux/healthcheck.sh" || warn "health check reported problems"

cat <<EOF

$(say "installation complete")

Next steps:
  1. Open the Termux:Boot app once so Android registers the boot receiver.
  2. Open the Termux:API app and grant the permissions you need.
  3. Send /start to your bot on Telegram.
  4. Optional: termux-wake-lock

Useful commands:
  pm2 status
  pm2 logs $BOT_NAME
  pm2 logs $BRIDGE_NAME
  bash termux/healthcheck.sh
  bash termux/update.sh
EOF
