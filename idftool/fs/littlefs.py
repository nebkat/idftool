"""littlefs images for ESP-IDF's ``littlefs`` partitions.

Built with :mod:`littlefs` (littlefs-python) — the same library the ``esp_littlefs``
component spins up in a venv to generate images at build time, so the defaults here
mirror the ones its ``littlefs_create_partition_image()`` passes: 4 KiB blocks and a
name limit taken from ``CONFIG_LITTLEFS_OBJ_NAME_LEN``.

``name_max``, ``file_max`` and ``attr_max`` are recorded in the superblock and are
checked against the mounting device's configuration, so a mismatch here is a mount
failure on the device rather than a corrupt image.
"""
import struct
from typing import Optional

from littlefs import LittleFS
from littlefs.context import UserContext
from littlefs.errors import LittleFSError

from idftool.fs.common import FsEntry, FsError, SourceEntry, Volume

NAME = 'littlefs'

BLOCK_SIZE = 4096
# CONFIG_LITTLEFS_OBJ_NAME_LEN's default. littlefs itself defaults to 255, but a device
# configured for 64 refuses to mount an image whose superblock asks for more than that.
NAME_MAX = 64

MAGIC = b'littlefs'
# Offsets within a metadata block: revision(4), name tag(4), "littlefs"(8), struct tag(4),
# then the superblock itself — version, block_size, block_count, name_max, file_max, attr_max.
MAGIC_OFFSET = 8
SUPERBLOCK_OFFSET = 20
# Block sizes worth probing when the superblock in block 0 is the stale half of the pair.
CANDIDATE_BLOCK_SIZES = (4096, 8192, 512, 256, 128, 1024, 2048, 16384, 32768)


def _superblock(image: bytes) -> Optional[tuple[int, int, int]]:
    """Return (block_size, block_count, name_max) from whichever half of the pair has it.

    The superblock lives in a metadata *pair* — blocks 0 and 1 — and littlefs alternates
    between them on every update, so on a device-written image block 0 may be the older
    copy. Both copies carry the same geometry, so either will do; block 1 is only reachable
    by guessing the block size, which is what we are trying to find in the first place.
    """
    for offset in (0, *CANDIDATE_BLOCK_SIZES):
        if image[offset + MAGIC_OFFSET:offset + MAGIC_OFFSET + len(MAGIC)] != MAGIC:
            continue
        base = offset + SUPERBLOCK_OFFSET
        if len(image) < base + 24:
            continue
        _, block_size, block_count, name_max, _, _ = struct.unpack_from('<6I', image, base)
        if offset and offset != block_size:
            continue  # found the magic at a candidate offset that isn't this image's block size
        if block_size and block_size <= len(image):
            return block_size, block_count, name_max
    return None


def _fs(size: int, block_size: int, name_max: int, buffer: Optional[bytearray] = None,
        disk_version: int = 0, mount: bool = True) -> LittleFS:
    if size % block_size != 0:
        raise FsError(f"Filesystem size {size:#x} is not a multiple of the block size {block_size:#x}")
    context = UserContext(buffer=buffer) if buffer is not None else UserContext(buffsize=size)
    return LittleFS(context=context, block_size=block_size, block_count=size // block_size,
                    name_max=name_max, disk_version=disk_version, mount=mount)


def create(sources: list[SourceEntry], size: int, *, block_size: int = BLOCK_SIZE,
           name_max: int = NAME_MAX, disk_version: int = 0, **_) -> bytes:
    """Build a littlefs image of exactly `size` bytes holding `sources`."""
    fs = _fs(size, block_size, name_max, disk_version=disk_version)
    try:
        for source in sources:
            if source.is_dir:
                fs.mkdir(source.path)
            else:
                with fs.open(source.path, 'wb') as f:
                    f.write(source.read())
    except LittleFSError as e:
        if e.code == LittleFSError.Error.LFS_ERR_NOSPC:
            raise FsError(f"Sources do not fit in a {size:#x}-byte littlefs image") from e
        raise FsError(f"Failed to build littlefs image: {e}") from e
    return bytes(fs.context.buffer)


class LittleFsVolume(Volume):
    def __init__(self, image: bytes, block_size: Optional[int] = None,
                 name_max: Optional[int] = None):
        superblock = _superblock(image)
        if superblock is None and block_size is None:
            raise FsError("Not a littlefs image (no superblock found)")
        found_block_size, _, found_name_max = superblock or (block_size, 0, 0)

        block_size = block_size or found_block_size
        # littlefs refuses to mount when the image asks for a longer name than we allow, so
        # take the image's own limit unless the caller insists on one.
        name_max = name_max or found_name_max or NAME_MAX
        try:
            self.fs = _fs(len(image) - len(image) % block_size, block_size, name_max,
                          buffer=bytearray(image))
        except LittleFSError as e:
            raise FsError(f"Not a readable littlefs image: {e}") from e

    def entries(self) -> list[FsEntry]:
        found: list[FsEntry] = []
        for root, dirs, files in self.fs.walk('/'):
            prefix = root.strip('/')
            prefix = f"{prefix}/" if prefix else ''
            for name in sorted(dirs):
                found.append(FsEntry(path=f"{prefix}{name}", is_dir=True, size=0))
            for name in sorted(files):
                path = f"{prefix}{name}"
                found.append(FsEntry(path=path, is_dir=False, size=self.fs.stat(path).size, handle=path))
        return found

    def read(self, entry: FsEntry) -> bytes:
        with self.fs.open(entry.handle, 'rb') as f:
            return f.read()


def mount(image: bytes, *, block_size: Optional[int] = None, name_max: Optional[int] = None,
          **_) -> LittleFsVolume:
    return LittleFsVolume(image, block_size=block_size, name_max=name_max)


def detect(image: bytes) -> bool:
    return _superblock(image) is not None


def describe(size: int, *, block_size: int = BLOCK_SIZE, name_max: int = NAME_MAX, **_) -> str:
    return f"littlefs, {size // block_size} blocks of {block_size:#x} bytes, name_max {name_max}"
