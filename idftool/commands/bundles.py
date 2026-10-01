"""Partition bundles (ZIP): ``create-bundle``, ``dump-bundle``, ``write-bundle``, ``print-bundle``.

The format itself is in :mod:`idftool.bundle`."""
import json
import os.path
import sys
import time
from zipfile import ZipFile

import rich_click as click

from esptool import flash_size_bytes
from esptool.cmds import detect_flash_size

from esp_idf_defs import ImageMetadata
from esp_idf_defs.partitions import BOOTLOADER_TYPE, PartitionTable

from idftool.apps import check_partition_image, parse_bootloader, print_app_info, \
    print_bootloader, print_partition_table_and_apps, validate_app_binary
from idftool.bundle import MANIFEST, ROLE_PREFIX, ROLES, BundleError, Manifest, check_bundle, \
    factory_target, is_ota_app, normalise_chip, nvs_target, read_bundle
from idftool.cli import cli, pass_state
from idftool.flash import flash_options, option_group, write_flash, write_flash_options
from idftool.params import HMAC_KEY
from idftool.partitions import get_partition

# Keep the pass-through write options in a panel of their own.
option_group('write-bundle', '--hmac-key')

def create_bundle(state, output_file, flash_partition_table, files, manifest_file=None):
    loaded = state.setup(needs_device=False)
    files = list(files)
    if len(files) % 2 != 0:
        raise ValueError("Files list must contain pairs of partition name and input file")

    with ZipFile(output_file, 'w') as zf:
        # Add specified partition files
        for partition_name, input_file in list(zip(files[::2], files[1::2])):
            input_file_size = os.path.getsize(input_file)
            if partition_name.startswith(ROLE_PREFIX):
                if partition_name[len(ROLE_PREFIX):] not in ROLES:
                    raise ValueError(f"Unknown role '{partition_name}' (expected @factory or @ota)")
                with open(input_file, 'rb') as f:
                    try:
                        ImageMetadata.from_bytes(f.read(), app_required=True)
                    except ValueError as e:
                        raise RuntimeError(f"Invalid application binary '{input_file}': {e}")
                zf.write(input_file, arcname=f"{partition_name}.bin")
                print(f"Adding file {input_file} (size={input_file_size:#x}) to bundle as {partition_name}.bin")
                continue
            partition = get_partition(
                loaded.partition_table, loaded.partition_table_entry, loaded.bootloader_entry, partition_name)
            if input_file_size > partition.size:
                raise ValueError(
                    f"Input file {input_file} size {input_file_size:#x} exceeds partition {partition.name} size {partition.size:#x}")
            zf.write(input_file, arcname=f"{partition.name}.bin")
            print(f"Adding file {input_file} (size={input_file_size:#x}) to bundle as partition {partition.name} (offset={partition.offset:#x}, size={partition.size:#x})")

        # Add partition table if requested
        if flash_partition_table:
            zf.writestr("partition_table.csv", loaded.partition_table.to_csv())
            print(f"Adding partition table CSV to bundle")

        # The manifest, and the files its ops name, relative to it.
        if manifest_file:
            with open(manifest_file, 'rb') as f:
                data = f.read()
            manifest = Manifest.from_json(json.loads(data))
            zf.writestr(MANIFEST, data)
            print(f"Adding {manifest_file} to bundle as {MANIFEST}")
            base = os.path.dirname(manifest_file)
            for name in dict.fromkeys(name for op in manifest.ops for name in op.files):
                if '/' not in name:
                    raise ValueError(f"'{name}' (named by {MANIFEST}) must be in a subdirectory, "
                                     f"e.g. files/{name}")
                zf.write(os.path.join(base, name), arcname=name)
                print(f"Adding file {name}")


