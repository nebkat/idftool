"""Application binaries: validation against the connected chip, and app info printing."""
import os.path
import struct
from dataclasses import dataclass
from typing import Callable, ClassVar, Optional
from weakref import WeakKeyDictionary

from esptool import ESPLoader
from esptool.targets import CHIP_DEFS

from esp_idf_defs import AppDescription, ImageHeader, ImageMetadata, ChipId
from esp_idf_defs.partitions import PartitionTable, PartitionDefinition, APP_TYPE, BOOTLOADER_TYPE, \
    PARTITION_TABLE_SIZE

from idftool.display import print_partition_table

#: Image chip ID → the flash offset that chip's ROM boots from.
BOOTLOADER_OFFSETS: dict[int, int] = {chip.IMAGE_CHIP_ID: chip.BOOTLOADER_FLASH_OFFSET
                      for chip in CHIP_DEFS.values() if hasattr(chip, 'IMAGE_CHIP_ID')}


@dataclass
class BootloaderDescription:
    """A bootloader's ``esp_bootloader_desc_t`` (ESP-IDF 5.2+), at the start of its first
    segment."""
    MAGIC: ClassVar[int] = 0x50
    SIZE: ClassVar[int] = 80

    secure_version: int
    version: int
    idf_version: str
    date_time: str
    #: Where the partition table this bootloader reads is, or 0 if it doesn't record it. The field
    #: esp-idf#19102 proposes, in the first 4 bytes of ``reserved2``, which bootloaders without it
    #: leave 0.
    partition_table_offset: int = 0

    @classmethod
    def from_bytes_or_none(cls, data: bytes) -> Optional['BootloaderDescription']:
        if len(data) < cls.SIZE or data[0] != cls.MAGIC:
            return None
        secure_version, version = data[3], struct.unpack_from('<I', data, 4)[0]

        def text(raw: bytes) -> str:
            return raw.split(b'\0', 1)[0].decode('utf-8', errors='replace')

        return cls(secure_version, version, text(data[8:40]), text(data[40:64]),
                   struct.unpack_from('<I', data, 64)[0])


@dataclass
class ImageInfo:
    """What an ESP image is: ``app``, ``bootloader``, or ``image`` when it carries neither
    descriptor (e.g. a bootloader older than ESP-IDF 5.2)."""
    kind: str
    metadata: ImageMetadata
    bootloader: Optional[BootloaderDescription] = None


def identify_image(data: bytes) -> Optional[ImageInfo]:
    """Parse `data` as an ESP image and tell apps and bootloaders apart, or None if it isn't one."""
    try:
        metadata = ImageMetadata.from_bytes(data)
    except Exception:  # noqa: BLE001
        return None
    if metadata.app_description is not None:
        return ImageInfo('app', metadata)
    first = metadata.segments[0] if metadata.segments else None
    description = (BootloaderDescription.from_bytes_or_none(
        data[first.offset:first.offset + first.length]) if first else None)
    return ImageInfo('bootloader' if description else 'image', metadata, description)


def _check_chip(esp: ESPLoader, image_metadata: ImageMetadata) -> None:
    if image_metadata.header.chip_id.value != esp.IMAGE_CHIP_ID:
        raise RuntimeError(
            f"Chip ID mismatch: "
            f"attempting to flash {image_metadata.header.chip_id.name} image "
            f"to {ChipId(esp.IMAGE_CHIP_ID).name} device"
        )


def validate_app_binary(esp: ESPLoader, app_binary: bytes, name: str = "The image"
                        ) -> tuple[bytes, ImageMetadata]:
    info = identify_image(app_binary)
    if info is not None and info.kind == 'bootloader':
        raise RuntimeError(f"{name} is a bootloader, not an app")
    try:
        image_metadata = ImageMetadata.from_bytes(app_binary, app_required=True)
    except ValueError as e:
        raise RuntimeError(f"Invalid application binary: {e}")
    _check_chip(esp, image_metadata)
    return app_binary, image_metadata


