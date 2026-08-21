"""NVS provisioning: ``create-nvs``, ``write-nvs``, ``print-nvs``, ``extract-nvs``,
``read-nvs``, ``get-nvs`` and ``set-nvs``.

The image format itself lives in :mod:`idftool.nvs`, which is imported lazily by the commands
that parse an image so the generate-only paths don't pay for it.
"""
import os.path
import sys

import rich_click as click

from esptool.cmds import write_flash

from idftool.cli import cli, pass_state, reject_file_as_partition
from idftool.flash import flash_options, option_group, write_flash_options
from idftool.nvs import fit_nvs_binary, generate_nvs_image, looks_like_nvs_binary
from idftool.params import BASED_INT
from idftool.partitions import get_partition

# Keep the pass-through write options in a panel of their own.
option_group('write-nvs')
option_group('set-nvs', '--file', '--delete', '--namespace', '--output', '--rewrite',
             '--dry-run')


def _nvs_partition(loaded, name):
    try:
        return get_partition(
            loaded.partition_table, loaded.partition_table_entry, loaded.bootloader_entry, name)
    except ValueError as e:
        # A name that is a real file on disk is almost always someone reaching for --file.
        if os.path.exists(name):
            raise click.UsageError(f"{e} — '{name}' is a file, so you probably want "
                                   f"-f/--file {name}")
        raise


def create_nvs(state, csv_file, output_file, size, partition):
    if (size is None) == (partition is None):
        raise click.UsageError("Provide exactly one of --size or --partition")
    if partition is not None:
        loaded = state.setup(needs_device=False)
        size = _nvs_partition(loaded, partition).size

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


def write_nvs(state, partition, csv_file, **options):
    """Generate an NVS image from CSV (or take a prebuilt one) and flash it. Keyword
    arguments go to esptool's ``write_flash`` (see
    :data:`idftool.flash.WRITE_FLASH_OPTIONS`)."""
    loaded = state.setup()
    partition = _nvs_partition(loaded, partition)
    data = open(csv_file, 'rb').read()
    if looks_like_nvs_binary(data):
        print(f"Using NVS binary '{csv_file}' for partition '{partition.name}' (size={partition.size:#x})...")
        image = fit_nvs_binary(data, partition.size)
    else:
        print(f"Generating NVS image from '{csv_file}' for partition '{partition.name}' (size={partition.size:#x})...")
        image = generate_nvs_image(csv_file, partition.size)
    print(f"Writing NVS image to partition '{partition.name}' (offset={partition.offset:#x}, size={partition.size:#x})")
    write_flash(esp=loaded.esp, addr_data=[(partition.offset, image)], flash_size='detect',
                **write_flash_options(options))


@cli.command('write-nvs', help='Generate an NVS image from CSV and flash it')
@click.argument('partition')
@click.argument('csv_file')
@flash_options
@pass_state
def cmd_write_nvs(state, partition, csv_file, **options):
    return write_nvs(state, partition, csv_file, **options)


# --------------------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------------------

def _load_image(state, partition, image_file, command='print-nvs'):
    """Get an NVS image off the device or from a file, whichever the command was given.

    Returns ``(image bytes, partition or None, description)``.
    """
    if (image_file is None) == (partition is None):
        raise click.UsageError("Provide exactly one of a partition name or --file")
    reject_file_as_partition(partition, command)

    if image_file is not None:
        data = open(image_file, 'rb').read()
        if not looks_like_nvs_binary(data):
            print(f"Warning: '{image_file}' does not look like an NVS image", file=sys.stderr)
        return data, None, f"'{image_file}'"

    loaded = state.setup()
    part = _nvs_partition(loaded, partition)
    print(f"Reading partition {part.name} (offset={part.offset:#x}, size={part.size:#x})")
    return loaded.esp.read_flash(part.offset, part.size), part, f"partition '{part.name}'"


def _report_errors(image):
    for error in image.errors:
        print(f"Warning: {error}", file=sys.stderr)