@cli.command('create-bundle', help='Pack partition images into a ZIP bundle')
@click.option('-o', '--output', 'output_file', required=True, help='Output ZIP filename')
@click.option('--flash-partition-table', is_flag=True, help='Include the partition table in the bundle')
@click.option('--manifest', 'manifest_file', default=None,
              help='Add this manifest.json, and the files its ops name (relative to it)')
@click.argument('files', nargs=-1, required=True, metavar='PARTITION FILENAME ...')
@pass_state
def cmd_create_bundle(state, output_file, flash_partition_table, manifest_file, files):
    return create_bundle(state, output_file, flash_partition_table, files, manifest_file)


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
        # A table with its own bootloader row already dumped it above.
        bootloader = loaded.bootloader_entry
        if bootloader is not None and bootloader not in partition_table:
            print(f"Reading bootloader (offset={bootloader.offset:#x}, size={bootloader.size:#x})")
            zf.writestr("bootloader.bin", esp.read_flash(bootloader.offset, bootloader.size))
        zf.writestr("partition_table.csv", partition_table.to_csv())
        print(f"Adding partition table CSV to bundle")

    print(f"Bundle written to {output_file}")


@cli.command('dump-bundle', help='Pack every partition from the device into a ZIP')
@click.argument('output_file', required=False)
@pass_state
def cmd_dump_bundle(state, output_file):
    return dump_bundle(state, output_file)




def _read_bundle(state, input_file):
    return read_bundle(input_file, state.partition_table_offset, state.primary_bootloader_offset,
                       state.recovery_bootloader_offset)


def _choose_table(state, check) -> bool:
    """Whether to write the bundle's differing table (False keeps the device's)."""
    if not check.table_changes:
        return False
    print("The bundle's partition table differs from the device's:")
    for difference in check.differences:
        print(f"  {difference}")
    if check.policy == 'update':
        return True
    if check.policy == 'require':
        return False  # a layout that cannot be kept is refused before this
    if state.assume_yes:
        return True
    if not sys.stdin.isatty():
        raise BundleError("This bundle asks before changing the partition table; pass -y to "
                          "update it")
    import questionary
    choices = ["Update the partition table"]
    if check.can_keep_layout:
        choices.append("Keep the device's partition table")
    choices.append("Cancel")
    try:
        answer = questionary.select("Update the partition table?", choices=choices).unsafe_ask()
    except KeyboardInterrupt:
        raise click.Abort() from None
    if answer == "Cancel":
        raise click.Abort()
    return answer == choices[0]


