#!/data/data/com.termux/files/usr/bin/bash
# Termux:Boot entry point for hp-ungu.
# Installed to ~/.termux/boot/10-hp-ungu.sh by termux/install.sh.
set -uo pipefail

LOG_DIR="$HOME/.hp-ungu"
LOG_FILE="$LOG_DIR/boot.log"
mkdir -p "$LOG_DIR"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"; }

log "=== boot start ==="

# Hold a wake lock so Android is less likely to freeze us mid-startup.
if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock && log "wake lock acquired"
else
    log "termux-wake-lock unavailable"
fi

# Wait for networking, but do not block forever.
for attempt in $(seq 1 30); do
    if ping -c 1 -W 1 1.1.1.1 >/dev/null 2>&1; then
        log "network reachable after ${attempt}s"
        break
    fi
    sleep 1
done

PROJECT_DIR="$HOME/hp-ungu-telegram-bot"
if [ ! -d "$PROJECT_DIR" ]; then
    log "project directory missing: $PROJECT_DIR"
    exit 1
fi
cd "$PROJECT_DIR" || exit 1

export HP_UNGU_ENV="$PROJECT_DIR/.env"

if ! command -v pm2 >/dev/null 2>&1; then
    log "pm2 not found in PATH"
    exit 1
fi

# Restore the saved process list, then make sure our two processes exist.
if [ -f "$HOME/.pm2/dump.pm2" ]; then
    pm2 resurrect >> "$LOG_FILE" 2>&1 && log "pm2 resurrected"
else
    log "no pm2 dump found; starting services directly"
fi

start_if_missing() {
    local name="$1" script="$2"
    if pm2 describe "$name" >/dev/null 2>&1; then
        pm2 restart "$name" --update-env >> "$LOG_FILE" 2>&1 && log "restarted $name"
    else
        pm2 start "$script" --name "$name" --interpreter bash --cwd "$PROJECT_DIR" \
            >> "$LOG_FILE" 2>&1 && log "started $name"
    fi
}

start_if_missing "hp-ungu-bridge" "$PROJECT_DIR/termux/launch_bridge.sh"
start_if_missing "hp-ungu-ubuntu-bot" "$PROJECT_DIR/termux/launch_ubuntu_bot.sh"

pm2 save >> "$LOG_FILE" 2>&1

sleep 20
if bash "$PROJECT_DIR/termux/healthcheck.sh" >> "$LOG_FILE" 2>&1; then
    log "health check passed"
else
    log "health check reported problems"
fi

log "=== boot complete ==="