def print_nvs(state, partition, image_file, pages):
    import idftool.nvs as nvs
    data, _, source = _load_image(state, partition, image_file)
    image = nvs.parse(data)
    _report_errors(image)
    print(f"{source[0].upper()}{source[1:]}: NVS version {2 if image.version == 0xFE else 1}, "
          f"{image.size:#x} bytes")
    if pages:
        print(nvs.format_pages(image))
        print()
    print(nvs.format_entries(image.entries))


@cli.command('print-nvs', aliases=['list-nvs'],
             help='List the contents of an NVS partition or image')
@click.argument('partition', required=False)
@click.option('-f', '--file', 'image_file', default=None,
              help='Read the NVS image from this file instead of the device')
@click.option('--pages', is_flag=True, help='Also show the page map (state, sequence, how full)')
@pass_state
def cmd_print_nvs(state, partition, image_file, pages):
    return print_nvs(state, partition, image_file, pages)


def extract_nvs(state, image_file, csv_file):
    """Dump an NVS image file to CSV. The device-side equivalent is :func:`read_nvs`."""
    import idftool.nvs as nvs
    data, _, source = _load_image(state, None, image_file)
    image = nvs.parse(data)
    _report_errors(image)
    text = nvs.to_csv(image.entries)
    with open(csv_file, 'w', encoding='utf-8') as f:
        f.write(text)
    print(nvs.format_entries(image.entries))
    print(f"Extracted {source} to '{csv_file}'")


@cli.command('extract-nvs', help='Extract an NVS image file to a CSV file')
@click.option('-f', '--file', 'image_file', required=True, help='NVS image file to extract')
@click.argument('csv_file')
@pass_state
def cmd_extract_nvs(state, image_file, csv_file):
    return extract_nvs(state, image_file, csv_file)


def read_nvs(state, partition, csv_file):
    """Read an NVS partition off the device and dump it to CSV."""
    import idftool.nvs as nvs
    data, _, source = _load_image(state, partition, None, 'read-nvs')
    image = nvs.parse(data)
    _report_errors(image)
    with open(csv_file, 'w', encoding='utf-8') as f:
        f.write(nvs.to_csv(image.entries))
    print(nvs.format_entries(image.entries))
    print(f"Extracted {source} to '{csv_file}'")


@cli.command('read-nvs', help='Read an NVS partition from the device and extract it to CSV')
@click.argument('partition')
@click.argument('csv_file')
@pass_state
def cmd_read_nvs(state, partition, csv_file):
    return read_nvs(state, partition, csv_file)


# --------------------------------------------------------------------------------------
# Editing
# --------------------------------------------------------------------------------------

SPEC_HELP = """\
A spec is `namespace:key=value`, or `namespace:key:type=value` to give the type explicitly.
With `--namespace` set, `key=value` and `:key:type=value` work too — a leading colon means
"the default namespace". Two colon-separated parts are read as namespace and key; if the
second one also names a type the spec is rejected rather than guessed at."""


def _parse_value(type_name, text):
    """Turn the text after the ``=`` into the value the entry will hold."""
    from idftool.nvs import NvsError

    if text.startswith('@'):
        path = text[1:]
        try:
            raw = open(path, 'rb').read()
        except OSError as e:
            raise click.UsageError(f"Cannot read value file '{path}': {e}")
        if type_name == 'blob':
            return raw
        if type_name == 'string':
            return raw.decode('utf-8')
        text = raw.decode('utf-8').strip()

    if type_name == 'string':
        return text
    if type_name == 'blob':
        cleaned = ''.join(text.split())
        try:
            return bytes.fromhex(cleaned)
        except ValueError as e:
            raise click.UsageError(
                f"Blob value must be hex (or @file): {e}")
    try:
        return int(text, 0)
    except ValueError:
        raise NvsError(f"'{text}' is not a valid {type_name} value")


