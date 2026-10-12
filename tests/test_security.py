from __future__ import annotations

from pathlib import Path

import pytest

from ubuntu.utils.security import (
    PathSecurityError,
    is_admin,
    is_denied,
    resolve_within_roots,
)


def test_is_admin_accepts_known_ids():
    admins = frozenset({1, 2, 3})
    assert is_admin(2, admins)
    assert not is_admin(99, admins)
    assert not is_admin(None, admins)


def test_is_admin_handles_garbage():
    assert not is_admin("not-a-number", frozenset({1}))  # type: ignore[arg-type]


def test_resolve_within_roots_allows(tmp_roots):
    allowed, _ = tmp_roots
    target = allowed / "file.txt"
    target.write_text("x", encoding="utf-8")
    assert resolve_within_roots(target, [allowed]) == target.resolve()


def test_resolve_within_roots_rejects_outside(tmp_roots):
    allowed, outside = tmp_roots
    with pytest.raises(PathSecurityError):
        resolve_within_roots(outside / "secret.txt", [allowed])


def test_resolve_rejects_relative_paths(tmp_roots):
    allowed, _ = tmp_roots
    with pytest.raises(PathSecurityError):
        resolve_within_roots("relative/path", [allowed])


def test_resolve_rejects_symlink_escape(tmp_roots):
    allowed, outside = tmp_roots
    link = allowed / "escape"
    link.symlink_to(outside)
    with pytest.raises(PathSecurityError):
        resolve_within_roots(link / "secret.txt", [allowed])


def test_deny_list_blocks_env_files(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TOKEN=x", encoding="utf-8")
    assert is_denied(env_file)


def test_deny_list_blocks_ssh_directory(tmp_path):
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    assert is_denied(ssh_dir / "id_rsa")


def test_resolve_blocks_sensitive_file_even_inside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    env_file = root / ".env"
    env_file.write_text("x", encoding="utf-8")
    with pytest.raises(PathSecurityError):
        resolve_within_roots(env_file, [root])


def test_empty_path_is_rejected(tmp_roots):
    allowed, _ = tmp_roots
    with pytest.raises(PathSecurityError):
        resolve_within_roots("", [allowed])
