"""Partition table files: ``print-table``, ``create-table``, ``dump-table``, ``write-table``."""
import time
from typing import Optional

import rich_click as click


from idftool.cli import cli, pass_state
from idftool.display import print_partition_table, say
from idftool.flash import write_flash
from idftool.params import INPUT_FILE
from idftool.partitions import load_partition_table_file, resolve_partition_table_format, \
    write_partition_table_file
from idftool.state import State

def print_table(state: State, table_file: Optional[str]) -> None:
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
@click.option('-f', '--file', 'table_file', type=INPUT_FILE, default=None,
              help='Partition table file to print instead of reading the device')
@pass_state
def cmd_print_table(state: State, table_file: Optional[str]) -> None:
    return print_table(state, table_file)


def create_table(state: State, input_file: str, output_file: str,
                 output_format: Optional[str]) -> None:
    partition_table = load_partition_table_file(
        input_file, state.partition_table_offset,
        state.primary_bootloader_offset, state.recovery_bootloader_offset)
    resolved_format = resolve_partition_table_format(output_file, output_format)
    write_partition_table_file(partition_table, output_file, resolved_format)


# `create-X` is the builder in every other family (create-image, create-bundle, create-nvs,
# create-fs), so it leads here too. `convert-table` stays as an alias: unlike the others this
# one is symmetric — a table's CSV and binary carry the same information, and round-tripping
# bin -> csv -> bin is byte-identical — which is why one command covers both directions.
@cli.command('create-table', aliases=['convert-table'],
             help='Convert a partition table file between CSV and binary')
@click.argument('input_file', type=INPUT_FILE)
@click.argument('output_file')
@click.option('--format', 'output_format', type=click.Choice(['csv', 'bin']), default=None,
              help='Output format (default: inferred from the output extension)')
@pass_state
def cmd_create_table(state: State, input_file: str, output_file: str,
                     output_format: Optional[str]) -> None:
    return create_table(state, input_file, output_file, output_format)


def dump_table(state: State, output_file: Optional[str], output_format: Optional[str]) -> None:
    loaded = state.setup()
    if not output_file:
        esp = loaded.esp
        assert esp is not None  # setup() connects
        chip = esp.CHIP_NAME.lower().replace(' ', '-')
        try:
            mac = esp.read_mac("BASE_MAC")
            serial = ''.join(f"{b:02x}" for b in mac)
        except Exception:
            serial = "unknown"
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        extension = 'bin' if output_format == 'bin' else 'csv'
        output_file = f"{chip}-{serial}-{timestamp}-partition-table.{extension}"
    resolved_format = resolve_partition_table_format(output_file, output_format)
    write_partition_table_file(loaded.partition_table, output_file, resolved_format)


@cli.command('dump-table', help='Read the partition table from the device into a file')
@click.argument('output_file', required=False)
@click.option('-f', '--format', 'output_format', type=click.Choice(['csv', 'bin']), default=None,
              help='Output format (default: inferred from the output extension, else csv)')
@pass_state
def cmd_dump_table(state: State, output_file: Optional[str], output_format: Optional[str]) -> None:
    return dump_table(state, output_file, output_format)


def write_table(state: State, table_file: str, force: bool) -> None:
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
        say(f"Warning: partition table failed verification: {e} (continuing due to --force)")

    state.check_table_fits(partition_table, strict=True, force=force)

    binary = partition_table.to_binary()
    if not state.quiet:
        print_partition_table(partition_table)
        print()
    say(f"Writing partition table ({len(binary):#x} bytes) to offset {state.partition_table_offset:#x}...")
    say("Note: this replaces only the partition map; existing partition data on flash is not moved, "
        "resized, or erased. A table that no longer matches the flash contents can make the device unbootable.")
    write_flash(esp=esp, addr_data=[(state.partition_table_offset, binary)], flash_size='detect',
                skip_flashed=True, diff=True)
    say("Partition table written")


@cli.command('write-table', help='Flash a partition table from a CSV or binary file to the device')
@click.argument('table_file', type=INPUT_FILE)
@click.option('--force', is_flag=True, help="Flash even if the partition table fails verification or doesn't fit in flash")
@pass_state
def cmd_write_table(state: State, table_file: str, force: bool) -> None:
    return write_table(state, table_file, force)
