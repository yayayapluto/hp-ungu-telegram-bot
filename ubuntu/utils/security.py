"""Authorization and path-safety helpers."""
from __future__ import annotations

from pathlib import Path
from typing import FrozenSet, Iterable, Sequence

DENY_NAMES: FrozenSet[str] = frozenset(
    {
        ".env",
        ".netrc",
        "id_rsa",
        "id_rsa.pub",
        "id_ed25519",
        "id_ed25519.pub",
        "id_ecdsa",
        "id_dsa",
        "authorized_keys",
        "shadow",
        "passwd",
    }
)
DENY_SUFFIXES: Sequence[str] = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks")
DENY_DIR_PARTS: FrozenSet[str] = frozenset({".ssh", ".gnupg", ".aws", ".config/hp-ungu"})


class PathSecurityError(PermissionError):
    """Raised when a path violates the configured safety rules."""


def is_admin(user_id: int | None, admin_ids: FrozenSet[int]) -> bool:
    """Return True when *user_id* is an authorized administrator."""
    if user_id is None:
        return False
    try:
        return int(user_id) in admin_ids
    except (TypeError, ValueError):
        return False


def is_denied(path: Path) -> bool:
    """Return True when *path* matches the sensitive-file deny list."""
    name = path.name
    if name in DENY_NAMES:
        return True
    if name.startswith(".env"):
        return True
    lower = name.lower()
    if any(lower.endswith(suffix) for suffix in DENY_SUFFIXES):
        return True
    text = str(path)
    if any(part in text for part in DENY_DIR_PARTS):
        return True
    return False


def resolve_within_roots(raw: str | Path, roots: Iterable[Path]) -> Path:
    """Resolve *raw* and guarantee it stays inside one of *roots*.

    Follows symlinks before checking, so a symlink pointing outside the
    allowed roots is rejected.
    """
    if raw is None or str(raw).strip() == "":
        raise PathSecurityError("Empty path is not allowed.")

    candidate = Path(str(raw)).expanduser()
    if not candidate.is_absolute():
        raise PathSecurityError("Only absolute paths are allowed.")

    try:
        resolved = candidate.resolve()
    except OSError as exc:  # pragma: no cover - depends on the filesystem
        raise PathSecurityError(f"Cannot resolve path: {exc}") from exc

    root_list = [Path(r).expanduser().resolve() for r in roots]
    for root in root_list:
        if resolved == root or root in resolved.parents:
            if is_denied(resolved):
                raise PathSecurityError(
                    f"Access to {resolved.name!r} is blocked by the sensitive-file policy."
                )
            return resolved

    allowed = ", ".join(str(r) for r in root_list)
    raise PathSecurityError(
        f"{resolved} is outside the allowed roots ({allowed})."
    )


def is_within(path: Path, roots: Iterable[Path]) -> bool:
    """Non-raising variant of :func:`resolve_within_roots`."""
    try:
        resolve_within_roots(path, roots)
        return True
    except PathSecurityError:
        return False
