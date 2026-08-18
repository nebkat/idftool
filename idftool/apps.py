"""Application binaries: validation against the connected chip, and app info printing."""
import os.path
from typing import Callable

from esptool import ESPLoader

from esp_idf_defs import ImageMetadata, ChipId
from esp_idf_defs.partitions import PartitionTable, PartitionDefinition, APP_TYPE, print_partition_table

def validate_app_binary(esp: ESPLoader, app_binary: bytes) -> tuple[bytes, ImageMetadata]:
    try:
        image_metadata = ImageMetadata.from_bytes(app_binary, app_required=True)
    except ValueError as e:
        raise RuntimeError(f"Invalid application binary: {e}")

    if image_metadata.header.chip_id.value != esp.IMAGE_CHIP_ID:
        raise RuntimeError(
            f"Chip ID mismatch: "
            f"attempting to flash {image_metadata.header.chip_id.name} image "
            f"to {ChipId(esp.IMAGE_CHIP_ID).name} device"
        )

    return app_binary, image_metadata

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

def print_partition_table_and_apps(
        partition_table: PartitionTable,
        read: Callable[[int, int], bytes],
):
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
