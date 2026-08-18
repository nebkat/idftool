"""Filesystem images: ``create-fs``, ``write-fs``, ``read-fs``, ``extract-fs``, ``print-fs``.

The filesystem formats themselves live in :mod:`idftool.fs`, which is imported lazily
inside each command: the backends pull in pyfatfs and littlefs-python (a C extension),
worth ~50 ms of import time that no other command should have to pay.
"""
import os.path
import sys

import rich_click as click

from esptool.cmds import write_flash

from idftool.cli import cli, pass_state
from idftool.params import BASED_INT
from idftool.partitions import get_partition

FS_TYPE_CHOICE = click.Choice(['fatfs', 'littlefs', 'spiffs'])

# Keep the per-filesystem knobs in their own panels so they don't bury the handful of options
# a command actually needs. Keyed with a wildcard because the program name differs between
# `idftool ...` and `python -m idftool ...`.
for _fs_command in ('create-fs', 'write-fs', 'read-fs', 'extract-fs', 'print-fs', 'list-fs'):
    click.rich_click.OPTION_GROUPS[f'* {_fs_command}'] = [
        # Long names only: listing a short alias too renders the option twice.
        {'name': 'Options', 'options': ['--output', '--type', '--size', '--partition', '--help']},
        {'name': 'FAT options', 'options': [
            '--fat-sector-size', '--fat-sectors-per-cluster', '--fat-type',
            '--fat-wear-levelling', '--fat-volume-id', '--fat-device-id']},
        {'name': 'littlefs options', 'options': [
            '--littlefs-block-size', '--littlefs-name-max', '--littlefs-disk-version']},
        {'name': 'SPIFFS options', 'options': [
            '--spiffs-page-size', '--spiffs-block-size', '--spiffs-obj-name-len',
            '--spiffs-meta-len', '--spiffs-use-magic', '--spiffs-use-magic-len']},
    ]


def fs_options(f):
    """Attach the per-filesystem tuning options shared by every fs command.

    Each defaults to None so the backend's own default — chosen to match ESP-IDF's
    Kconfig defaults — applies; set them when the device's sdkconfig differs.
    """
    options = [
        click.option('--fat-sector-size', type=BASED_INT, default=None,
                     help='FAT sector size (CONFIG_WL_SECTOR_SIZE)  [default: 0x1000]'),
        click.option('--fat-sectors-per-cluster', type=BASED_INT, default=None,
                     help='FAT sectors per cluster  [default: 1]'),
        click.option('--fat-type', type=click.Choice(['12', '16']), default=None,
                     help='Force FAT12 or FAT16 instead of deriving it from the cluster count'),
        click.option('--fat-wear-levelling/--no-fat-wear-levelling', default=None,
                     help='Wrap the FAT image in a wear levelling container '
                          '[default: on for writes, auto-detected for reads]'),
        click.option('--fat-volume-id', type=BASED_INT, default=None,
                     help='FAT volume serial number  [default: random]'),
        click.option('--fat-device-id', type=BASED_INT, default=None,
                     help='Wear levelling device id  [default: random]'),
        click.option('--littlefs-block-size', type=BASED_INT, default=None,
                     help='littlefs block size  [default: 0x1000]'),
        click.option('--littlefs-name-max', type=BASED_INT, default=None,
                     help='littlefs name limit (CONFIG_LITTLEFS_OBJ_NAME_LEN)  [default: 64]'),
        click.option('--littlefs-disk-version', type=BASED_INT, default=None,
                     help='littlefs on-disk version, e.g. 0x20000 for 2.0  [default: newest]'),
        click.option('--spiffs-page-size', type=BASED_INT, default=None,
                     help='SPIFFS page size (CONFIG_SPIFFS_PAGE_SIZE)  [default: 0x100]'),
        click.option('--spiffs-block-size', type=BASED_INT, default=None,
                     help='SPIFFS block size (the flash sector size)  [default: 0x1000]'),
        click.option('--spiffs-obj-name-len', type=BASED_INT, default=None,
                     help='SPIFFS name limit (CONFIG_SPIFFS_OBJ_NAME_LEN)  [default: 32]'),
        click.option('--spiffs-meta-len', type=BASED_INT, default=None,
                     help='SPIFFS metadata length (CONFIG_SPIFFS_META_LENGTH)  [default: 4]'),
        click.option('--spiffs-use-magic/--no-spiffs-use-magic', default=None,
                     help='SPIFFS volume magic (CONFIG_SPIFFS_USE_MAGIC)  [default: on]'),
        click.option('--spiffs-use-magic-len/--no-spiffs-use-magic-len', default=None,
                     help='Per-block SPIFFS magic (CONFIG_SPIFFS_USE_MAGIC_LENGTH)  [default: on]'),
    ]
    for option in reversed(options):
        f = option(f)
    return f


