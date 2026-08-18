"""Whole-flash images: ``create-image``, ``dump-image``, ``write-image``, ``print-image``."""
import os.path
import time

import rich_click as click

from esptool import flash_size_bytes
from esptool.cmds import read_flash, write_flash, merge_bin, erase_flash, detect_flash_size

from esp_idf_defs import ImageMetadata, ChipId
from esp_idf_defs.partitions import PartitionTable

from idftool.apps import print_partition_table_and_apps
from idftool.cli import cli, pass_state
from idftool.partitions import check_image_file, get_partition_address, require_partitions

def create_image(state, output_file, output_format, flash_partition_table, files):
    loaded = state.setup(needs_device=False)
    files = list(files)
    if len(files) % 2 != 0:
        raise ValueError("Files list must contain pairs of partition name and input file")

    addr_data = []
    for partition_name, input_file in list(zip(files[::2], files[1::2])):
        partition, address = get_partition_address(
            loaded.partition_table, loaded.partition_table_entry, loaded.bootloader_entry, partition_name)
        input_file_size = os.path.getsize(input_file)
        if input_file_size > partition.size:
            raise ValueError(
                f"Input file {input_file} size {input_file_size:#x} exceeds partition {partition.name} size {partition.size:#x}")
        addr_data.append((address, input_file))
        print(f"Merging file {input_file} (size={input_file_size:#x}) to partition {partition.name} (offset={address:#x}, size={partition.size:#x})")

    # Add partition table if requested
    if flash_partition_table:
        partition_table_binary = loaded.partition_table.to_binary()
        addr_data.append((loaded.partition_table_entry.offset, partition_table_binary))

    merge_bin(
        addr_data=addr_data,
        flash_freq="keep",
        flash_mode="keep",
        flash_size="keep",
        chip='esp32', # Does not matter since we are keeping all flash parameters
        output=output_file,
        output_format=output_format,
        partition_table=loaded.partition_table
    )


@cli.command('create-image', help='Merge partition binaries into a single flash image')
@click.option('-o', '--output', 'output_file', required=True, help='Output filename')
@click.option('-f', '--format', 'output_format', type=click.Choice(['raw', 'uf2', 'hex']),
              default='raw', show_default=True, help='Output format')
@click.option('--flash-partition-table', is_flag=True, help='Include the partition table in the image')
@click.argument('files', nargs=-1, required=True, metavar='PARTITION FILENAME ...')
@pass_state
def cmd_create_image(state, output_file, output_format, flash_partition_table, files):
    return create_image(state, output_file, output_format, flash_partition_table, files)


def dump_image(state, output_file):
    esp = state.connect()
    flash_size = flash_size_bytes(detect_flash_size(esp))

    if not output_file:
        chip = esp.CHIP_NAME.lower().replace(' ', '-')
        try:
            mac = esp.read_mac("BASE_MAC")
            serial = ''.join(f"{b:02x}" for b in mac)
        except Exception:
            serial = "unknown"
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        output_file = f"{chip}-{serial}-{timestamp}.img"

    print(f"Dumping {flash_size:#x} bytes of flash to {output_file}...")
    read_flash(esp, address=0, size=flash_size, output=output_file)


@cli.command('dump-image', help='Dump the entire flash to an image file')
@click.argument('output_file', required=False)
@pass_state
def cmd_dump_image(state, output_file):
    return dump_image(state, output_file)


def write_image(state, image_file):
    loaded = state.setup(image_file=image_file)
    esp = loaded.esp
    if os.path.getsize(image_file) == 0:
        raise RuntimeError(f"Image file '{image_file}' is empty")
    with open(image_file, 'rb') as f:
        f.seek(loaded.bootloader_entry.offset)
        bootloader_binary = f.read(loaded.bootloader_entry.size)
    try:
        bootloader_image_metadata = ImageMetadata.from_bytes(bootloader_binary)
    except (RuntimeError, ValueError) as e:
        raise RuntimeError("Invalid bootloader in image, check the input file (chip type may be incorrect)")\
            from e

    if bootloader_image_metadata.header.chip_id.value != esp.IMAGE_CHIP_ID:
        raise RuntimeError(
            f"Chip ID mismatch: "
            f"attempting to flash {bootloader_image_metadata.header.chip_id.name} image "
            f"to {ChipId(esp.IMAGE_CHIP_ID).name} device"
        )

    with open(image_file, 'rb') as image_f:
        image_f.seek(esp.BOOTLOADER_FLASH_OFFSET)
        image_data = image_f.read()

    print(f"Writing image '{image_file}'...")
    erase_flash(esp)
    write_flash(
        esp=esp,
        addr_data=[(esp.BOOTLOADER_FLASH_OFFSET, image_data)],
        flash_size='detect',
    )


@cli.command('write-image', aliases=['reflash'], help='Write a full flash image to the device')
@click.argument('image_file')
@pass_state
def cmd_write_image(state, image_file):
    return write_image(state, image_file)


def print_image(state, image_file):
    image_size = check_image_file(image_file, state.partition_table_offset)
    with open(image_file, 'rb') as f:
        f.seek(state.partition_table_offset)
        partition_table_binary = f.read(state.partition_table_size)
        try:
            partition_table = PartitionTable.from_binary(partition_table_binary)
        except (RuntimeError, ValueError) as e:
            raise RuntimeError(f"Could not parse partition table at offset {state.partition_table_offset:#x}") from e
        require_partitions(partition_table, f"image at offset {state.partition_table_offset:#x}")

        def read(offset: int, length: int) -> bytes:
            if offset >= image_size:
                return b"\xff" * length
            f.seek(offset)
            data = f.read(length)
            if len(data) < length:
                data += b"\xff" * (length - len(data))
            return data

        print(f"Image: {image_file} ({image_size:#x} bytes)")
        print()
        print_partition_table_and_apps(partition_table, read)


@cli.command('print-image', help='Print partition table and app info from a flash image file')
@click.argument('image_file')
@pass_state
def cmd_print_image(state, image_file):
    return print_image(state, image_file)
