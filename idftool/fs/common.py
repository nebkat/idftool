"""Types shared by the filesystem backends.

Kept out of ``idftool.fs`` itself so the backends can import them without an import
cycle: ``idftool.fs`` imports every backend to build its registry.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


class FsError(RuntimeError):
    """A filesystem image could not be built, parsed, or fitted to a partition."""


@dataclass
class SourceEntry:
    """One file or directory on the host, staged for inclusion in an image.

    `path` is the slash-separated location inside the image, without a leading slash.
    File contents are read lazily so a listing never pulls the whole tree into memory.
    """
    path: str
    is_dir: bool
    size: int
    mtime: float
    # Where the entry came from on the host. Backends that hand the work to a vendored
    # generator (SPIFFS) need a real path rather than the bytes.
    host_path: Optional[Path] = None

    def read(self) -> bytes:
        return b'' if self.is_dir or self.host_path is None else self.host_path.read_bytes()


@dataclass
class FsEntry:
    """One file or directory found inside an image."""
    path: str
    is_dir: bool
    size: int
    # Backend-private handle (a directory entry, an open path, …) used by Volume.read().
    handle: Any = field(default=None, repr=False)


class Volume:
    """Read-only view of a mounted image.

    Backends return one from their ``open()`` so a listing or an extraction parses the
    image once, instead of re-mounting it for every file.
    """

    def entries(self) -> list[FsEntry]:
        raise NotImplementedError

    def read(self, entry: FsEntry) -> bytes:
        raise NotImplementedError

    def close(self):
        pass

    def __enter__(self) -> 'Volume':
        return self

    def __exit__(self, *exc):
        self.close()


def collect(source: str) -> list[SourceEntry]:
    """Walk a directory (or take a single file) into a sorted list of `SourceEntry`.

    Directories always precede their contents, so a backend can create them in order.
    Symlinks are followed, matching ESP-IDF's image generators.
    """
    root = Path(source)
    if root.is_file():
        stat = root.stat()
        return [SourceEntry(root.name, False, stat.st_size, stat.st_mtime, root)]
    if not root.is_dir():
        raise FsError(f"Source '{source}' is neither a file nor a directory")

    entries: list[SourceEntry] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        dirnames.sort()
        filenames.sort()
        base = Path(dirpath)
        for name in dirnames:
            path = base / name
            entries.append(SourceEntry(
                path.relative_to(root).as_posix(), True, 0, path.stat().st_mtime, path))
        for name in filenames:
            path = base / name
            stat = path.stat()
            entries.append(SourceEntry(
                path.relative_to(root).as_posix(), False, stat.st_size, stat.st_mtime, path))
    entries.sort(key=lambda e: (e.path.count('/'), e.path))
    return entries
