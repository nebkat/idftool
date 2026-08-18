"""NVS provisioning: ``create-nvs`` and ``write-nvs``."""
import rich_click as click

from esptool.cmds import write_flash

from idftool.cli import cli, pass_state
from idftool.nvs import fit_nvs_binary, generate_nvs_image, looks_like_nvs_binary
from idftool.params import BASED_INT
from idftool.partitions import get_partition

def create_nvs(state, csv_file, output_file, size, partition):
    if (size is None) == (partition is None):
        raise click.UsageError("Provide exactly one of --size or --partition")
    if partition is not None:
        loaded = state.setup(needs_device=False)
        size = get_partition(
            loaded.partition_table, loaded.partition_table_entry, loaded.bootloader_entry, partition).size

    data = open(csv_file, 'rb').read()
    if looks_like_nvs_binary(data):
        print(f"Using NVS binary '{csv_file}' (size={size:#x})...")
        image = fit_nvs_binary(data, size)
    else:
        print(f"Generating NVS image from '{csv_file}' (size={size:#x})...")
        image = generate_nvs_image(csv_file, size)
    with open(output_file, 'wb') as f:
        f.write(image)
    print(f"Wrote {len(image):#x} bytes to '{output_file}'")


@cli.command('create-nvs', help='Generate an NVS partition image from a CSV file')
@click.argument('csv_file')
@click.option('-o', '--output', 'output_file', required=True, help='Output binary filename (.bin)')
@click.option('--size', type=BASED_INT, default=None, help='Partition size in bytes (e.g. 0x6000)')
@click.option('--partition', default=None, help='Partition name to read the size from the partition table')
@pass_state
def cmd_create_nvs(state, csv_file, output_file, size, partition):
    return create_nvs(state, csv_file, output_file, size, partition)


def write_nvs(state, partition, csv_file):
    loaded = state.setup()
    partition = get_partition(
        loaded.partition_table, loaded.partition_table_entry, loaded.bootloader_entry, partition)
    data = open(csv_file, 'rb').read()
    if looks_like_nvs_binary(data):
        print(f"Using NVS binary '{csv_file}' for partition '{partition.name}' (size={partition.size:#x})...")
        image = fit_nvs_binary(data, partition.size)
    else:
        print(f"Generating NVS image from '{csv_file}' for partition '{partition.name}' (size={partition.size:#x})...")
        image = generate_nvs_image(csv_file, partition.size)
    print(f"Writing NVS image to partition '{partition.name}' (offset={partition.offset:#x}, size={partition.size:#x})")
    write_flash(esp=loaded.esp, addr_data=[(partition.offset, image)], flash_size='detect')


@cli.command('write-nvs', help='Generate an NVS image from CSV and flash it')
@click.argument('partition')
@click.argument('csv_file')
@pass_state
def cmd_write_nvs(state, partition, csv_file):
    return write_nvs(state, partition, csv_file)