def validate_bootloader_binary(esp: ESPLoader, bootloader_binary: bytes, name: str = "The image"
                               ) -> ImageMetadata:
    """Check a bootloader is a valid image built for the connected chip."""
    info = identify_image(bootloader_binary)
    if info is None:
        try:
            ImageMetadata.from_bytes(bootloader_binary)
        except (RuntimeError, ValueError) as e:
            raise RuntimeError(f"Invalid bootloader binary: {e}")
        raise RuntimeError("Invalid bootloader binary")
    if info.kind == 'app':
        raise RuntimeError(f"{name} is an app, not a bootloader")
    _check_chip(esp, info.metadata)
    return info.metadata


def check_partition_image(esp: ESPLoader, partition: PartitionDefinition, data: bytes,
                          name: str, force: bool = False) -> None:
    """Check an image about to fill an app or bootloader partition. `force` warns instead."""
    try:
        # An erased slot (as dump-bundle saves an unused OTA partition) has no app to check.
        if partition.type == APP_TYPE and data.strip(b'\xff'):
            validate_app_binary(esp, data, name)
        elif partition.type == BOOTLOADER_TYPE:
            validate_bootloader_binary(esp, data, name)
    except RuntimeError as e:
        if not force:
            raise RuntimeError(f"{e}. Pass --force to write it anyway.") from None
        print(f"Warning: {e} (writing it anyway, --force)")


@dataclass
class Bootloader:
    offset: Optional[int]
    metadata: ImageMetadata
    description: Optional[BootloaderDescription]


def parse_bootloader(data: bytes, offset: Optional[int] = None) -> Optional[Bootloader]:
    """`data` as a bootloader: at `offset`, else the offset its chip boots from. None if it
    isn't an ESP image, or is an app."""
    info = identify_image(data)
    if info is None or info.kind == 'app':
        return None
    if offset is None:
        offset = BOOTLOADER_OFFSETS.get(info.metadata.header.chip_id.value)
    return Bootloader(offset, info.metadata, info.bootloader)


def find_bootloader(read: Callable[[int, int], bytes],
                    partition_table_offset: int) -> Optional[Bootloader]:
    """The first chip bootloader offset below the table holding a bootloader for a chip that
    boots from there."""
    for offset in sorted(set(BOOTLOADER_OFFSETS.values())):
        if offset >= partition_table_offset:
            continue
        found = parse_bootloader(read(offset, partition_table_offset - offset))
        if found is not None and found.offset == offset:
            return found
    return None

#: Where ``esp_bootloader_desc_t`` is in a bootloader image: after the image and first segment headers.
BOOTLOADER_DESCRIPTION_OFFSET = 0x20
BOOTLOADER_DESCRIPTION_END = BOOTLOADER_DESCRIPTION_OFFSET + BootloaderDescription.SIZE
#: Past this, no write can reach a bootloader's description.
BOOTLOADER_DESCRIPTIONS_END = max(BOOTLOADER_OFFSETS.values()) + BOOTLOADER_DESCRIPTION_END


def partition_table_offset_recorded_by(bootloader: bytes, offset: int,
                                       limit: Optional[int] = None) -> Optional[int]:
    """Where `bootloader`, an image flashed at `offset`, records its partition table, or None if it
    records none. Raises if what it records can't be a partition table's offset: one on a sector
    boundary, past the bootloader, and with the table below `limit`, the flash's or image's end."""
    if len(bootloader) < BOOTLOADER_DESCRIPTION_END or bootloader[0] != ImageHeader.MAGIC:
        return None
    description = BootloaderDescription.from_bytes_or_none(bootloader[BOOTLOADER_DESCRIPTION_OFFSET:])
    if description is None or description.partition_table_offset == 0:
        return None
    recorded = description.partition_table_offset
    if recorded % 0x1000 or recorded <= offset or (limit is not None and recorded + PARTITION_TABLE_SIZE > limit):
        raise RuntimeError(f"The bootloader at {offset:#x} records partition table offset {recorded:#x}, "
                           f"which can't be one")
    return recorded


def bootloader_offset_of(bootloader: bytes) -> Optional[int]:
    """The offset the chip `bootloader` is built for boots it from, or None if it isn't an image."""
    if len(bootloader) < 14 or bootloader[0] != ImageHeader.MAGIC:
        return None
    return BOOTLOADER_OFFSETS.get(struct.unpack_from('<H', bootloader, 12)[0])


