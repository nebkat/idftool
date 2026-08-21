"""Partition bundles (ZIP): ``create-bundle``, ``dump-bundle``, ``write-bundle``, ``print-bundle``."""
import os.path
import time
from zipfile import BadZipFile, ZipFile

import rich_click as click

from esptool.cmds import write_flash

from esp_idf_defs.partitions import APP_TYPE

from idftool.apps import print_partition_table_and_apps, validate_app_binary
from idftool.cli import cli, pass_state
from idftool.flash import flash_options, option_group, write_flash_options
from idftool.partitions import get_partition, parse_partition_table_csv

# Keep the pass-through write options in a panel of their own.
option_group('write-bundle')

def create_bundle(state, output_file, flash_partition_table, files):
    loaded = state.setup(needs_device=False)
    files = list(files)
    if len(files) % 2 != 0:
        raise ValueError("Files list must contain pairs of partition name and input file")

    with ZipFile(output_file, 'w') as zf:
        # Add specified partition files
        for partition_name, input_file in list(zip(files[::2], files[1::2])):
            partition = get_partition(
                loaded.partition_table, loaded.partition_table_entry, loaded.bootloader_entry, partition_name)
            input_file_size = os.path.getsize(input_file)
            if input_file_size > partition.size:
                raise ValueError(
                    f"Input file {input_file} size {input_file_size:#x} exceeds partition {partition.name} size {partition.size:#x}")
            zf.write(input_file, arcname=f"{partition.name}.bin")
            print(f"Adding file {input_file} (size={input_file_size:#x}) to bundle as partition {partition.name} (offset={partition.offset:#x}, size={partition.size:#x})")

        # Add partition table if requested
        if flash_partition_table:
            zf.writestr("partition_table.csv", loaded.partition_table.to_csv())
            print(f"Adding partition table CSV to bundle")


@cli.command('create-bundle', help='Pack partition images into a ZIP bundle')
@click.option('-o', '--output', 'output_file', required=True, help='Output ZIP filename')
@click.option('--flash-partition-table', is_flag=True, help='Include the partition table in the bundle')
@click.argument('files', nargs=-1, required=True, metavar='PARTITION FILENAME ...')
@pass_state
def cmd_create_bundle(state, output_file, flash_partition_table, files):
    return create_bundle(state, output_file, flash_partition_table, files)


def dump_bundle(state, output_file):
    loaded = state.setup()
    esp, partition_table = loaded.esp, loaded.partition_table
    if not output_file:
        chip = esp.CHIP_NAME.lower().replace(' ', '-')
        try:
            mac = esp.read_mac("BASE_MAC")
            serial = ''.join(f"{b:02x}" for b in mac)
        except Exception:
            serial = "unknown"
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        output_file = f"{chip}-{serial}-{timestamp}.zip"

    with ZipFile(output_file, 'w') as zf:
        for partition in partition_table:
            print(f"Reading partition {partition.name} (offset={partition.offset:#x}, size={partition.size:#x})")
            data = esp.read_flash(partition.offset, partition.size)
            zf.writestr(f"{partition.name}.bin", data)
        zf.writestr("partition_table.csv", partition_table.to_csv())
        print(f"Adding partition table CSV to bundle")

    print(f"Bundle written to {output_file}")


@cli.command('dump-bundle', help='Pack every partition from the device into a ZIP')
@click.argument('output_file', required=False)
@pass_state
def cmd_dump_bundle(state, output_file):
    return dump_bundle(state, output_file)


def write_bundle(state, input_file, **options):
    """Flash every binary in a bundle ZIP. Keyword arguments go to esptool's ``write_flash``
    (see :data:`idftool.flash.WRITE_FLASH_OPTIONS`)."""
    loaded = state.setup(bundle_file=input_file)
    esp, partition_table = loaded.esp, loaded.partition_table
    with ZipFile(input_file, 'r') as zf:
        # Flash files
        addr_data = []
        for member in filter(lambda m: m.endswith('.bin'), zf.namelist()):
            partition_name = member[:-4]  # Remove .bin extension
            partition = get_partition(
                partition_table, loaded.partition_table_entry, loaded.bootloader_entry, partition_name)
            data = zf.read(member)
            if len(data) > partition.size:
                raise ValueError(f"Bundle entry {member} size {len(data):#x} exceeds partition {partition.name} size {partition.size:#x}")

            if partition.type == APP_TYPE:
                validate_app_binary(esp, data)

            print(f"Writing partition {partition.name} (offset={partition.offset:#x}, size={partition.size:#x}) from bundle")
            addr_data.append((partition.offset, data))

        # Flash partition table if present. Use the virtual partition-table entry (as create-image
        # does): a table parsed from CSV or read from flash never contains a self-referential
        # partition-table row, so searching `partition_table` for one raises StopIteration.
        if any(m == 'partition_table.csv' for m in zf.namelist()):
            partition = loaded.partition_table_entry
            print(f"Writing partition table (offset={partition.offset:#x}, size={partition.size:#x}) from bundle")
            addr_data.append((partition.offset, partition_table.to_binary()))

        # Perform write
        write_flash(
            esp=esp,
            addr_data=addr_data,
            flash_size='detect',
            **write_flash_options(options),
        )


@cli.command('write-bundle', help='Flash every binary in a bundle ZIP')
@click.argument('input_file')
@flash_options
@pass_state
def cmd_write_bundle(state, input_file, **options):
    return write_bundle(state, input_file, **options)


def print_bundle(state, bundle_file):
    bundle_size = os.path.getsize(bundle_file)
    if bundle_size == 0:
        raise RuntimeError(f"Bundle '{bundle_file}' is empty")
    try:
        bundle_zip = ZipFile(bundle_file, 'r')
    except BadZipFile as e:
        raise RuntimeError(f"Bundle '{bundle_file}' is not a valid ZIP archive") from e
    with bundle_zip as zf:
        names = zf.namelist()
        if 'partition_table.csv' not in names:
            raise RuntimeError(f"Bundle {bundle_file} does not contain a partition_table.csv")
        csv_text = zf.read('partition_table.csv').decode('utf-8')
        if not csv_text.strip():
            raise RuntimeError(f"Bundle '{bundle_file}' contains an empty partition_table.csv")

        partition_table = parse_partition_table_csv(
            csv_text, f"bundle '{bundle_file}'", state.partition_table_offset,
            state.primary_bootloader_offset, state.recovery_bootloader_offset)

        partition_data = {
            name[:-4]: zf.read(name)
            for name in names if name.endswith('.bin')
        }

        def read(offset: int, length: int) -> bytes:
            for part in partition_table:
                if part.offset <= offset < part.offset + part.size:
                    local = offset - part.offset
                    data = partition_data.get(part.name, b"")
                    chunk = data[local:local + length]
                    if len(chunk) < length:
                        chunk += b"\xff" * (length - len(chunk))
                    return chunk
            return b"\xff" * length

        included = sorted(partition_data.keys())
        print(f"Bundle: {bundle_file} ({bundle_size:#x} bytes)")
        print(f"Partitions included: {', '.join(included) if included else '(none)'}")
        print()
        print_partition_table_and_apps(partition_table, read)


@cli.command('print-bundle', help='Print partition table and app info from a bundle ZIP')
@click.option('-f', '--file', 'bundle_file', required=True, help='Bundle ZIP to read')
@pass_state
def cmd_print_bundle(state, bundle_file):
    return print_bundle(state, bundle_file)
