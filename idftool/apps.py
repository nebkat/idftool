"""Application binaries: validation against the connected chip, and app info printing."""
import os.path
import struct
from dataclasses import dataclass
from typing import Callable, ClassVar, Optional

from esptool import ESPLoader
from esptool.targets import CHIP_DEFS

from esp_idf_defs import ImageMetadata, ChipId
from esp_idf_defs.partitions import PartitionTable, PartitionDefinition, APP_TYPE, BOOTLOADER_TYPE

from idftool.display import print_partition_table

#: Image chip ID → the flash offset that chip's ROM boots from.
BOOTLOADER_OFFSETS = {chip.IMAGE_CHIP_ID: chip.BOOTLOADER_FLASH_OFFSET
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

    @classmethod
    def from_bytes_or_none(cls, data: bytes) -> Optional['BootloaderDescription']:
        if len(data) < cls.SIZE or data[0] != cls.MAGIC:
            return None
        secure_version, version = data[3], struct.unpack_from('<I', data, 4)[0]

        def text(raw):
            return raw.split(b'\0', 1)[0].decode('utf-8', errors='replace')

        return cls(secure_version, version, text(data[8:40]), text(data[40:64]))


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


def _check_chip(esp: ESPLoader, image_metadata: ImageMetadata):
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
                          name: str, force: bool = False):
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

def load_app_binary(esp: ESPLoader, app_binary_file: str, partition: PartitionDefinition) -> tuple[bytes, ImageMetadata]:
    app_binary_size = os.path.getsize(app_binary_file)
    if app_binary_size == 0:
        raise RuntimeError(f"Application binary '{app_binary_file}' is empty")
    if app_binary_size > partition.size:
        raise RuntimeError(
            f"Application binary size '{app_binary_size}' is greater than partition size {partition.size}")

    app_binary = open(app_binary_file, 'rb').read()
    return validate_app_binary(esp, app_binary)


def print_app_info(desc, header, indent: str = ""):
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

def print_bootloader(bootloader: Optional[Bootloader]):
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
):
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
        print()
        print(f"Partition '{part.name}' (offset={part.offset:#x}):")
        print_app_info(image_metadata.app_description, image_metadata.header, indent="  ")
