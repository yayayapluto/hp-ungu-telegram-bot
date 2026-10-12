#!/data/data/com.termux/files/usr/bin/bash
# Launch the native Termux bridge under PM2.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

export HP_UNGU_ENV="${HP_UNGU_ENV:-$PROJECT_DIR/.env}"
export PYTHONUNBUFFERED=1

exec python3 "$PROJECT_DIR/termux/bridge_server.py"