def _fs_options_for(fs_type, options):
    """Narrow a command's raw options down to the ones the chosen filesystem understands."""
    import idftool.fs as fs
    resolved = fs.backend_options(fs_type, options)
    if 'fat_type' in resolved:
        resolved['fat_type'] = int(resolved['fat_type'])
    return resolved


def _fs_partition(loaded, name):
    return get_partition(
        loaded.partition_table, loaded.partition_table_entry, loaded.bootloader_entry, name)


def create_fs(state, source, output_file, fs_type, size, partition, **options):
    import idftool.fs as fs
    if (size is None) == (partition is None):
        raise click.UsageError("Provide exactly one of --size or --partition")

    part = None
    if partition is not None:
        loaded = state.setup(needs_device=False)
        part = _fs_partition(loaded, partition)
        size = part.size

    fs_type = fs.resolve_type(fs_type, partition=part, what=f"'{source}'")
    opts = _fs_options_for(fs_type, options)
    print(f"Creating {fs_type} image from '{source}' (size={size:#x})...")
    print(f"  {fs.describe(fs_type, size, **opts)}")

    image = fs.create(fs_type, source, size, **opts)
    with open(output_file, 'wb') as f:
        f.write(image)
    with fs.mount(fs_type, image, **opts) as volume:
        print(fs.format_listing(volume.entries()))
    print(f"Wrote {len(image):#x} bytes to '{output_file}'")


@cli.command('create-fs', help='Build a filesystem image from a directory')
@click.argument('source')
@click.option('-o', '--output', 'output_file', required=True, help='Output image filename (.bin)')
@click.option('-t', '--type', 'fs_type', type=FS_TYPE_CHOICE, default=None,
              help='Filesystem to build  [default: from --partition, else required]')
@click.option('--size', type=BASED_INT, default=None, help='Image size in bytes (e.g. 0x100000)')
@click.option('--partition', default=None, help='Partition name to take the size and type from')
@fs_options
@pass_state
def cmd_create_fs(state, source, output_file, fs_type, size, partition, **options):
    return create_fs(state, source, output_file, fs_type, size, partition, **options)


def write_fs(state, partition, source, fs_type, **options):
    import idftool.fs as fs
    loaded = state.setup()
    part = _fs_partition(loaded, partition)
    fs_type = fs.resolve_type(fs_type, partition=part, what=f"'{source}'")
    opts = _fs_options_for(fs_type, options)

    if os.path.isdir(source):
        print(f"Creating {fs_type} image from '{source}' for partition '{part.name}' (size={part.size:#x})...")
        print(f"  {fs.describe(fs_type, part.size, **opts)}")
        image = fs.create(fs_type, source, part.size, **opts)
    else:
        # A single file is ambiguous: it is either an already-built image or content to put
        # in a one-file filesystem. Sniff it, the same way write-nvs handles a prebuilt NVS
        # binary, and fall back to building.
        data = open(source, 'rb').read()
        if fs.detect(data) == fs_type:
            if len(data) > part.size:
                raise ValueError(f"Image '{source}' size {len(data):#x} exceeds partition "
                                 f"{part.name} size {part.size:#x}")
            print(f"Using {fs_type} image '{source}' for partition '{part.name}' (size={part.size:#x})...")
            if len(data) < part.size:
                # Deliberately not padded: a wear-levelled FAT image keeps its metadata in the
                # last sectors and records its own size, so padding it out would corrupt it.
                print(f"Warning: image is {len(data):#x} bytes, smaller than partition "
                      f"'{part.name}' ({part.size:#x}) — it was built for a different size and "
                      f"may not mount", file=sys.stderr)
            image = data
        else:
            print(f"Creating {fs_type} image containing '{source}' for partition "
                  f"'{part.name}' (size={part.size:#x})...")
            image = fs.create(fs_type, source, part.size, **opts)

    with fs.mount(fs_type, image, **opts) as volume:
        print(fs.format_listing(volume.entries()))
    print(f"Writing {fs_type} image to partition '{part.name}' "
          f"(offset={part.offset:#x}, size={part.size:#x})")
    write_flash(esp=loaded.esp, addr_data=[(part.offset, image)], flash_size='detect')


