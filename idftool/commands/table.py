"""Partition table files: ``print-table``, ``convert-table``, ``dump-table``, ``write-table``."""
import time

import rich_click as click

from esptool import flash_size_bytes
from esptool.cmds import write_flash, detect_flash_size

from esp_idf_defs.partitions import print_partition_table

from idftool.cli import cli, pass_state
from idftool.partitions import load_partition_table_file, resolve_partition_table_format, \
    write_partition_table_file

def print_table(state, table_file):
    if table_file:
        partition_table = load_partition_table_file(
            table_file, state.partition_table_offset,
            state.primary_bootloader_offset, state.recovery_bootloader_offset)
        print(f"Partition table: {table_file}")
        print()
        print_partition_table(partition_table)
        return
    # No file: fall through to the shared loader, whose printout is the output (like the device table)
    state.setup(needs_device=False)


@cli.command('print-table', aliases=['list'], help='Print a partition table from a CSV or binary file, or from the device')
@click.argument('table_file', required=False)
@pass_state
def cmd_print_table(state, table_file):
    return print_table(state, table_file)


def convert_table(state, input_file, output_file, output_format):
    partition_table = load_partition_table_file(
        input_file, state.partition_table_offset,
        state.primary_bootloader_offset, state.recovery_bootloader_offset)
    output_format = resolve_partition_table_format(output_file, output_format)
    write_partition_table_file(partition_table, output_file, output_format)


@cli.command('convert-table', aliases=['create-table'], help='Convert a partition table file between CSV and binary')
@click.argument('input_file')
@click.argument('output_file')
@click.option('-f', '--format', 'output_format', type=click.Choice(['csv', 'bin']), default=None,
              help='Output format (default: inferred from the output extension)')
@pass_state
def cmd_convert_table(state, input_file, output_file, output_format):
    return convert_table(state, input_file, output_file, output_format)


def dump_table(state, output_file, output_format):
    loaded = state.setup()
    if not output_file:
        esp = loaded.esp
        chip = esp.CHIP_NAME.lower().replace(' ', '-')
        try:
            mac = esp.read_mac("BASE_MAC")
            serial = ''.join(f"{b:02x}" for b in mac)
        except Exception:
            serial = "unknown"
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        extension = 'bin' if output_format == 'bin' else 'csv'
        output_file = f"{chip}-{serial}-{timestamp}-partition-table.{extension}"
    output_format = resolve_partition_table_format(output_file, output_format)
    write_partition_table_file(loaded.partition_table, output_file, output_format)


@cli.command('dump-table', help='Read the partition table from the device into a file')
@click.argument('output_file', required=False)
@click.option('-f', '--format', 'output_format', type=click.Choice(['csv', 'bin']), default=None,
              help='Output format (default: inferred from the output extension, else csv)')
@pass_state
def cmd_dump_table(state, output_file, output_format):
    return dump_table(state, output_file, output_format)


def write_table(state, table_file, force):
    esp = state.connect()
    partition_table = load_partition_table_file(
        table_file, state.partition_table_offset,
        state.primary_bootloader_offset, state.recovery_bootloader_offset)

    try:
        partition_table.verify(partition_table_offset=state.partition_table_offset)
    except RuntimeError as e:
        if not force:
            raise RuntimeError(
                f"Partition table failed verification: {e}. Re-run with --force to flash it anyway.") from e
        print(f"Warning: partition table failed verification: {e} (continuing due to --force)")

    partition_table.verify_size_fits(flash_size_bytes(detect_flash_size(esp)))

    binary = partition_table.to_binary()
    print_partition_table(partition_table)
    print()
    print(f"Writing partition table ({len(binary):#x} bytes) to offset {state.partition_table_offset:#x}...")
    print("Note: this replaces only the partition map; existing partition data on flash is not moved, "
          "resized, or erased. A table that no longer matches the flash contents can make the device unbootable.")
    write_flash(esp=esp, addr_data=[(state.partition_table_offset, binary)], flash_size='detect')
    print("Partition table written")


@cli.command('write-table', help='Flash a partition table from a CSV or binary file to the device')
@click.argument('table_file')
@click.option('--force', is_flag=True, help='Flash even if the partition table fails verification')
@pass_state
def cmd_write_table(state, table_file, force):
    return write_table(state, table_file, force)