def recorded_partition_table_offset(read: Callable[[int, int], bytes], bootloader_offsets: list[int],
                                    limit: Optional[int] = None) -> Optional[int]:
    """Where the bootloader at the first of `bootloader_offsets` holding one for a chip that boots
    from there records its partition table, or None."""
    for offset in bootloader_offsets:
        data = read(offset, BOOTLOADER_DESCRIPTION_END)
        if bootloader_offset_of(data) != offset:
            continue
        return partition_table_offset_recorded_by(data, offset, limit)
    return None


#: What each connected device's bootloader records, as last read; forgotten whenever idftool writes
#: over a bootloader description (:func:`forget_device_partition_table_offset`).
_device_table_offsets: 'WeakKeyDictionary[ESPLoader, Optional[int]]' = WeakKeyDictionary()


def device_partition_table_offset(esp: ESPLoader, bootloader_offset: int,
                                  flash_size: Callable[[], Optional[int]]) -> Optional[int]:
    """Where the bootloader at `bootloader_offset` on `esp` records its partition table, or None,
    read once per connection. `flash_size` is asked only when the bootloader is read."""
    if esp not in _device_table_offsets:
        _device_table_offsets[esp] = recorded_partition_table_offset(
            lambda offset, length: esp.read_flash(offset, length), [bootloader_offset], flash_size())
    return _device_table_offsets[esp]


def forget_device_partition_table_offset(esp: ESPLoader) -> None:
    """Have the next :func:`device_partition_table_offset` read the bootloader again."""
    _device_table_offsets.pop(esp, None)


def load_app_binary(esp: ESPLoader, app_binary_file: str, partition: PartitionDefinition) -> tuple[bytes, ImageMetadata]:
    app_binary_size = os.path.getsize(app_binary_file)
    if app_binary_size == 0:
        raise RuntimeError(f"Application binary '{app_binary_file}' is empty")
    if app_binary_size > partition.size:
        raise RuntimeError(
            f"Application binary size '{app_binary_size}' is greater than partition size {partition.size}")

    app_binary = open(app_binary_file, 'rb').read()
    return validate_app_binary(esp, app_binary)


def print_app_info(desc: AppDescription, header: ImageHeader, indent: str = "") -> None:
    print(f"{indent}Project name:     {desc.project_name}")
    print(f"{indent}Version:          {desc.version}")
    print(f"{indent}IDF version:      {desc.idf_version}")
    print(f"{indent}Secure version:   {desc.secure_version}")
    if desc.compiled:
        print(f"{indent}Compiled:         {desc.compiled}")
    print(f"{indent}ELF SHA256:       {desc.elf_sha256.hex()}")
    max_rev = header.max_chip_rev_full if header.max_chip_rev_full != 0xFFFF else None
    rev_range = f"rev {header.min_chip_rev_full} to {max_rev}" if max_rev else f"rev {header.min_chip_rev_full}+"
    print(f"{indent}Chip:             {header.chip_id.name} ({rev_range})")

def print_bootloader(bootloader: Optional[Bootloader]) -> None:
    if bootloader is None:
        print("Bootloader: none")
        return
    where = f" (offset={bootloader.offset:#x})" if bootloader.offset is not None else ""
    idf = f", IDF {bootloader.description.idf_version}" if bootloader.description else ""
    print(f"Bootloader: {bootloader.metadata.header.chip_id.name}{where}{idf}")


def print_partition_table_and_apps(
        partition_table: PartitionTable,
        read: Callable[[int, int], bytes],
        bootloader: Optional[Bootloader] = None,
) -> None:
    print_bootloader(bootloader)
    print()
    print_partition_table(partition_table, read)

    for part in partition_table:
        if part.type != APP_TYPE:
            continue
        try:
            image_metadata = ImageMetadata.from_bytes(read(part.offset, part.size), app_required=True)
        except Exception:
            continue
        assert image_metadata.app_description is not None  # app_required
        print()
        print(f"Partition '{part.name}' (offset={part.offset:#x}):")
        print_app_info(image_metadata.app_description, image_metadata.header, indent="  ")