@cli.command('write-fs', help='Build a filesystem image from a directory and flash it')
@click.argument('partition')
@click.argument('source')
@click.option('-t', '--type', 'fs_type', type=FS_TYPE_CHOICE, default=None,
              help='Filesystem to build  [default: from the partition subtype]')
@fs_options
@pass_state
def cmd_write_fs(state, partition, source, fs_type, **options):
    return write_fs(state, partition, source, fs_type, **options)


def read_fs(state, partition, destination, fs_type, **options):
    import idftool.fs as fs
    loaded = state.setup()
    part = _fs_partition(loaded, partition)
    print(f"Reading partition {part.name} (offset={part.offset:#x}, size={part.size:#x})")
    image = loaded.esp.read_flash(part.offset, part.size)

    fs_type = fs.resolve_type(fs_type, partition=part, image=image,
                              what=f"the contents of partition '{part.name}'")
    opts = _fs_options_for(fs_type, options)
    with fs.mount(fs_type, image, **opts) as volume:
        entries = fs.extract(volume, destination)
    print(fs.format_listing(entries))
    print(f"Extracted {fs_type} partition '{part.name}' to '{destination}'")


@cli.command('read-fs', help='Read a filesystem partition and extract it to a directory')
@click.argument('partition')
@click.argument('destination')
@click.option('-t', '--type', 'fs_type', type=FS_TYPE_CHOICE, default=None,
              help='Filesystem to read  [default: from the partition subtype, else detected]')
@fs_options
@pass_state
def cmd_read_fs(state, partition, destination, fs_type, **options):
    return read_fs(state, partition, destination, fs_type, **options)


def extract_fs(state, image_file, destination, fs_type, **options):
    import idftool.fs as fs
    image = open(image_file, 'rb').read()
    fs_type = fs.resolve_type(fs_type, image=image, what=f"'{image_file}'")
    opts = _fs_options_for(fs_type, options)
    with fs.mount(fs_type, image, **opts) as volume:
        entries = fs.extract(volume, destination)
    print(fs.format_listing(entries))
    print(f"Extracted {fs_type} image '{image_file}' to '{destination}'")


@cli.command('extract-fs', help='Extract a filesystem image file to a directory')
@click.argument('image_file')
@click.argument('destination')
@click.option('-t', '--type', 'fs_type', type=FS_TYPE_CHOICE, default=None,
              help='Filesystem to read  [default: detected from the image]')
@fs_options
@pass_state
def cmd_extract_fs(state, image_file, destination, fs_type, **options):
    return extract_fs(state, image_file, destination, fs_type, **options)


def print_fs(state, image_file, partition, fs_type, **options):
    import idftool.fs as fs
    if (image_file is None) == (partition is None):
        raise click.UsageError("Provide exactly one of an image file or --partition")

    part = None
    if image_file is not None:
        image = open(image_file, 'rb').read()
        source = f"'{image_file}'"
    else:
        loaded = state.setup()
        part = _fs_partition(loaded, partition)
        print(f"Reading partition {part.name} (offset={part.offset:#x}, size={part.size:#x})")
        image = loaded.esp.read_flash(part.offset, part.size)
        source = f"partition '{part.name}'"

    fs_type = fs.resolve_type(fs_type, partition=part, image=image, what=source)
    opts = _fs_options_for(fs_type, options)
    print(f"{source[0].upper()}{source[1:]}: {fs_type}, {len(image):#x} bytes")
    with fs.mount(fs_type, image, **opts) as volume:
        print(fs.format_listing(volume.entries()))


@cli.command('print-fs', aliases=['list-fs'], help='List the contents of a filesystem image or partition')
@click.argument('image_file', required=False)
@click.option('--partition', default=None, help='Read the filesystem from this partition on the device')
@click.option('-t', '--type', 'fs_type', type=FS_TYPE_CHOICE, default=None,
              help='Filesystem to read  [default: from the partition subtype, else detected]')
@fs_options
@pass_state
def cmd_print_fs(state, image_file, partition, fs_type, **options):
    return print_fs(state, image_file, partition, fs_type, **options)
