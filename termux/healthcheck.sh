#!/data/data/com.termux/files/usr/bin/bash
# Verify the bridge, the Ubuntu bot and PM2.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_DIR/.env"
PATHS_FILE="$SCRIPT_DIR/paths.env"

FAILURES=0
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[1;31m✗\033[0m %s\n' "$*"; FAILURES=$((FAILURES + 1)); }
info() { printf '  \033[1;34m·\033[0m %s\n' "$*"; }

printf '\033[1;36m[hp-ungu] health check\033[0m\n'

if [ ! -f "$ENV_FILE" ]; then
    bad ".env not found at $ENV_FILE"
    exit 1
fi
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

HOST="${TERMUX_BRIDGE_HOST:-127.0.0.1}"
PORT="${TERMUX_BRIDGE_PORT:-8765}"
TOKEN="${TERMUX_BRIDGE_TOKEN:-}"

# ------------------------------------------------------------- bridge HTTP
printf '\nBridge (http://%s:%s)\n' "$HOST" "$PORT"
if command -v curl >/dev/null 2>&1; then
    RESPONSE="$(curl -fsS --max-time 8 \
        -H "X-Auth-Token: $TOKEN" \
        "http://$HOST:$PORT/health" 2>/dev/null || true)"
    if [ -n "$RESPONSE" ]; then
        ok "bridge responded: $(printf '%s' "$RESPONSE" | head -c 200)"
    else
        bad "bridge did not respond. Try: pm2 logs hp-ungu-bridge"
    fi

    AUTH_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 \
        "http://$HOST:$PORT/health" 2>/dev/null || echo 000)"
    if [ "$AUTH_CODE" = "401" ]; then
        ok "token enforcement works (401 without token)"
    else
        bad "expected HTTP 401 without a token, got $AUTH_CODE"
    fi
else
    info "curl is not installed; skipping HTTP checks (pkg install curl)"
fi

# ------------------------------------------------------------------- PM2
printf '\nPM2\n'
if command -v pm2 >/dev/null 2>&1; then
    if pm2 jlist 2>/dev/null | grep -q '"name":"hp-ungu-bridge"'; then
        ok "hp-ungu-bridge is known to PM2"
    else
        bad "hp-ungu-bridge is not registered with PM2"
    fi
    if pm2 jlist 2>/dev/null | grep -q '"name":"hp-ungu-ubuntu-bot"'; then
        ok "hp-ungu-ubuntu-bot is known to PM2"
    else
        bad "hp-ungu-ubuntu-bot is not registered with PM2"
    fi
    pm2 jlist 2>/dev/null \
        | python3 -c 'import json,sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for p in data:
    env = p.get("pm2_env", {})
    print(f"  - {env.get(\"name\")}: {env.get(\"status\")} (restarts={env.get(\"restart_time\")}, exit={env.get(\"exit_code\")})")' 2>/dev/null
else
    bad "pm2 is not installed"
fi

# -------------------------------------------------- Ubuntu -> bridge loopback
printf '\nUbuntu → bridge reachability\n'
if [ -f "$PATHS_FILE" ]; then
    # shellcheck disable=SC1090
    set -a; . "$PATHS_FILE"; set +a
    DISTRO="${HP_UNGU_DISTRO:-ubuntu}"
    if proot-distro login "$DISTRO" -- /bin/bash -lc \
        "exec 3<>/dev/tcp/$HOST/$PORT" >/dev/null 2>&1; then
        ok "Ubuntu can open a TCP connection to $HOST:$PORT"
    else
        bad "Ubuntu cannot reach $HOST:$PORT over loopback"
        info "Fallback: run the bridge on a UNIX socket and point"
        info "TERMUX_BRIDGE_HOST at the socket path, or use"
        info "proot-distro login --bind to expose the socket file."
    fi
else
    info "termux/paths.env missing; run bash termux/install.sh --repair"
fi

# ------------------------------------------------------------------- SSH
printf '\nSSH\n'
SSH_PORT_VALUE="${SSH_PORT:-8022}"
if (exec 3<>"/dev/tcp/127.0.0.1/$SSH_PORT_VALUE") >/dev/null 2>&1; then
    ok "127.0.0.1:$SSH_PORT_VALUE accepts TCP connections"
else
    bad "127.0.0.1:$SSH_PORT_VALUE refused. Is sshd running? pm2 status"
fi
if pgrep -f sshd >/dev/null 2>&1; then
    ok "sshd process detected"
else
    info "pgrep did not find sshd (this may be a pgrep availability issue)"
fi

# ------------------------------------------------------------------ summary
printf '\n'
if [ "$FAILURES" -eq 0 ]; then
    printf '\033[1;32m[hp-ungu] all checks passed\033[0m\n'
    exit 0
fi
printf '\033[1;31m[hp-ungu] %d check(s) failed\033[0m\n' "$FAILURES"
exit 1
