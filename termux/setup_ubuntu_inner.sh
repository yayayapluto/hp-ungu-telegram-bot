#!/bin/bash
# Runs INSIDE Ubuntu. Installs dependencies, builds the venv, runs tests.
set -euo pipefail

PROJECT_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$PROJECT_DIR"

echo "[hp-ungu] Ubuntu setup in $PROJECT_DIR"
echo "[hp-ungu] $(. /etc/os-release && echo "$PRETTY_NAME")"

export DEBIAN_FRONTEND=noninteractive

if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip ca-certificates curl procps
else
    echo "[hp-ungu] apt-get not found; assuming dependencies are already present." >&2
fi

if [ ! -d ".venv" ]; then
    echo "[hp-ungu] creating virtual environment"
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
. .venv/bin/activate

python -m pip install --upgrade pip wheel
python -m pip install -r requirements.txt

mkdir -p data/logs

echo "[hp-ungu] python: $(python --version)"
echo "[hp-ungu] installed packages:"
python -m pip list --format=freeze | sed 's/^/    /'

echo "[hp-ungu] running the offline test suite"
if python -m pytest -q; then
    echo "[hp-ungu] tests passed"
else
    echo "[hp-ungu] WARNING: some tests failed. Review the output above." >&2
fi

echo "[hp-ungu] Ubuntu setup complete."
