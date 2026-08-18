"""ESP-IDF wear levelling container (the ``wear_levelling`` component).

A FAT partition in SPI flash is almost always mounted through the wear
levelling layer (``esp_vfs_fat_spiflash_mount_rw_wl``), which spends a few
sectors of the partition on its own bookkeeping and shifts the filesystem by
one "dummy" sector. The filesystem therefore does not start at offset 0 of the
partition, and on a device that has been written to it is not even contiguous:
the dummy sector migrates through the partition and, once it wraps, the whole
filesystem is rotated by a whole number of sectors.

The layout mirrors ESP-IDF's ``wl_fatfsgen.py``::

    [dummy][filesystem ...][state copy 1][state copy 2][config]

``state copy N`` is ``state_sectors`` sectors long — a 64-byte header followed
by one 16-byte record per sector of the partition, which is how the driver
recovers the dummy sector's position after a power loss. The number of records
scales with the partition, so the overhead is *not* a fixed 4 sectors: a 1 MiB
partition already needs two sectors per state copy, six in total.
"""
import os
import struct
import zlib

# The driver's page/sector size is fixed at 4 KiB for every NOR flash ESP-IDF supports,
# independent of the sector size the filesystem inside the container is formatted with.
SECTOR_SIZE = 0x1000

STATE_HEADER_SIZE = 64
STATE_RECORD_SIZE = 16
STATE_COPY_COUNT = 2  # two copies, for power-failure safety
CONFIG_HEADER_SIZE = 48
DUMMY_SECTORS = 1
CONFIG_SECTORS = 1

# Defaults from ESP-IDF's FATDefaults; `version` is the wl_state_t/wl_config_t layout version.
VERSION = 2
UPDATE_RATE = 16
WR_SIZE = 16
TEMP_BUFFER_SIZE = 32

# wl_state_t: everything up to (but not including) the trailing CRC.
_STATE_T = struct.Struct('<8I28x')
# wl_config_t: same, 8 words then the CRC.
_CONFIG_T = struct.Struct('<8I')

ERASED = b'\xff'


class WearLevellingError(RuntimeError):
    pass


def _crc32(data: bytes) -> int:
    # esp_rom_crc32_le(UINT32_MAX, ...) — a plain CRC-32 seeded with 0xFFFFFFFF.
    return zlib.crc32(data, 0xFFFFFFFF) & 0xFFFFFFFF


def state_sectors(partition_size: int) -> int:
    """Sectors taken by one copy of the wear levelling state."""
    total_sectors = partition_size // SECTOR_SIZE
    state_size = STATE_HEADER_SIZE + STATE_RECORD_SIZE * total_sectors
    return (state_size + SECTOR_SIZE - 1) // SECTOR_SIZE


def overhead_sectors(partition_size: int) -> int:
    """Sectors of `partition_size` that wear levelling keeps for itself."""
    return DUMMY_SECTORS + CONFIG_SECTORS + STATE_COPY_COUNT * state_sectors(partition_size)


def filesystem_size(partition_size: int) -> int:
    """Bytes left for the filesystem inside a wear-levelled partition."""
    if partition_size % SECTOR_SIZE != 0:
        raise WearLevellingError(
            f"Partition size {partition_size:#x} is not a multiple of the wear levelling "
            f"sector size {SECTOR_SIZE:#x}")
    size = partition_size - overhead_sectors(partition_size) * SECTOR_SIZE
    if size <= 0:
        raise WearLevellingError(
            f"Partition size {partition_size:#x} is too small for wear levelling "
            f"(needs more than {overhead_sectors(partition_size) * SECTOR_SIZE:#x} bytes of overhead)")
    return size


