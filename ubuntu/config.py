"""Configuration loading and validation for the hp-ungu Ubuntu application."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Tuple

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

load_dotenv(ENV_PATH, override=False)


class ConfigError(RuntimeError):
    """Raised when the configuration is missing or invalid."""


def _raw(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _require(name: str) -> str:
    value = _raw(name)
    if value is None:
        raise ConfigError(f"Required setting {name} is missing. Add it to {ENV_PATH}.")
    return value


def _int(name: str, default: int, minimum: int | None = None,
         maximum: int | None = None) -> int:
    raw = _raw(name)
    if raw is None:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError as exc:
            raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum} (got {value})")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name} must be <= {maximum} (got {value})")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = _raw(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _admin_ids(name: str) -> FrozenSet[int]:
    raw = _require(name)
    ids: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.add(int(chunk))
        except ValueError as exc:
            raise ConfigError(f"{name} contains a non-numeric id: {chunk!r}") from exc
    if not ids:
        raise ConfigError(f"{name} must contain at least one Telegram user id.")
    return frozenset(ids)


def _roots(name: str, default: str) -> Tuple[Path, ...]:
    raw = _raw(name, default) or default
    roots: list[Path] = []
    for chunk in raw.split(":"):
        chunk = chunk.strip()
        if not chunk:
            continue
        roots.append(Path(chunk).expanduser())
    if not roots:
        raise ConfigError(f"{name} must contain at least one absolute path.")
    for root in roots:
        if not root.is_absolute():
            raise ConfigError(f"{name} entry must be absolute: {root}")
    return tuple(roots)


@dataclass(frozen=True)
class Settings:
    """Validated runtime settings."""

    telegram_bot_token: str
    telegram_admin_ids: FrozenSet[int]
    default_working_directory: Path
    command_timeout_seconds: int
    max_concurrent_jobs: int
    max_output_bytes: int
    max_read_bytes: int
    max_upload_bytes: int
    progress_interval_seconds: int
    enable_unrestricted_shell: bool
    allowed_file_roots: Tuple[Path, ...]
    termux_bridge_host: str
    termux_bridge_port: int
    termux_bridge_token: str
    termux_bridge_timeout_seconds: int
    ssh_hostname: str
    ssh_port: int
    ssh_username: str
    tailscale_ip: str
    log_level: str
    project_root: Path
    data_dir: Path
    log_file: Path

    @property
    def bridge_base_url(self) -> str:
        return f"http://{self.termux_bridge_host}:{self.termux_bridge_port}"


def load_settings() -> Settings:
    """Load, validate and return the application settings."""
    token = _require("TELEGRAM_BOT_TOKEN")
    if ":" not in token:
        raise ConfigError("TELEGRAM_BOT_TOKEN does not look like a BotFather token.")

    admin_ids = _admin_ids("TELEGRAM_ADMIN_IDS")

    default_cwd = Path(_raw("DEFAULT_WORKING_DIRECTORY", "/root") or "/root").expanduser()
    if not default_cwd.is_absolute():
        raise ConfigError("DEFAULT_WORKING_DIRECTORY must be absolute.")
    if not default_cwd.exists():
        raise ConfigError(
            f"DEFAULT_WORKING_DIRECTORY does not exist inside Ubuntu: {default_cwd}. "
            "Check the value in .env (it is an Ubuntu path, not a Termux path)."
        )

    data_dir = Path(_raw("HP_UNGU_DATA_DIR", str(PROJECT_ROOT / "data")) or "").expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        telegram_bot_token=token,
        telegram_admin_ids=admin_ids,
        default_working_directory=default_cwd,
        command_timeout_seconds=_int("COMMAND_TIMEOUT_SECONDS", 120, minimum=1, maximum=86400),
        max_concurrent_jobs=_int("MAX_CONCURRENT_JOBS", 3, minimum=1, maximum=32),
        max_output_bytes=_int("MAX_OUTPUT_BYTES", 262144, minimum=4096),
        max_read_bytes=_int("MAX_READ_BYTES", 1048576, minimum=1024),
        max_upload_bytes=_int("MAX_UPLOAD_BYTES", 20971520, minimum=1024),
        progress_interval_seconds=_int("PROGRESS_INTERVAL_SECONDS", 5, minimum=2, maximum=60),
        enable_unrestricted_shell=_bool("ENABLE_UNRESTRICTED_SHELL", False),
        allowed_file_roots=_roots("ALLOWED_FILE_ROOTS", "/root:/tmp"),
        termux_bridge_host=_raw("TERMUX_BRIDGE_HOST", "127.0.0.1") or "127.0.0.1",
        termux_bridge_port=_int("TERMUX_BRIDGE_PORT", 8765, minimum=1, maximum=65535),
        termux_bridge_token=_require("TERMUX_BRIDGE_TOKEN"),
        termux_bridge_timeout_seconds=_int("TERMUX_BRIDGE_TIMEOUT_SECONDS", 60, minimum=1),
        ssh_hostname=_raw("SSH_HOSTNAME", "") or "",
        ssh_port=_int("SSH_PORT", 8022, minimum=1, maximum=65535),
        ssh_username=_raw("SSH_USERNAME", "") or "",
        tailscale_ip=_raw("TAILSCALE_IP", "") or "",
        log_level=(_raw("LOG_LEVEL", "INFO") or "INFO").upper(),
        project_root=PROJECT_ROOT,
        data_dir=data_dir,
        log_file=log_dir / "bot.log",
    )
    return settings