def write_bundle(state, input_file, hmac_key=None, **options):
    """Flash a bundle: its table, bootloader, role app and named partitions, then its
    manifest's ops. Everything is checked against the device before the first write.
    `hmac_key` decrypts an encrypted NVS partition for ``set-nvs`` ops. Keyword arguments go to
    esptool's ``write_flash`` (see :data:`idftool.flash.WRITE_FLASH_OPTIONS`)."""
    from idftool.commands.firmware import boot_partition, clear_boot_partition, flash_factory, \
        flash_ota, ota_partition
    from idftool.commands.fs import edit_fs_partition
    from idftool.commands.nvs import check_nvs_key, edit_nvs_partition, manifest_edits

    esp = state.connect()
    bundle = _read_bundle(state, input_file)
    manifest = bundle.manifest
    print(f"Bundle: {bundle.name}" + (f" — {manifest.description}" if manifest and manifest.description else ""))
    for i, step in enumerate(bundle.outline(), 1):
        print(f"  {i}. {step}")

    if manifest and manifest.chip and manifest.chip != normalise_chip(esp.CHIP_NAME):
        raise BundleError(f"This bundle is for {manifest.chip}, but the connected device is "
                          f"an {esp.CHIP_NAME}")

    device_table = state.read_device_table()
    virtual = {e.name for e in state.virtual_entries(bundle.table or device_table or PartitionTable()) if e}
    check = check_bundle(bundle, device_table, virtual)
    if check.blocker:
        raise BundleError(check.blocker)
    update_table = _choose_table(state, check)
    table = bundle.table if bundle.table is not None and (update_table or not check.differences) \
        else device_table
    table = table if table is not None else PartitionTable()
    table_entry, bootloader_entry = state.virtual_entries(table)

    def fits(what, data, partition):
        if len(data) > partition.size:
            raise BundleError(f"{what} size {len(data):#x} exceeds partition {partition.name} "
                              f"size {partition.size:#x}")

    def resolve(name):
        return get_partition(table, table_entry, bootloader_entry, name)

    def read_file(name):
        if name not in bundle.files:
            raise BundleError(f"'{name}' is not in the bundle")
        return bundle.files[name]

    # Check every image before the first write.
    force = options.get('force')
    if bundle.bootloader is not None:
        if bootloader_entry is None:
            raise BundleError(f"The bootloader offset of {esp.CHIP_NAME} is not known")
        check_partition_image(esp, bootloader_entry, bundle.bootloader, "bootloader.bin", force)
        fits("bootloader.bin", bundle.bootloader, bootloader_entry)
    role_image = None
    if bundle.role:
        _, role_image = validate_app_binary(esp, bundle.role_app, bundle.role_file)
        if bundle.role == 'factory':
            fits(bundle.role_file, bundle.role_app, factory_target(table))
    named = []
    for name, data in bundle.partitions.items():
        partition = resolve(name)
        fits(f"{name}.bin", data, partition)
        check_partition_image(esp, partition, data, f"{name}.bin", force)
        named.append((partition, data))
    nvs_edits = {}
    for op in bundle.ops:
        if op.op in ('write', 'write-fs'):
            fits(op.file, bundle.files[op.file], resolve(op.partition))
            if op.op == 'write':
                check_partition_image(esp, resolve(op.partition), bundle.files[op.file], op.file, force)
        elif op.op == 'set-boot' and not is_ota_app(resolve(op.partition)):
            raise BundleError(f"manifest.json: op {op.index} (set-boot): "
                              f"'{op.partition}' is not an OTA app partition")
        elif op.op == 'set-nvs':
            try:
                nvs_edits[op.index] = manifest_edits(op.set, op.delete, read_file)
            except (click.UsageError, RuntimeError) as e:
                message = e.format_message() if isinstance(e, click.UsageError) else str(e)
                raise BundleError(f"manifest.json: op {op.index} (set-nvs): {message}") from e
            # Where the table stays, check now that the key fits the partition.
            if not (bundle.table is not None and update_table):
                check_nvs_key(esp, nvs_target(table, op.partition), hmac_key)

    write = write_flash_options(options, skip_flashed=True, diff=True)

    if bundle.table is not None:
        if not check.differences:
            print("Partition table already matches; not written")
        elif not update_table:
            print("Partition table differs only outside this bundle's partitions; the device's is kept")
        else:
            table.verify_size_fits(flash_size_bytes(detect_flash_size(esp)))
            print(f"Writing partition table (offset={table_entry.offset:#x}, size={table_entry.size:#x}) from bundle")
            write_flash(esp=esp, addr_data=[(table_entry.offset, table.to_binary())],
                        flash_size='detect', **write)

    if bundle.bootloader is not None:
        print(f"Writing bootloader (offset={bootloader_entry.offset:#x}, size={bootloader_entry.size:#x}) from bundle")
        write_flash(esp=esp, addr_data=[(bootloader_entry.offset, bundle.bootloader)],
                    flash_size='detect', **write)

    if bundle.role == 'factory':
        flash_factory(esp, table, factory_target(table), bundle.role_app, role_image, options)
    elif bundle.role == 'ota':
        partition = ota_partition(esp, table)
        fits(bundle.role_file, bundle.role_app, partition)
        flash_ota(esp, table, partition, bundle.role_app, role_image, options)

    if named:
        for partition, _ in named:
            print(f"Writing partition {partition.name} (offset={partition.offset:#x}, size={partition.size:#x}) from bundle")
        write_flash(esp=esp, addr_data=[(p.offset, data) for p, data in named],
                    flash_size='detect', **write)

    for op in bundle.ops:
        print(f"Op {op.index}: {op.describe()}")
        if op.op in ('write', 'write-fs'):
            partition = resolve(op.partition)
            write_flash(esp=esp, addr_data=[(partition.offset, bundle.files[op.file])],
                        flash_size='detect', **write)
        elif op.op == 'erase':
            partition = resolve(op.partition)
            esp.erase_region(offset=partition.offset, size=partition.size)
        elif op.op == 'edit-fs':
            edit_fs_partition(esp, resolve(op.partition),
                              {path: bundle.files[name] for path, name in op.put.items()},
                              op.delete, options)
        elif op.op == 'set-nvs':
            edit_nvs_partition(esp, nvs_target(table, op.partition), nvs_edits[op.index],
                               options, read_file, hmac_key)
        elif op.op == 'set-boot':
            boot_partition(esp, table, op.partition)
        elif op.op == 'clear-boot':
            clear_boot_partition(esp, table)