def wrap(filesystem: bytes, partition_size: int, device_id: int | None = None) -> bytes:
    """Wrap a filesystem image in a freshly-initialised wear levelling container.

    `device_id` is stored as-is and lets the driver notice that it is looking at a
    different chip; ESP-IDF randomises it, so pass a fixed value for reproducible builds.
    """
    fs_size = filesystem_size(partition_size)
    if len(filesystem) > fs_size:
        raise WearLevellingError(
            f"Filesystem image size {len(filesystem):#x} exceeds the {fs_size:#x} bytes available "
            f"inside a wear-levelled partition of {partition_size:#x} bytes")
    filesystem = filesystem.ljust(fs_size, ERASED)

    if device_id is None:
        device_id = int.from_bytes(os.urandom(4), 'little')

    state = _STATE_T.pack(
        0,                                              # pos — the dummy sector is still at the front
        fs_size // SECTOR_SIZE + DUMMY_SECTORS,         # max_pos
        0,                                              # move_count
        0,                                              # access_count
        UPDATE_RATE,                                    # max_count
        SECTOR_SIZE,                                    # block_size
        VERSION,
        device_id,
    )
    state += struct.pack('<I', _crc32(state))
    # Records stay erased: no sector has been moved yet, so the recovered position is 0.
    state_copy = state.ljust(state_sectors(partition_size) * SECTOR_SIZE, ERASED)

    config = _CONFIG_T.pack(
        0,                  # start_addr, relative to the partition
        partition_size,     # full_mem_size
        SECTOR_SIZE,        # page_size
        SECTOR_SIZE,        # sector_size
        UPDATE_RATE,
        WR_SIZE,
        VERSION,
        TEMP_BUFFER_SIZE,
    )
    # The CRC is followed by three padding words that align wl_config_t to CONFIG_HEADER_SIZE.
    config += struct.pack('<4I', _crc32(config), 0, 0, 0)
    config = config.ljust(SECTOR_SIZE, ERASED)

    return (ERASED * SECTOR_SIZE) + filesystem + state_copy * STATE_COPY_COUNT + config


def _parse_state(image: bytes, partition_size: int) -> tuple[int, int]:
    """Return (dummy sector position, move count) from whichever state copy is valid."""
    copy_size = state_sectors(partition_size) * SECTOR_SIZE
    states_start = partition_size - CONFIG_SECTORS * SECTOR_SIZE - STATE_COPY_COUNT * copy_size

    for copy in range(STATE_COPY_COUNT):
        start = states_start + copy * copy_size
        header = image[start:start + STATE_HEADER_SIZE]
        if len(header) < STATE_HEADER_SIZE:
            continue
        stored_crc = struct.unpack_from('<I', header, _STATE_T.size)[0]
        if stored_crc != _crc32(header[:_STATE_T.size]):
            continue
        _, _, move_count, *_ = _STATE_T.unpack(header[:_STATE_T.size])

        # The header's `pos` is only rewritten when the dummy sector wraps around; between
        # wraps the driver appends one record per move, so the record count is authoritative.
        pos = 0
        blank = ERASED * STATE_RECORD_SIZE
        for offset in range(start + STATE_HEADER_SIZE, start + copy_size, STATE_RECORD_SIZE):
            if image[offset:offset + STATE_RECORD_SIZE] == blank:
                break
            pos += 1
        return pos, move_count

    raise WearLevellingError("No valid wear levelling state sector found (both copies failed CRC)")


def unwrap(image: bytes) -> bytes:
    """Recover the filesystem image from a wear levelling container.

    Undoes both transformations the driver applies: the dummy sector is cut out of
    wherever it has migrated to, and the remainder is rotated back by `move_count`
    sectors so the filesystem starts where the filesystem thinks it does.
    """
    partition_size = len(image)
    fs_size = filesystem_size(partition_size)
    pos, move_count = _parse_state(image, partition_size)

    dummy = pos * SECTOR_SIZE
    without_dummy = image[:dummy] + image[dummy + SECTOR_SIZE:]
    filesystem = without_dummy[:fs_size]

    rotation = (move_count * SECTOR_SIZE) % fs_size if fs_size else 0
    if rotation:
        filesystem = filesystem[-rotation:] + filesystem[:-rotation]
    return filesystem


def looks_like_wl(image: bytes) -> bool:
    """Return True if `image` is a wear levelling container.

    Identified by the config sector at the very end of the partition: its CRC has to
    check out *and* its recorded size has to match the image we were handed, which no
    bare filesystem image would manage by accident.
    """
    if len(image) < 2 * SECTOR_SIZE or len(image) % SECTOR_SIZE != 0:
        return False
    config = image[-SECTOR_SIZE:][:CONFIG_HEADER_SIZE]
    stored_crc = struct.unpack_from('<I', config, _CONFIG_T.size)[0]
    if stored_crc != _crc32(config[:_CONFIG_T.size]):
        return False
    _, full_mem_size, page_size, sector_size, *_ = _CONFIG_T.unpack(config[:_CONFIG_T.size])
    return full_mem_size == len(image) and page_size == SECTOR_SIZE and sector_size == SECTOR_SIZE
