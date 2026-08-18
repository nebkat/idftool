"""Firmware and boot selection: ``factory``, ``ota``, and the ``*-boot`` commands."""
import rich_click as click

from esptool.cmds import write_flash

from esp_idf_defs.otadata import OtaImageState
from esp_idf_defs.partitions import APP_TYPE, DATA_TYPE, SUBTYPES

from idftool.apps import load_app_binary
from idftool.cli import cli, pass_state
from idftool.partitions import read_otadata, write_otadata

def factory(state, app_binary_file):
    loaded = state.setup()
    esp, partition_table = loaded.esp, loaded.partition_table
    partition = next(
        (part for part in partition_table if part.type == APP_TYPE and part.subtype == SUBTYPES[APP_TYPE]['factory']),
        None
    )
    if not partition:
        partition = next(
            (part for part in partition_table if part.type == APP_TYPE and part.subtype == SUBTYPES[APP_TYPE]['ota_0']),
            None
        )
    if not partition:
        raise ValueError("No factory or OTA partition found")

    app_binary, image_metadata = load_app_binary(esp, app_binary_file, partition)

    print(f"Writing '{image_metadata.app_description.title}' to partition '{partition.name}'...")
    write_flash(esp=esp, addr_data=[(partition.offset, app_binary)])

    otadata_partition = next(
        (p for p in partition_table if p.type == DATA_TYPE and p.subtype == SUBTYPES[DATA_TYPE]['ota']),
        None
    )
    if otadata_partition:
        print("Erasing 'otadata' partition...")
        esp.erase_region(offset=otadata_partition.offset, size=otadata_partition.size)


@cli.command('factory', help='Flash an app to the factory partition')
@click.argument('app_binary_file')
@pass_state
def cmd_factory(state, app_binary_file):
    return factory(state, app_binary_file)


def ota(state, app_binary_file):
    loaded = state.setup()
    esp, partition_table = loaded.esp, loaded.partition_table
    otadata_partition, otadata = read_otadata(esp, partition_table)

    next_slot = otadata.next_slot
    partition = next(
        (p for p in partition_table if p.type == APP_TYPE and p.subtype == SUBTYPES[APP_TYPE]['ota_0'] + next_slot),
        None
    )
    if not partition:
        raise ValueError(f"Partition ota_{next_slot} not found")

    app_binary, image_metadata = load_app_binary(esp, app_binary_file, partition)

    print(f"Writing '{image_metadata.app_description.title}' to partition '{partition.name}'...")
    write_flash(esp=esp, addr_data=[(partition.offset, app_binary)])

    print(f"Setting boot partition to 'ota_{next_slot}'...")
    otadata = otadata.incremented_and_swapped(next_slot)
    write_otadata(esp, otadata_partition, otadata)


@cli.command('ota', help='Push an app to the next OTA slot and switch to it')
@click.argument('app_binary_file')
@pass_state
def cmd_ota(state, app_binary_file):
    return ota(state, app_binary_file)


def get_boot(state):
    loaded = state.setup()
    _, otadata = read_otadata(loaded.esp, loaded.partition_table)

    if otadata.slot is None:
        print("OTA slot not set")
    else:
        print(f"OTA slot 'ota_{otadata.slot}' (seq={otadata.otadata.seq}, state={otadata.otadata.ota_state.name})")


@cli.command('get-boot', help='Show the currently-active OTA slot')
@pass_state
def cmd_get_boot(state):
    return get_boot(state)


def set_boot(state, partition):
    loaded = state.setup()
    otadata_partition, otadata = read_otadata(loaded.esp, loaded.partition_table)

    label = partition
    partition = loaded.partition_table[label]
    if partition.type != APP_TYPE:
        raise ValueError(f"Partition {label} is not an app partition")
    if partition.subtype < SUBTYPES[APP_TYPE]['ota_0'] or partition.subtype > SUBTYPES[APP_TYPE]['ota_15']:
        raise ValueError(f"Partition {label} is not an OTA partition")
    ota_slot = partition.subtype & 0x0F

    print(f"Setting boot partition to '{partition.name}'...")
    otadata = otadata.incremented_and_swapped(ota_slot)
    otadata.otadata.ota_state = OtaImageState.VALID
    write_otadata(loaded.esp, otadata_partition, otadata)


@cli.command('set-boot', help='Force the next boot to a specific OTA partition')
@click.argument('partition')
@pass_state
def cmd_set_boot(state, partition):
    return set_boot(state, partition)


def clear_boot(state):
    loaded = state.setup()
    otadata_partition = next(
        (p for p in loaded.partition_table if p.type == DATA_TYPE and p.subtype == SUBTYPES[DATA_TYPE]['ota']),
        None
    )
    if not otadata_partition:
        raise ValueError("No otadata partition found") # TODO Better error type

    print("Clearing boot partition...")
    loaded.esp.erase_region(offset=otadata_partition.offset, size=otadata_partition.size)


@cli.command('clear-boot', help='Erase otadata and let the bootloader fall back')
@pass_state
def cmd_clear_boot(state):
    return clear_boot(state)