def _parse_set(spec, default_namespace):
    """Parse a ``--set`` spec into an Edit."""
    from idftool.nvs import PRIMITIVES, NvsError
    from idftool.nvs.edit import Edit

    if '=' not in spec:
        raise click.UsageError(f"'{spec}' has no '='.\n{SPEC_HELP}")
    target, text = spec.split('=', 1)
    parts = target.split(':')

    known = list(PRIMITIVES) + ['string', 'blob']

    if len(parts) == 1:
        namespace, key, type_name = default_namespace, parts[0], None
    elif len(parts) == 2:
        if parts[1] in known:
            # 'a:b' is namespace:key by the grammar, but a second part that names a type is
            # almost always someone reaching for key:type. Too easy to get silently wrong —
            # both readings are valid, so make them spell out which one they meant.
            raise click.UsageError(
                f"'{spec}' is ambiguous: '{parts[1]}' is both a plausible key and a type "
                f"name.\n"
                f"  For namespace '{parts[0]}', key '{parts[1]}':  {parts[0]}:{parts[1]}={text}\n"
                f"  For key '{parts[0]}' of type '{parts[1]}':     "
                f"{default_namespace or '<namespace>'}:{parts[0]}:{parts[1]}={text}"
                + ("  (or :{}:{}={})".format(parts[0], parts[1], text)
                   if default_namespace else ""))
        namespace, key, type_name = parts[0] or default_namespace, parts[1], None
    elif len(parts) == 3:
        namespace, key, type_name = parts[0] or default_namespace, parts[1], parts[2]
    else:
        raise click.UsageError(f"'{spec}' has too many ':' separators.\n{SPEC_HELP}")

    if not namespace:
        raise click.UsageError(
            f"'{spec}' does not name a namespace — write it as namespace:{key}=… "
            f"or pass --namespace")
    if not key:
        raise click.UsageError(f"'{spec}' does not name a key.\n{SPEC_HELP}")

    if type_name is not None and type_name not in known:
        raise NvsError(f"Unknown type '{type_name}' (expected one of {', '.join(known)})")

    # With no type given the value can only be decoded once the existing entry is known, so
    # hand the raw text through and let the editor resolve it.
    value = _parse_value(type_name, text) if type_name else text
    return Edit(namespace, key, type_name, value)


def _parse_delete(spec, default_namespace):
    from idftool.nvs.edit import Edit

    parts = spec.split(':')
    if len(parts) == 1:
        namespace, key = default_namespace, parts[0]
    elif len(parts) == 2:
        namespace, key = parts[0] or default_namespace, parts[1]
    else:
        raise click.UsageError(f"--delete '{spec}' should be namespace:key or key")
    if not namespace:
        raise click.UsageError(
            f"--delete '{spec}' does not name a namespace — write it as namespace:{key} "
            f"or pass --namespace")
    return Edit(namespace, key)


def _resolve_untyped(image, edits):
    """Fill in the type of any ``--set`` that didn't give one, from the entry it replaces."""
    from idftool.nvs.edit import Edit

    resolved = []
    for edit in edits:
        if edit.is_delete or edit.type is not None:
            resolved.append(edit)
            continue
        existing = image.get(edit.namespace, edit.key)
        if existing is None:
            from idftool.nvs import PRIMITIVES, NvsError
            known = ', '.join(list(PRIMITIVES) + ['string', 'blob'])
            raise NvsError(
                f"'{edit.namespace}:{edit.key}' is not in the image, so its type cannot be "
                f"inferred — write it as {edit.namespace}:{edit.key}:<type>={edit.value} "
                f"(types: {known})")
        resolved.append(Edit(edit.namespace, edit.key, existing.type,
                             _parse_value(existing.type, edit.value)))
    return resolved


def _contiguous_writes(offset, image, dirty):
    """Group dirty page indices into (address, data) runs so a device write is one erase run."""
    from idftool.nvs import PAGE_SIZE

    writes = []
    for page in dirty:
        if writes and writes[-1][0] + len(writes[-1][1]) == offset + page * PAGE_SIZE:
            writes[-1] = (writes[-1][0],
                          writes[-1][1] + image[page * PAGE_SIZE:(page + 1) * PAGE_SIZE])
        else:
            writes.append((offset + page * PAGE_SIZE,
                           image[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]))
    return [(addr, bytes(data)) for addr, data in writes]


