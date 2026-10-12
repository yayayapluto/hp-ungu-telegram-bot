"""Administrator-only Ubuntu file manager with strict path rules."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from ubuntu.config import Settings
from ubuntu.utils.security import PathSecurityError, is_denied, resolve_within_roots


@dataclass
class Entry:
    name: str
    path: str
    is_dir: bool
    size: int
    mtime: float
    symlink: bool
    denied: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "is_dir": self.is_dir,
            "size": self.size,
            "mtime": self.mtime,
            "symlink": self.symlink,
            "denied": self.denied,
        }


class FileManager:
    """Sandboxed filesystem operations."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.roots: Sequence[Path] = settings.allowed_file_roots

    # ------------------------------------------------------------- resolving
    def resolve(self, raw: str | Path) -> Path:
        return resolve_within_roots(raw, self.roots)

    def is_allowed(self, raw: str | Path) -> bool:
        try:
            self.resolve(raw)
            return True
        except PathSecurityError:
            return False

    def default_root(self) -> Path:
        return self.roots[0]

    # -------------------------------------------------------------- listing
    def list_dir(self, raw: str | Path, show_denied: bool = False) -> List[Entry]:
        directory = self.resolve(raw)
        if not directory.is_dir():
            raise NotADirectoryError(f"Not a directory: {directory}")

        entries: List[Entry] = []
        try:
            children = sorted(
                directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
            )
        except PermissionError as exc:
            raise PermissionError(f"Permission denied: {directory}") from exc

        for child in children:
            denied = is_denied(child)
            if denied and not show_denied:
                continue
            try:
                stat = child.stat()
                size = stat.st_size
                mtime = stat.st_mtime
                is_dir = child.is_dir()
            except OSError:
                size, mtime, is_dir = 0, 0.0, False
            entries.append(
                Entry(
                    name=child.name,
                    path=str(child),
                    is_dir=is_dir,
                    size=size,
                    mtime=mtime,
                    symlink=child.is_symlink(),
                    denied=denied,
                )
            )
        return entries

    def search(self, raw: str | Path, pattern: str, limit: int = 100) -> List[Entry]:
        directory = self.resolve(raw)
        results: List[Entry] = []
        for child in directory.rglob(pattern):
            if len(results) >= limit:
                break
            if is_denied(child):
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            results.append(
                Entry(
                    name=str(child.relative_to(directory)),
                    path=str(child),
                    is_dir=child.is_dir(),
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                    symlink=child.is_symlink(),
                    denied=False,
                )
            )
        return results

    # ----------------------------------------------------------------- read
    def stat(self, raw: str | Path) -> Dict[str, Any]:
        path = self.resolve(raw)
        stat = path.stat()
        return {
            "path": str(path),
            "name": path.name,
            "is_dir": path.is_dir(),
            "is_file": path.is_file(),
            "is_symlink": path.is_symlink(),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "mode": oct(stat.st_mode & 0o777),
        }

    def read_text(self, raw: str | Path, max_bytes: int | None = None) -> Dict[str, Any]:
        path = self.resolve(raw)
        if not path.is_file():
            raise IsADirectoryError(f"Not a regular file: {path}")
        limit = max_bytes or self.settings.max_read_bytes
        size = path.stat().st_size
        with path.open("rb") as handle:
            data = handle.read(limit)
        truncated = size > limit
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        return {
            "path": str(path),
            "size": size,
            "bytes_read": len(data),
            "truncated": truncated,
            "text": text,
        }

    def read_bytes(self, raw: str | Path, max_bytes: int | None = None) -> bytes:
        path = self.resolve(raw)
        if not path.is_file():
            raise IsADirectoryError(f"Not a regular file: {path}")
        limit = max_bytes or self.settings.max_upload_bytes
        size = path.stat().st_size
        if size > limit:
            raise ValueError(
                f"File is {size} bytes, larger than the {limit} byte download limit."
            )
        return path.read_bytes()

    # ---------------------------------------------------------------- write
    def write_bytes(self, raw: str | Path, data: bytes, overwrite: bool = False) -> Dict[str, Any]:
        if len(data) > self.settings.max_upload_bytes:
            raise ValueError(
                f"Upload is {len(data)} bytes, larger than the "
                f"{self.settings.max_upload_bytes} byte limit."
            )
        path = self.resolve(raw)
        if path.exists() and not overwrite:
            raise FileExistsError(f"{path} already exists.")
        if path.exists() and path.is_dir():
            raise IsADirectoryError(f"{path} is a directory.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"path": str(path), "size": len(data)}

    def mkdir(self, raw: str | Path, parents: bool = True) -> Dict[str, Any]:
        path = self.resolve(raw)
        path.mkdir(parents=parents, exist_ok=True)
        return {"path": str(path)}

    def rename(self, raw_src: str | Path, raw_dst: str | Path) -> Dict[str, Any]:
        src = self.resolve(raw_src)
        dst = self.resolve(raw_dst)
        if not src.exists():
            raise FileNotFoundError(f"{src} does not exist.")
        if dst.exists():
            raise FileExistsError(f"{dst} already exists.")
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        return {"src": str(src), "dst": str(dst)}

    def delete(self, raw: str | Path, recursive: bool = False) -> Dict[str, Any]:
        path = self.resolve(raw)
        if not path.exists():
            raise FileNotFoundError(f"{path} does not exist.")
        if path.is_dir():
            if not recursive:
                try:
                    next(path.iterdir())
                except StopIteration:
                    path.rmdir()
                    return {"path": str(path), "removed": "empty_directory"}
                except PermissionError as exc:
                    raise PermissionError(str(exc)) from exc
                raise OSError(f"{path} is not empty. Recursive delete is required.")
            shutil.rmtree(path)
            return {"path": str(path), "removed": "directory_tree"}
        path.unlink()
        return {"path": str(path), "removed": "file"}
