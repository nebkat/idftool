"""FAT12/FAT16 images for ESP-IDF's ``fatfs`` partitions.

The on-flash geometry follows ESP-IDF's ``fatfsgen.py``: 4 KiB sectors (matching
``CONFIG_WL_SECTOR_SIZE``), one sector per cluster, two FATs and a 512-entry root
directory. The filesystem itself is built with :mod:`pyfatfs`, using its low-level
``PyFat`` API — the ``PyFatFS`` layer on top of it is deliberately avoided because
it drags in PyFilesystem2, which no longer imports on modern setuptools.

Everything here works on a bare FAT image. Partitions that are mounted read-write
in SPI flash wrap that image in a wear levelling container; see :mod:`idftool.fs.wl`.
"""
import errno
import os
import struct
from copy import copy
from io import BytesIO
from typing import Optional

from pyfatfs import FAT_OEM_ENCODING, PyFATException
from pyfatfs.DosDateTime import DosDateTime
from pyfatfs.EightDotThree import EightDotThree
from pyfatfs.FATDirectoryEntry import FATDirectoryEntry, make_lfn_entry
from pyfatfs.PyFat import PyFat

from idftool.fs import wl
from idftool.fs.common import FsEntry, FsError, SourceEntry, Volume

NAME = 'fatfs'

SECTOR_SIZE = 0x1000
SECTORS_PER_CLUSTER = 1
RESERVED_SECTORS = 1
FAT_COUNT = 2
ROOT_ENTRIES = 512
MEDIA_TYPE = 0xF8
OEM_NAME = 'MSDOS5.0'
VOLUME_LABEL = 'Espressif'

# Cluster counts at which FatFs (and every other implementation) switches FAT width.
# The type is derived from the count alone, so the FAT we write has to agree with it.
FAT12_MAX_CLUSTERS = 4084
FAT16_MAX_CLUSTERS = 65524

ERASED = b'\xff'


class _Image(BytesIO):
    """BytesIO that survives being closed, so PyFat.close() can flush without losing the image."""

    def close(self):
        pass

    def release(self) -> bytes:
        data = self.getvalue()
        super().close()
        return data


def _fat_bytes(clusters: int, bits: int) -> int:
    entries = clusters + 2  # entries 0 and 1 are reserved markers
    return (entries * 3 + 1) // 2 if bits == 12 else entries * (bits // 8)


def _geometry(size: int, sector_size: int, sectors_per_cluster: int,
              fat_count: int, root_entries: int, fat_type: Optional[int]) -> dict:
    """Work out the BPB geometry, picking the FAT width the cluster count implies."""
    total_sectors = size // sector_size
    root_dir_sectors = (root_entries * 32 + sector_size - 1) // sector_size

    def clusters_for(fat_size: int) -> int:
        data_sectors = total_sectors - (RESERVED_SECTORS + fat_count * fat_size + root_dir_sectors)
        if data_sectors <= 0:
            raise FsError(f"Partition of {size:#x} bytes is too small for a FAT filesystem")
        return data_sectors // sectors_per_cluster

    def solve(bits: int) -> tuple[int, int]:
        """Smallest FAT that can index the data area left over once the FAT is accounted for.

        Growing the FAT shrinks the data area, which shrinks the FAT needed again, so this
        walks up to a fixed point.
        """
        fat_size = 1
        while True:
            clusters = clusters_for(fat_size)
            needed = (_fat_bytes(clusters, bits) + sector_size - 1) // sector_size
            if needed <= fat_size:
                return fat_size, clusters
            fat_size = needed

    def fit(bits: int) -> tuple[int, int]:
        """As solve(), then pad the FAT until the cluster count is inside the width's range."""
        fat_size, clusters = solve(bits)
        limit = FAT12_MAX_CLUSTERS if bits == 12 else FAT16_MAX_CLUSTERS
        while clusters > limit:
            fat_size += 1
            clusters = clusters_for(fat_size)
        return fat_size, clusters

    ranges = {12: (1, FAT12_MAX_CLUSTERS), 16: (FAT12_MAX_CLUSTERS + 1, FAT16_MAX_CLUSTERS)}

    if fat_type is not None:
        if fat_type not in ranges:
            raise FsError(f"Unsupported FAT type {fat_type} (idftool creates FAT12 or FAT16 images)")
        bits = fat_type
        fat_size, clusters = fit(bits)
        if clusters < ranges[bits][0]:
            raise FsError(
                f"A {size:#x}-byte volume only holds {clusters} clusters, which every FAT "
                f"implementation reads as FAT12, not the requested FAT{bits} — use a smaller "
                f"--fat-sectors-per-cluster to get more clusters")
    else:
        # The FAT width is derived from the cluster count, never declared, so pick the width
        # whose range the natural count already falls in rather than padding the FAT to force it.
        for bits, (low, high) in ranges.items():
            fat_size, clusters = solve(bits)
            if low <= clusters <= high:
                break
        else:
            if clusters > FAT16_MAX_CLUSTERS:
                largest = FAT16_MAX_CLUSTERS * sectors_per_cluster * sector_size
                raise FsError(
                    f"A {size:#x}-byte volume needs {clusters} clusters of "
                    f"{sectors_per_cluster * sector_size:#x} bytes, more than the {FAT16_MAX_CLUSTERS} "
                    f"FAT16 allows (idftool does not create FAT32) — raise "
                    f"--fat-sectors-per-cluster; at this cluster size the limit is {largest:#x} bytes")
            # The count sits in the gap between the widths: FAT12 overflows, and FAT16's larger
            # table pushes it back under the FAT16 minimum. Settle for FAT12 a few clusters short.
            bits = 12
            fat_size, clusters = fit(bits)

    return {
        'bits': bits,
        'total_sectors': total_sectors,
        'sector_size': sector_size,
        'sectors_per_cluster': sectors_per_cluster,
        'fat_count': fat_count,
        'fat_size': fat_size,
        'root_entries': root_entries,
        'root_dir_sectors': root_dir_sectors,
        'clusters': clusters,
    }


