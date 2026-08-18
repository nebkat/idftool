"""Filesystem images for ESP-IDF data partitions.

Three filesystems are supported, one per ESP-IDF data partition subtype:

===========  ==========  ================================================
Name         Subtype     Implementation
===========  ==========  ================================================
``fatfs``    ``fat``     :mod:`pyfatfs`, plus ESP-IDF's wear levelling
``littlefs`` ``littlefs`` littlefs-python, as used by ``esp_littlefs``
``spiffs``   ``spiffs``  ESP-IDF's ``spiffsgen``, vendored, plus a reader
===========  ==========  ================================================

Each backend exposes the same four entry points — ``create``, ``mount``, ``detect``
and ``describe`` — which this module dispatches to after working out which
filesystem is meant: an explicit choice wins, then the partition's subtype, then
what the image itself looks like.
"""
from pathlib import Path
from typing import Optional

from esp_idf_defs.partitions import DATA_TYPE, SUBTYPES, PartitionDefinition

from idftool.fs import fatfs, littlefs, spiffs
from idftool.fs.common import FsEntry, FsError, SourceEntry, Volume, collect

__all__ = ['FsEntry', 'FsError', 'SourceEntry', 'Volume', 'FS_TYPES', 'create', 'mount',
           'detect', 'describe', 'extract', 'resolve_type', 'backend_options', 'collect']

BACKENDS = {module.NAME: module for module in (fatfs, littlefs, spiffs)}
FS_TYPES = tuple(BACKENDS)

#: Aliases accepted on the command line, including the partition subtype names.
ALIASES = {'fat': 'fatfs', 'fat12': 'fatfs', 'fat16': 'fatfs', 'lfs': 'littlefs', 'spiffs': 'spiffs'}

#: Partition subtype (within the data type) → filesystem, used when no type is given.
SUBTYPE_TYPES = {'fat': 'fatfs', 'littlefs': 'littlefs', 'spiffs': 'spiffs'}

#: CLI option name → backend keyword, per filesystem. Options left at None are dropped so
#: each backend falls back to its own ESP-IDF-matching default.
FS_OPTIONS = {
    'fatfs': {
        'fat_sector_size': 'sector_size',
        'fat_sectors_per_cluster': 'sectors_per_cluster',
        'fat_type': 'fat_type',
        'fat_wear_levelling': 'wear_levelling',
        'fat_volume_id': 'volume_id',
        'fat_device_id': 'device_id',
    },
    'littlefs': {
        'littlefs_block_size': 'block_size',
        'littlefs_name_max': 'name_max',
        'littlefs_disk_version': 'disk_version',
    },
    'spiffs': {
        'spiffs_page_size': 'page_size',
        'spiffs_block_size': 'block_size',
        'spiffs_obj_name_len': 'obj_name_len',
        'spiffs_meta_len': 'meta_len',
        'spiffs_use_magic': 'use_magic',
        'spiffs_use_magic_len': 'use_magic_len',
    },
}


def backend(fs_type: str):
    fs_type = ALIASES.get(fs_type, fs_type)
    if fs_type not in BACKENDS:
        raise FsError(f"Unknown filesystem '{fs_type}' (expected one of {', '.join(FS_TYPES)})")
    return BACKENDS[fs_type]


def backend_options(fs_type: str, params: dict) -> dict:
    """Pick the options that apply to `fs_type` out of a command's parameters."""
    mapping = FS_OPTIONS[ALIASES.get(fs_type, fs_type)]
    return {kwarg: params[option] for option, kwarg in mapping.items()
            if params.get(option) is not None}


def type_for_partition(partition: PartitionDefinition) -> Optional[str]:
    """The filesystem a partition's subtype implies, or None if it doesn't imply one."""
    if partition.type != DATA_TYPE:
        return None
    for name, fs_type in SUBTYPE_TYPES.items():
        if partition.subtype == SUBTYPES[DATA_TYPE][name]:
            return fs_type
    return None


def detect(image: bytes) -> Optional[str]:
    """Identify an image by its content, or None if nothing recognises it."""
    for name, module in BACKENDS.items():
        try:
            if module.detect(image):
                return name
        except Exception:
            continue
    return None


def resolve_type(explicit: Optional[str] = None, partition: Optional[PartitionDefinition] = None,
                 image: Optional[bytes] = None, what: str = 'the filesystem') -> str:
    """Settle on a filesystem: an explicit choice, else the partition subtype, else the image."""
    if explicit:
        backend(explicit)  # validate
        return ALIASES.get(explicit, explicit)

    if partition is not None:
        fs_type = type_for_partition(partition)
        if fs_type:
            return fs_type

    if image is not None:
        fs_type = detect(image)
        if fs_type:
            return fs_type

    if partition is not None:
        hint = (f"partition '{partition.name}' has subtype '{partition.subtype}', which is not "
                f"one of the filesystem subtypes (fat, littlefs, spiffs)")
    elif image is not None:
        hint = f"{what} does not look like any filesystem idftool knows"
    else:
        hint = "there is no --partition to take it from"
    raise FsError(f"Cannot tell which filesystem to use — {hint}. "
                  f"Pass --type ({'/'.join(FS_TYPES)}).")


def create(fs_type: str, source: str, size: int, **options) -> bytes:
    """Build an image of exactly `size` bytes from a host directory (or a single file)."""
    return backend(fs_type).create(collect(source), size, **options)


def mount(fs_type: str, image: bytes, **options) -> Volume:
    return backend(fs_type).mount(image, **options)


def describe(fs_type: str, size: int, **options) -> str:
    return backend(fs_type).describe(size, **options)


def extract(volume: Volume, destination: str) -> list[FsEntry]:
    """Write a volume's contents into `destination`, creating it if needed."""
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    entries = volume.entries()
    for entry in entries:
        # Filenames come off the device; refuse anything that would escape the destination.
        target = (root / entry.path).resolve()
        if not target.is_relative_to(root.resolve()):
            raise FsError(f"Refusing to extract '{entry.path}': it escapes {destination}")
        if entry.is_dir:
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(volume.read(entry))
    return entries


def format_listing(entries: list[FsEntry]) -> str:
    """Render a listing as a table in the same style as the partition table."""
    if not entries:
        return "(empty)"
    width = max(len(e.path) for e in entries) + 1
    lines = [f"| {'Path'.ljust(width)}| {'Size'.rjust(9)} |",
             f"|{'-' * (width + 1)}|{'-' * 11}|"]
    for entry in sorted(entries, key=lambda e: e.path):
        size = '<dir>' if entry.is_dir else f"{entry.size}"
        lines.append(f"| {entry.path.ljust(width)}| {size.rjust(9)} |")
    files = sum(1 for e in entries if not e.is_dir)
    total = sum(e.size for e in entries if not e.is_dir)
    dirs = len(entries) - files
    summary = f"{files} file{'' if files == 1 else 's'}, {total} bytes"
    if dirs:
        summary += f", {dirs} director{'y' if dirs == 1 else 'ies'}"
    lines.append(summary)
    return '\n'.join(lines)
