"""Raw partition I/O: ``read``, ``write``, ``erase``, ``view``."""
import os.path
from typing import Any, Iterable

import rich_click as click

from esptool.cmds import read_flash, erase_region

from idftool.apps import check_partition_image
from idftool.cli import cli, pass_state
from idftool.display import say
from idftool.flash import flash_options, option_group, write_flash, write_flash_options
from idftool.partitions import get_partition_address, get_partition_slice
from idftool.state import State

# Keep the pass-through write options in a panel of their own.
option_group('write')

def hexdump(data: bytes, start: int = 0, width: int = 16) -> None:
    def to_printable_ascii(byte: int) -> str:
        return chr(byte) if 32 <= byte <= 126 else "."

    # If start is not aligned, print an empty row with padding
    misalignment = start % width
    start -= misalignment

    offset = 0
    while offset < len(data):
        chunk = data[offset : offset + width - misalignment]
        hex_values = "-- " * misalignment +  " ".join(f"{byte:02x}" for byte in chunk) + " " + "-- " * (width - len(chunk) - misalignment)
        ascii_values = "-" * misalignment + "".join(to_printable_ascii(byte) for byte in chunk) + "-" * (width - len(chunk) - misalignment)
        print(f"0x{start + offset:08x}  {hex_values} |{ascii_values}|")
        offset += width - misalignment
        misalignment = 0


def read_partition(state: State, partition: str, output_file: str) -> None:
    loaded = state.setup()
    target, address, length = get_partition_slice(
        loaded.partition_table, loaded.partition_table_entry, loaded.bootloader_entry, partition)
    print(f"Reading partition {target.name} (offset={address:#x}, size={length:#x})")
    read_flash(loaded.esp, address=address, size=length, output=output_file)


@cli.command('read', help='Read a partition (or slice) into a file')
@click.argument('partition')
@click.argument('output_file')
@pass_state
def cmd_read(state: State, partition: str, output_file: str) -> None:
    return read_partition(state, partition, output_file)


def write_partitions(state: State, files: Iterable[str], **options: Any) -> None:
    """Write files to named partitions. Keyword arguments go to esptool's ``write_flash``
    (see :data:`idftool.flash.WRITE_FLASH_OPTIONS`)."""
    loaded = state.setup()
    names_and_files = list(files)
    if len(names_and_files) % 2 != 0:
        raise ValueError("Files list must contain pairs of partition name and input file")

    addr_data: list[tuple[int, str]] = []
    for partition_name, input_file in list(zip(names_and_files[::2], names_and_files[1::2])):
        partition, address = get_partition_address(
            loaded.partition_table, loaded.partition_table_entry, loaded.bootloader_entry, partition_name)
        input_file_size = os.path.getsize(input_file)
        if input_file_size > partition.size:
            raise ValueError(f"Input file {input_file} size {input_file_size:#x} exceeds partition {partition.name} size {partition.size:#x}")
        if address == partition.offset:
            with open(input_file, 'rb') as f:
                check_partition_image(loaded.esp, partition, f.read(), f"'{input_file}'",
                                      bool(options.get('force')))
        addr_data.append((address, input_file))
        say(f"Writing file {input_file} (size={input_file_size:#x}) to partition {partition.name} (offset={address:#x}, size={partition.size:#x})")

    write_flash(
        esp=loaded.esp,
        addr_data=addr_data,
        flash_size='detect',
        **write_flash_options(options, skip_flashed=True, diff=True),
    )


@cli.command('write', help='Write one or more files to named partitions')
@click.argument('files', nargs=-1, required=True, metavar='PARTITION FILENAME ...')
@flash_options
@pass_state
def cmd_write(state: State, /, files: tuple[str, ...], **options: Any) -> None:
    return write_partitions(state, files, **options)


def erase_partition(state: State, partition: str) -> None:
    loaded = state.setup()
    target, address, length = get_partition_slice(
        loaded.partition_table, loaded.partition_table_entry, loaded.bootloader_entry, partition)
    say(f"Erasing partition {target.name} (offset={address:#x}, size={length:#x})")
    erase_region(loaded.esp, address=address, size=length)


@cli.command('erase', help='Erase a partition (or slice)')
@click.argument('partition')
@pass_state
def cmd_erase(state: State, partition: str) -> None:
    return erase_partition(state, partition)


def view_partition(state: State, partition: str, width: int, output: str) -> None:
    loaded = state.setup()
    target, address, length = get_partition_slice(
        loaded.partition_table, loaded.partition_table_entry, loaded.bootloader_entry, partition)
    print(f"Viewing partition {target.name} (offset={address:#x}, size={length:#x})")
    assert loaded.esp is not None  # setup() connects by default
    buffer = loaded.esp.read_flash(offset=address, length=length)
    if output == 'str':
        print(buffer.decode('utf-8', errors='replace'), flush=True)
    elif output == 'hex':
        hexdump(buffer, address - target.offset, width)
    else:
        raise ValueError(f"Invalid output type {output}")


@cli.command('view', help="Pretty-print a partition's contents")
@click.argument('partition')
@click.option('-w', '--width', type=int, default=16, show_default=True, help='Width of the hexdump')
@click.option('--hex', 'output', flag_value='hex', default=True, help='Output as a hex dump')
@click.option('-s', '--string', 'output', flag_value='str', help='Output as a string')
@pass_state
def cmd_view(state: State, partition: str, width: int, output: str) -> None:
    return view_partition(state, partition, width, output)