def _format(size: int, geometry: dict, volume_id: int) -> bytearray:
    """Lay down a boot sector, empty FATs and an empty root directory."""
    sector_size = geometry['sector_size']
    total_sectors = geometry['total_sectors']

    boot = bytearray(b'\x00' * sector_size)
    boot[0:3] = b'\xeb\xfe\x90'  # jump instruction; ESP-IDF writes an endless loop
    boot[3:11] = OEM_NAME.encode('ascii').ljust(8)
    struct.pack_into(
        '<HBHBHHBHHHII', boot, 11,
        sector_size,                        # BPB_BytsPerSec
        geometry['sectors_per_cluster'],    # BPB_SecPerClus
        RESERVED_SECTORS,                   # BPB_RsvdSecCnt
        geometry['fat_count'],              # BPB_NumFATs
        geometry['root_entries'],           # BPB_RootEntCnt
        total_sectors if total_sectors < 0x10000 else 0,   # BPB_TotSec16
        MEDIA_TYPE,                         # BPB_Media
        geometry['fat_size'],               # BPB_FATSz16
        0x3F,                               # BPB_SecPerTrk
        0xFF,                               # BPB_NumHeads
        0,                                  # BPB_HiddSec
        0 if total_sectors < 0x10000 else total_sectors,   # BPB_TotSec32
    )
    boot[36] = 0x80                         # BS_DrvNum
    boot[38] = 0x29                         # BS_BootSig — volume id/label/type fields follow
    struct.pack_into('<I', boot, 39, volume_id)
    boot[43:54] = VOLUME_LABEL.encode('ascii')[:11].ljust(11)
    boot[54:62] = b'FAT     '               # BS_FilSysType, informational only
    boot[510:512] = b'\x55\xaa'

    image = bytearray(ERASED * size)
    image[0:sector_size] = boot

    # Entry 0 carries the media byte, entry 1 is the end-of-chain marker; both FATs start
    # zeroed, which marks every data cluster free.
    head = struct.pack('<I', 0xFFFFFF00 | MEDIA_TYPE)[:3 if geometry['bits'] == 12 else 4]
    fat = bytearray(b'\x00' * (geometry['fat_size'] * sector_size))
    fat[0:len(head)] = head
    for i in range(geometry['fat_count']):
        start = (RESERVED_SECTORS + i * geometry['fat_size']) * sector_size
        image[start:start + len(fat)] = fat

    root_start = (RESERVED_SECTORS + geometry['fat_count'] * geometry['fat_size']) * sector_size
    root_size = geometry['root_dir_sectors'] * sector_size
    image[root_start:root_start + root_size] = b'\x00' * root_size
    return image


def _new_entry(fs: PyFat, parent: FATDirectoryEntry, name: str, attr: int,
               dt: DosDateTime) -> FATDirectoryEntry:
    """Build a directory entry (plus a long-name entry when the name needs one)."""
    short_name = EightDotThree(encoding=fs.encoding)
    short_name.set_str_name(short_name.make_8dot3_name(name, parent))

    entry = FATDirectoryEntry.new(name=short_name, tz=dt.tzinfo, attr=attr, encoding=fs.encoding)
    entry.crttime = entry.wrttime = dt.serialize_time()
    entry.crtdate = entry.wrtdate = entry.lstaccessdate = dt.serialize_date()

    if short_name.get_unpadded_filename() != name:
        entry.set_lfn_entry(make_lfn_entry(name, short_name))
    return entry


