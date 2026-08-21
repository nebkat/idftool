"""The ``idftool`` command group: global options, help layout, and the connection lifecycle.

Command modules import ``cli`` and ``pass_state`` from here and register themselves by
being imported (see :mod:`idftool.commands`)."""
import rich_click as click

from esptool import ESPLoader

from esp_idf_defs.partitions import PARTITION_TABLE_SIZE, PARTITION_TABLE_OFFSET

from idftool.params import BASED_INT, BOOTLOADER_OFFSET
from idftool.state import State

pass_state = click.make_pass_decorator(State)


def reject_file_as_partition(name, command):
    """Catch an image file passed where a partition name belongs.

    Commands that can read either a partition or a file take the partition as the positional
    and the file behind ``-f/--file`` (matching ``read``/``write``/``erase``), so a path in
    that slot is a common slip. Checked before connecting, or the user waits for a serial
    timeout to be told they had the arguments the wrong way round.

    Only fires on something that actually looks like a path — a real file whose name has an
    extension or a directory separator. A partition genuinely named ``storage`` is left alone
    even if a file by that name happens to sit in the working directory.
    """
    import os.path

    if name is None or not os.path.isfile(name):
        return
    if '.' not in os.path.basename(name) and os.sep not in name:
        return
    raise click.UsageError(
        f"'{name}' is a file, not a partition name — use "
        f"`idftool {command} -f {name}`, or name the partition to read from the device")

CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])

# esptool-style help: rich-click already renders options/commands in boxed panels; group the
# commands into labelled panels (matching the README) instead of one long list. Command aliases
# (reflash, list) are declared natively via `aliases=` on the commands and shown by rich-click.
# Show the alias in the same column as the name ("name_with_aliases") rather than a separate
# column, which otherwise squeezes the name column down to an ellipsis.
click.rich_click.COMMANDS_TABLE_COLUMN_TYPES = ['name_with_aliases', 'help']
click.rich_click.COMMAND_GROUPS = {
    '*': [
        {'name': 'Discovery', 'commands': ['devices']},
        {'name': 'Partition I/O', 'commands': ['read', 'write', 'erase', 'view']},
        {'name': 'Firmware', 'commands': ['ota', 'factory']},
        {'name': 'Boot selection', 'commands': ['get-boot', 'set-boot', 'clear-boot']},
        {'name': 'Images', 'commands': ['create-image', 'dump-image', 'write-image', 'print-image']},
        {'name': 'Bundles', 'commands': ['create-bundle', 'dump-bundle', 'write-bundle', 'print-bundle']},
        {'name': 'Partition table', 'commands': ['print-table', 'create-table', 'dump-table', 'write-table']},
        {'name': 'NVS', 'commands': ['create-nvs', 'write-nvs', 'read-nvs', 'extract-nvs',
                                     'print-nvs', 'get-nvs', 'set-nvs']},
        {'name': 'Filesystems', 'commands': ['create-fs', 'write-fs', 'read-fs',
                                             'extract-fs', 'print-fs']},
        {'name': 'Misc', 'commands': ['enter-bootloader']},
    ],
}


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option('-p', '--port', default=None, help='Serial port device')
@click.option('-b', '--baud', type=int, default=ESPLoader.ESP_ROM_BAUD, show_default=True, help='Serial port baud rate')
@click.option('--no-reset', is_flag=True, help='Do not reset the chip after operations')
@click.option('--partition-table-file', default=None,
              help='Path to a partition table CSV or binary file to use instead of reading from the device')
@click.option('--partition-table-offset', type=BASED_INT, default=PARTITION_TABLE_OFFSET, show_default=True,
              help='Partition table offset (where to read the table from flash, or place it when loading from CSV)')
@click.option('--partition-table-size', type=BASED_INT, default=PARTITION_TABLE_SIZE, show_default=True,
              help='Partition table size')
@click.option('--primary-bootloader-offset', type=BOOTLOADER_OFFSET, default=None,
              help='Primary bootloader offset or chip type e.g. esp32s3 (used when loading a table from CSV)')
@click.option('--recovery-bootloader-offset', type=BASED_INT, default=None,
              help='Recovery bootloader offset (used when loading a table from CSV)')
@click.pass_context
def cli(ctx, port, baud, no_reset, partition_table_file, partition_table_offset,
        partition_table_size, primary_bootloader_offset, recovery_bootloader_offset):
    """Utility for flashing, provisioning, and interacting with Espressif SOCs running ESP-IDF."""
    ctx.obj = State(
        port=port,
        baud=baud,
        no_reset=no_reset,
        partition_table_file=partition_table_file,
        partition_table_offset=partition_table_offset,
        partition_table_size=partition_table_size,
        primary_bootloader_offset=primary_bootloader_offset,
        recovery_bootloader_offset=recovery_bootloader_offset,
    )


@cli.result_callback()
@pass_state
def _reset_after_command(state, result, **_global_options):
    # Runs only after a command succeeds (a command that raises propagates past this), so the chip
    # is reset only on success. Offline commands never connect, so state.esp stays None.
    if state.esp is not None and not state.no_reset:
        state.esp.hard_reset()