def _describe(change):
    if change.action == 'unchanged':
        return f"  = {change.edit.qualified} unchanged"
    if change.action == 'deleted':
        return f"  - {change.edit.qualified} ({change.type}) deleted"
    if change.action == 'added':
        return f"  + {change.edit.qualified} ({change.type}) = {_short(change.edit.value)}"
    return (f"  ~ {change.edit.qualified} ({change.type}): "
            f"{_short(change.before.value)} -> {_short(change.edit.value)}")


def _short(value, limit=48):
    text = value.hex() if isinstance(value, bytes) else str(value)
    return text if len(text) <= limit else f'{text[:limit]}…'


def _split_target(args, image_file, what):
    """Separate an optional leading partition name from the specs that follow it.

    ``--file`` means every positional is a spec; without it the first one names the partition,
    matching ``read``/``write``/``erase``. Click cannot express that conditional arity, so it
    is done by hand.
    """
    args = list(args)
    if image_file is not None:
        return None, args
    if not args:
        raise click.UsageError(
            f"Provide a partition name (or --file) and at least one {what}")
    return args[0], args[1:]


def set_nvs(state, args, image_file, deletes, namespace, output_file, do_rewrite, dry_run,
            **options):
    """Set or delete keys in an NVS partition or image. Keyword arguments go to esptool's
    ``write_flash`` (see :data:`idftool.flash.WRITE_FLASH_OPTIONS`)."""
    import idftool.nvs as nvs
    from idftool.nvs.edit import apply

    partition, specs = _split_target(args, image_file, "SPEC")
    if not specs and not deletes:
        if partition and '=' in partition:
            # The one positional given is a spec, so what's missing is the target it applies to.
            raise click.UsageError(
                f"'{partition}' looks like a SPEC, not a partition — name the partition first "
                f"(idftool set-nvs nvs {partition}) or use -f/--file")
        raise click.UsageError(f"Nothing to do — pass at least one SPEC or --delete.\n{SPEC_HELP}")

    edits = ([_parse_set(spec, namespace) for spec in specs] +
             [_parse_delete(spec, namespace) for spec in deletes])

    data, part, source = _load_image(state, partition, image_file, 'set-nvs')
    image = nvs.parse(data)
    _report_errors(image)
    edits = _resolve_untyped(image, edits)

    result, changes, dirty, compacted = apply(data, edits, force_rewrite=do_rewrite)

    print(f"Editing {source} ({len(data):#x} bytes)")
    for change in changes:
        print(_describe(change))

    if not dirty:
        print("Nothing changed.")
        return

    if compacted:
        print("No room left to append — the image was compacted and rewritten in full.")
    else:
        print(f"Appended in place; {len(dirty)} of {len(data) // nvs.PAGE_SIZE} "
              f"page{'' if len(dirty) == 1 else 's'} changed ({', '.join(map(str, dirty))}).")

    if dry_run:
        print("Dry run — nothing written.")
        return

    if part is not None:
        # Only the pages that actually differ go back to the device. Flash erases in 4 KiB
        # sectors and a page is exactly one sector, so a partial write is safe here.
        writes = _contiguous_writes(part.offset, result, dirty)
        total = sum(len(d) for _, d in writes)
        print(f"Writing {total:#x} bytes to partition '{part.name}' in "
              f"{len(writes)} run{'' if len(writes) == 1 else 's'}")
        write_flash(esp=state.esp, addr_data=writes, flash_size='detect',
                    **write_flash_options(options))
    else:
        target = output_file or image_file
        with open(target, 'wb') as f:
            f.write(result)
        print(f"Wrote {len(result):#x} bytes to '{target}'")


@cli.command('set-nvs', help='Set or delete keys in an NVS partition or image')
@click.argument('args', nargs=-1, metavar='[PARTITION] SPEC...')
@click.option('-f', '--file', 'image_file', default=None,
              help='Edit this NVS image file instead of a partition on the device')