def _mkdir(fs: PyFat, parent: FATDirectoryEntry, name: str, dt: DosDateTime) -> FATDirectoryEntry:
    entry = _new_entry(fs, parent, name, FATDirectoryEntry.ATTR_DIRECTORY, dt)

    # A directory always starts with its own '.' and '..' entries; '..' points at cluster 0
    # when the parent is the (cluster-less) fixed root directory.
    entry.set_cluster(fs.allocate_bytes(FATDirectoryEntry.FAT_DIRECTORY_HEADER_SIZE * 2, erase=True)[0])
    for dot_name, template in ((b'.          ', entry), (b'..         ', parent)):
        short_name = EightDotThree()
        short_name.set_byte_name(dot_name)
        dot = copy(template)
        dot.name = short_name
        dot.lfn_entry = None
        dot._parent = None
        if template is parent and parent == fs.root_dir:
            dot.set_cluster(0)
        entry.add_subdirectory(dot)

    fs.update_directory_entry(entry)
    parent.add_subdirectory(entry)
    fs.update_directory_entry(parent)
    return entry


def _mkfile(fs: PyFat, parent: FATDirectoryEntry, name: str, data: bytes, dt: DosDateTime):
    # ATTR_ARCHIVE is what FatFs (and ESP-IDF's fatfsgen) sets on every new file; some readers,
    # including ESP-IDF's own fatfsparse.py, match on the attribute byte exactly and skip files
    # without it.
    entry = _new_entry(fs, parent, name, FATDirectoryEntry.ATTR_ARCHIVE, dt)
    if data:
        entry.set_cluster(fs.allocate_bytes(len(data))[0])
        entry.filesize = len(data)
    parent.add_subdirectory(entry)
    fs.update_directory_entry(parent)
    if data:
        fs.write_data_to_cluster(data, entry.get_cluster())


def create(sources: list[SourceEntry], size: int, *, wear_levelling: bool = True,
           device_id: Optional[int] = None, **options) -> bytes:
    """Build a FAT image that fills a `size`-byte partition.

    With wear levelling (the default, and how ESP-IDF mounts a ``fat`` partition in SPI
    flash) the filesystem is built to fit inside the container and then wrapped, so the
    result is still exactly `size` bytes.
    """
    if not wear_levelling:
        return _build(sources, size, **options)
    return wl.wrap(_build(sources, wl.filesystem_size(size), **options), size, device_id=device_id)


def _build(sources: list[SourceEntry], size: int, *, sector_size: int = SECTOR_SIZE,
           sectors_per_cluster: int = SECTORS_PER_CLUSTER, fat_count: int = FAT_COUNT,
           root_entries: int = ROOT_ENTRIES, fat_type: Optional[int] = None,
           volume_id: Optional[int] = None, **_) -> bytes:
    """Build a bare FAT image of exactly `size` bytes holding `sources`."""
    if size % sector_size != 0:
        raise FsError(f"Filesystem size {size:#x} is not a multiple of the sector size {sector_size:#x}")

    geometry = _geometry(size, sector_size, sectors_per_cluster, fat_count, root_entries, fat_type)
    if volume_id is None:
        volume_id = int.from_bytes(os.urandom(4), 'little')

    fp = _Image(bytes(_format(size, geometry, volume_id)))
    fs = PyFat(encoding=FAT_OEM_ENCODING)
    fs.set_fp(fp)
    # pyfatfs sizes its allocator from the FAT table rather than from the volume, so it will
    # hand out clusters that don't exist whenever the FAT has room to spare — writing past the
    # end of the image instead of reporting that it is full. Reserve the entries past the end
    # of the volume for the duration of the build so they are never handed out.
    #
    # They are reserved rather than removed, and one spare is always kept, because pyfatfs's
    # allocator only breaks out of its search on the iteration *after* it has found enough
    # clusters: with nothing beyond the last real cluster the loop ends by exhaustion and
    # reports the volume full, so an image whose contents exactly fill it would be rejected.
    entries = geometry['clusters'] + 2
    reserved = fs.FAT_CLUSTER_VALUES[fs.fat_type]['END_OF_CLUSTER_MAX']
    if len(fs.fat) <= entries:
        fs.fat.extend([reserved] * (entries + 1 - len(fs.fat)))
    for i in range(entries, len(fs.fat)):
        fs.fat[i] = reserved

    try:
        directories = {'': fs.root_dir}
        for source in sources:
            # Split on '/' rather than via pathlib: source paths are always posix, and
            # pathlib would rewrite the separator (and so miss the key) on Windows.
            parent_path, _, name = source.path.rpartition('/')
            parent = directories[parent_path]
            dt = DosDateTime.fromtimestamp(source.mtime)
            if source.is_dir:
                directories[source.path] = _mkdir(fs, parent, name, dt)
            else:
                _mkfile(fs, parent, name, source.read(), dt)
        # Drop the reserved entries again so only the volume's own clusters reach the disk;
        # the rest of the FAT region keeps the zeroes _format() wrote, which reads as free.
        del fs.fat[entries:]
        fs.flush_fat()
        fs.close()
    except PyFATException as e:
        if e.errno == errno.ENOSPC:
            capacity = geometry['clusters'] * geometry['sectors_per_cluster'] * geometry['sector_size']
            raise FsError(f"Sources do not fit in a {size:#x}-byte FAT image "
                          f"({capacity:#x} bytes usable): {e}") from e
        raise FsError(f"Failed to build FAT image: {e}") from e
    except Exception as e:
        raise FsError(f"Failed to build FAT image: {e}") from e

    image = fp.release()
    if len(image) != size:
        raise FsError(f"Built a {len(image):#x}-byte FAT image for a {size:#x}-byte volume — "
                      f"this is a bug, please report it")
    return image


