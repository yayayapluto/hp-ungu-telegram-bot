"""Shared pytest fixtures. Android-specific behaviour is mocked here."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture()
def tmp_roots(tmp_path: Path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("nope", encoding="utf-8")
    return allowed, outside


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Prevent tests from reading the developer's real .env."""
    for key in list(os.environ):
        if key.startswith(("TELEGRAM_", "TERMUX_", "SSH_", "TAILSCALE_", "HP_UNGU_")):
            monkeypatch.delenv(key, raising=False)
    yield