@click.option('-d', '--delete', 'deletes', multiple=True, metavar='KEY',
              help='Delete a key: namespace:key')
@click.option('-n', '--namespace', default=None,
              help='Default namespace for specs that do not name one')
@click.option('-o', '--output', 'output_file', default=None,
              help='With --file, write the result here instead of back over the input')
@click.option('--rewrite', 'do_rewrite', is_flag=True,
              help='Compact the image instead of appending — rebuilds it from its contents')
@click.option('--dry-run', is_flag=True, help='Show what would change without writing anything')
@flash_options
@pass_state
def cmd_set_nvs(state, args, image_file, deletes, namespace, output_file, do_rewrite, dry_run,
                **options):
    """Set or delete keys in an NVS partition on the device, or in an image file with --file.

    A SPEC is `namespace:key=value`, or `namespace:key:type=value` to give the type of a key
    that isn't there yet — for one that is, the type is taken from the entry being replaced.
    A value of `@FILE` is read from that file.

    Changes are appended the way the firmware would write them, so everything else in the
    partition is left byte-for-byte alone and a device write only touches the pages that
    changed. If there is no room left to append, the image is compacted instead.
    """
    return set_nvs(state, args, image_file, deletes, namespace, output_file, do_rewrite, dry_run,
                   **options)


def _parse_get(spec, default_namespace):
    parts = spec.split(':')
    if len(parts) == 1:
        namespace, key = default_namespace, parts[0]
    elif len(parts) == 2:
        namespace, key = parts[0] or default_namespace, parts[1]
    else:
        raise click.UsageError(f"'{spec}' should be namespace:key or key")
    if not namespace:
        raise click.UsageError(
            f"'{spec}' does not name a namespace — write it as namespace:{key} "
            f"or pass --namespace")
    return namespace, key


def get_nvs(state, args, image_file, namespace, raw):
    import idftool.nvs as nvs

    partition, specs = _split_target(args, image_file, "KEY")
    if not specs:
        if partition and ':' in partition:
            raise click.UsageError(
                f"'{partition}' looks like a KEY, not a partition — name the partition first "
                f"(idftool get-nvs nvs {partition}) or use -f/--file")
        raise click.UsageError("Provide at least one key to read (namespace:key)")

    keys = [_parse_get(spec, namespace) for spec in specs]
    if raw and len(keys) != 1:
        raise click.UsageError("--raw reads exactly one key")

    data, _, _ = _load_image(state, partition, image_file, 'get-nvs')
    image = nvs.parse(data)
    _report_errors(image)

    values = []
    for ns, key in keys:
        entry = image.get(ns, key)
        if entry is None:
            raise nvs.NvsError(f"'{ns}:{key}' is not in the image")
        values.append(entry)

    if raw:
        entry = values[0]
        payload = (entry.value if isinstance(entry.value, bytes)
                   else str(entry.value).encode())
        sys.stdout.buffer.write(payload)
        return

    # Bare values, one per line and nothing else, so the output can be piped straight into
    # a shell variable.
    for entry in values:
        print(entry.value.hex() if isinstance(entry.value, bytes) else entry.value)


@cli.command('get-nvs', help='Print the value of one or more keys in an NVS partition or image')
@click.argument('args', nargs=-1, metavar='[PARTITION] KEY...')
@click.option('-f', '--file', 'image_file', default=None,
              help='Read the NVS image from this file instead of the device')
@click.option('-n', '--namespace', default=None,
              help='Default namespace for keys that do not name one')
@click.option('--raw', is_flag=True,
              help='Write the value to stdout as raw bytes rather than a line of text')
@pass_state
def cmd_get_nvs(state, args, image_file, namespace, raw):
    """Print the value of one or more keys, from a partition on the device or --file.

    A KEY is `namespace:key`, or just `key` with `--namespace`. Values are printed bare, one
    per line, so they can be captured in a shell; a blob comes out as hex, or as its raw
    bytes with `--raw`.
    """
    return get_nvs(state, args, image_file, namespace, raw)
