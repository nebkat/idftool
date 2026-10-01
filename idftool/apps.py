"""Application binaries: validation against the connected chip, and app info printing."""
import os.path
from typing import Callable, Optional

from esptool import ESPLoader
from esptool.targets import CHIP_DEFS

from esp_idf_defs import ImageMetadata, ChipId
from esp_idf_defs.partitions import PartitionTable, PartitionDefinition, APP_TYPE

from idftool.display import print_partition_table

#: Image chip ID → the flash offset that chip's ROM boots from.
BOOTLOADER_OFFSETS = {chip.IMAGE_CHIP_ID: chip.BOOTLOADER_FLASH_OFFSET
                      for chip in CHIP_DEFS.values() if hasattr(chip, 'IMAGE_CHIP_ID')}


def _check_chip(esp: ESPLoader, image_metadata: ImageMetadata):
    if image_metadata.header.chip_id.value != esp.IMAGE_CHIP_ID:
        raise RuntimeError(
            f"Chip ID mismatch: "
            f"attempting to flash {image_metadata.header.chip_id.name} image "
            f"to {ChipId(esp.IMAGE_CHIP_ID).name} device"
        )


def validate_app_binary(esp: ESPLoader, app_binary: bytes) -> tuple[bytes, ImageMetadata]:
    try:
        image_metadata = ImageMetadata.from_bytes(app_binary, app_required=True)
    except ValueError as e:
        raise RuntimeError(f"Invalid application binary: {e}")
    _check_chip(esp, image_metadata)
    return app_binary, image_metadata


def validate_bootloader_binary(esp: ESPLoader, bootloader_binary: bytes) -> ImageMetadata:
    """Check a bootloader is a valid image built for the connected chip."""
    try:
        image_metadata = ImageMetadata.from_bytes(bootloader_binary)
    except (RuntimeError, ValueError) as e:
        raise RuntimeError(f"Invalid bootloader binary: {e}")
    if image_metadata.app_description is not None:
        raise RuntimeError("Invalid bootloader binary: it is an application")
    _check_chip(esp, image_metadata)
    return image_metadata


def parse_bootloader(data: bytes, offset: Optional[int] = None
                     ) -> Optional[tuple[Optional[int], ImageMetadata]]:
    """`data` as a bootloader, as ``(offset, metadata)``: at `offset`, else the offset its chip
    boots from. None if it isn't an ESP image."""
    try:
        image_metadata = ImageMetadata.from_bytes(data)
    except Exception:  # noqa: BLE001
        return None
    if offset is None:
        offset = BOOTLOADER_OFFSETS.get(image_metadata.header.chip_id.value)
    return offset, image_metadata


def find_bootloader(read: Callable[[int, int], bytes],
                    partition_table_offset: int) -> Optional[tuple[int, ImageMetadata]]:
    """The first chip bootloader offset below the table holding an image for a chip that boots
    from there, as ``(offset, metadata)``."""
    for offset in sorted(set(BOOTLOADER_OFFSETS.values())):
        if offset >= partition_table_offset:
            continue
        found = parse_bootloader(read(offset, partition_table_offset - offset))
        if found is not None and found[0] == offset:
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

def print_bootloader(bootloader: Optional[tuple[Optional[int], ImageMetadata]]):
    if bootloader is None:
        print("Bootloader: none")
        return
    offset, image_metadata = bootloader
    where = f" (offset={offset:#x})" if offset is not None else ""
    print(f"Bootloader: {image_metadata.header.chip_id.name}{where}")


def print_partition_table_and_apps(
        partition_table: PartitionTable,
        read: Callable[[int, int], bytes],
        bootloader: Optional[tuple[Optional[int], ImageMetadata]] = None,
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