class FatVolume(Volume):
    def __init__(self, image: bytes):
        self.fs = PyFat(encoding=FAT_OEM_ENCODING)
        try:
            self.fs.set_fp(_Image(image))
        except Exception as e:
            raise FsError(f"Not a readable FAT filesystem: {e}") from e

    def entries(self) -> list[FsEntry]:
        return list(self._walk(self.fs.root_dir))

    def _walk(self, directory: FATDirectoryEntry, prefix: str = ''):
        dirs, files, _ = directory.get_entries()
        for entry in sorted(dirs + files, key=lambda e: e.get_short_name()):
            # Only names that don't fit 8.3 (or that would lose their case) carry a long
            # name entry; everything else is stored — and reads back — upper-cased.
            name = entry.get_long_name() if entry.lfn_entry else entry.get_short_name()
            path = f"{prefix}{name}"
            if entry.is_directory():
                yield FsEntry(path=path, is_dir=True, size=0)
                yield from self._walk(entry, f"{path}/")
            else:
                yield FsEntry(path=path, is_dir=False, size=entry.filesize, handle=entry)

    def read(self, entry: FsEntry) -> bytes:
        dentry = entry.handle
        if dentry.filesize == 0:
            return b''
        chain = self.fs.get_cluster_chain(dentry.get_cluster())
        return b''.join(self.fs.read_cluster_contents(c) for c in chain)[:dentry.filesize]

    def close(self):
        self.fs.close()


def mount(image: bytes, *, wear_levelling: Optional[bool] = None, **_) -> FatVolume:
    """Open a FAT image, unwrapping wear levelling when the image turns out to have it."""
    if wear_levelling is None:
        wear_levelling = wl.looks_like_wl(image)
    return FatVolume(wl.unwrap(image) if wear_levelling else image)


def detect(image: bytes) -> bool:
    """Recognise a FAT image by its boot sector: signature word plus a plausible BPB."""
    if wl.looks_like_wl(image):
        image = wl.unwrap(image)
    if len(image) < 512 or image[510:512] != b'\x55\xaa':
        return False
    bytes_per_sector, sectors_per_cluster = struct.unpack_from('<HB', image, 11)
    if bytes_per_sector not in (512, 1024, 2048, 4096) or sectors_per_cluster == 0:
        return False
    if sectors_per_cluster & (sectors_per_cluster - 1):  # must be a power of two
        return False
    reserved, fat_count = struct.unpack_from('<HB', image, 14)
    return reserved > 0 and fat_count in (1, 2) and image[21] in range(0xF0, 0x100)


def describe(size: int, *, sector_size: int = SECTOR_SIZE,
             sectors_per_cluster: int = SECTORS_PER_CLUSTER, fat_count: int = FAT_COUNT,
             root_entries: int = ROOT_ENTRIES, fat_type: Optional[int] = None,
             wear_levelling: bool = True, **_) -> str:
    """One-line summary of the geometry a `size`-byte partition would be built with."""
    overhead = size - wl.filesystem_size(size) if wear_levelling else 0
    g = _geometry(size - overhead, sector_size, sectors_per_cluster, fat_count, root_entries, fat_type)
    levelling = f", wear levelling overhead {overhead:#x} bytes" if wear_levelling else ""
    return (f"FAT{g['bits']}, {g['clusters']} clusters of "
            f"{g['sectors_per_cluster'] * g['sector_size']:#x} bytes{levelling}")