@cli.command('write-bundle', help='Flash a bundle ZIP')
@click.argument('input_file')
@click.option('--hmac-key', type=HMAC_KEY, default=None,
              help="HMAC key of an encrypted NVS partition the manifest's set-nvs edits")
@flash_options
@pass_state
def cmd_write_bundle(state, input_file, hmac_key, **options):
    return write_bundle(state, input_file, hmac_key, **options)


def print_bundle(state, bundle_file):
    bundle = _read_bundle(state, bundle_file)
    manifest = bundle.manifest
    print(f"Bundle: {bundle_file} ({os.path.getsize(bundle_file):#x} bytes)")
    if manifest:
        print(f"Name: {bundle.name}")
        if manifest.description:
            print(f"Description: {manifest.description}")
        if manifest.chip:
            print(f"Chip: {manifest.chip}")
    if bundle.table is not None:
        policy = f" (table: {manifest.table}, tableMatch: {manifest.table_match})" if manifest else ""
        print(f"Partition table: {bundle.table_file}{policy}")
    else:
        print("Partition table: none, uses the device's")
    print("Steps:")
    for i, step in enumerate(bundle.outline(), 1):
        print(f"  {i}. {step}")
    included = sorted(bundle.partitions)
    print(f"Partitions included: {', '.join(included) if included else '(none)'}")
    print()

    table = bundle.table
    # A table with its own bootloader row names where it goes; otherwise it's the chip's.
    row = next((p for p in table or () if p.type == BOOTLOADER_TYPE), None)
    data = bundle.partitions.get(row.name, b'') if row else bundle.bootloader or b''
    bootloader = parse_bootloader(data, row.offset if row else None)

    if table is not None:
        def read(offset: int, length: int) -> bytes:
            for part in table:
                if part.offset <= offset < part.offset + part.size:
                    local = offset - part.offset
                    chunk = bundle.partitions.get(part.name, b"")[local:local + length]
                    return chunk + b"\xff" * (length - len(chunk))
            return b"\xff" * length

        print_partition_table_and_apps(table, read, bootloader)
    else:
        print_bootloader(bootloader)

    if bundle.role:
        try:
            image = ImageMetadata.from_bytes(bundle.role_app, app_required=True)
        except ValueError as e:
            raise BundleError(f"{bundle.role_file} is not a valid application binary: {e}")
        print()
        print(f"{bundle.role_file}:")
        print_app_info(image.app_description, image.header, indent="  ")


@cli.command('print-bundle', help='Print what a bundle ZIP holds and what flashing it does')
@click.option('-f', '--file', 'bundle_file', required=True, help='Bundle ZIP to read')
@pass_state
def cmd_print_bundle(state, bundle_file):
    return print_bundle(state, bundle_file)
