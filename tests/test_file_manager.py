from __future__ import annotations

from pathlib import Path

import pytest

from ubuntu.services.file_manager import FileManager
from ubuntu.utils.security import PathSecurityError


class FakeSettings:
    def __init__(self, roots, tmp_path: Path) -> None:
        self.allowed_file_roots = tuple(roots)
        self.max_read_bytes = 1024
        self.max_upload_bytes = 4096
        self.default_working_directory = tmp_path


@pytest.fixture()
def manager(tmp_roots):
    allowed, outside = tmp_roots
    settings = FakeSettings([allowed], allowed)
    return FileManager(settings), allowed, outside


def test_list_dir(manager):
    fm, allowed, _ = manager
    (allowed / "a.txt").write_text("hello", encoding="utf-8")
    (allowed / "sub").mkdir()
    names = [entry.name for entry in fm.list_dir(allowed)]
    assert "a.txt" in names
    assert "sub" in names


def test_list_dir_hides_sensitive_files(manager):
    fm, allowed, _ = manager
    (allowed / ".env").write_text("SECRET=1", encoding="utf-8")
    names = [entry.name for entry in fm.list_dir(allowed)]
    assert ".env" not in names


def test_read_text_respects_limit(manager):
    fm, allowed, _ = manager
    (allowed / "big.txt").write_text("x" * 5000, encoding="utf-8")
    payload = fm.read_text(allowed / "big.txt", max_bytes=100)
    assert payload["truncated"] is True
    assert len(payload["text"]) == 100


def test_read_outside_root_is_blocked(manager):
    fm, _allowed, outside = manager
    with pytest.raises(PathSecurityError):
        fm.read_text(outside / "secret.txt")


def test_write_and_overwrite(manager):
    fm, allowed, _ = manager
    target = allowed / "new.txt"
    info = fm.write_bytes(target, b"hello")
    assert info["size"] == 5
    with pytest.raises(FileExistsError):
        fm.write_bytes(target, b"again")
    fm.write_bytes(target, b"again", overwrite=True)
    assert target.read_bytes() == b"again"


def test_write_rejects_oversize(manager):
    fm, allowed, _ = manager
    with pytest.raises(ValueError):
        fm.write_bytes(allowed / "huge.bin", b"x" * 10_000)


def test_mkdir_and_delete(manager):
    fm, allowed, _ = manager
    fm.mkdir(allowed / "nested" / "dir")
    assert (allowed / "nested" / "dir").is_dir()
    fm.delete(allowed / "nested", recursive=True)
    assert not (allowed / "nested").exists()


def test_delete_non_empty_requires_recursive(manager):
    fm, allowed, _ = manager
    d = allowed / "d"
    d.mkdir()
    (d / "f").write_text("x", encoding="utf-8")
    with pytest.raises(OSError):
        fm.delete(d)
    fm.delete(d, recursive=True)
    assert not d.exists()


def test_rename(manager):
    fm, allowed, _ = manager
    src = allowed / "a.txt"
    src.write_text("x", encoding="utf-8")
    fm.rename(src, allowed / "b.txt")
    assert (allowed / "b.txt").exists()
    assert not src.exists()


def test_symlink_escape_is_blocked(manager):
    fm, allowed, outside = manager
    link = allowed / "escape"
    link.symlink_to(outside)
    with pytest.raises(PathSecurityError):
        fm.read_text(link / "secret.txt")


def test_download_size_limit(manager):
    fm, allowed, _ = manager
    big = allowed / "big.bin"
    big.write_bytes(b"x" * 10_000)
    with pytest.raises(ValueError):
        fm.read_bytes(big)
