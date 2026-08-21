"""The pass-through options shared by every command that writes to flash.

esptool's ``write_flash`` takes its knobs as ``**kwargs`` and reads each one with
``kwargs.get``, so a name it does not recognise is silently ignored rather than rejected —
a misspelled option would be a write that quietly did something else. Everything idftool
forwards is therefore checked against :data:`WRITE_FLASH_OPTIONS` first.

Commands expose the useful subset as flags via :func:`flash_options` and hand what they
were given to :func:`write_flash_options`; library callers pass the same names as keyword
arguments::

    write_image(state, 'flash.img', erase=False, skip_flashed=True)
"""
import rich_click as click

#: Keyword arguments of esptool's ``write_flash`` that idftool forwards. The ones without a
#: flag below take images or lists rather than a yes/no, so they are library-only:
#: ``encrypt_files`` (per-file encryption), ``diff_with`` (previously flashed images, zipped
#: with ``addr_data``) and its ``no_diff_verify``.
WRITE_FLASH_OPTIONS = (
    'skip_flashed', 'erase_all', 'compress', 'no_compress', 'encrypt', 'encrypt_files',
    'force', 'ignore_flash_enc_efuse', 'no_progress', 'diff_with', 'no_diff_verify',
)

#: The flags :func:`flash_options` adds, for the help panel. Long names only: listing a short
#: alias too renders the option twice, and a `--x/--no-x` pair is named by its first half.
#: The panel is not called "Flash options" because esptool registers a global group by that
#: name (for --flash-freq and friends), and same-named panels overwrite each other.
FLASH_OPTION_FLAGS = ['--skip-flashed', '--compress', '--encrypt', '--force',
                      '--ignore-flash-enc-efuse', '--no-progress']


def flash_options(f):
    """Attach the write_flash pass-through flags shared by the flashing commands.

    Each defaults to None or False, meaning "leave esptool's own default alone", so a
    command that adds these behaves exactly as it did before unless a flag is given.
    """
    options = [
        click.option('--skip-flashed', is_flag=True,
                     help='Skip each file whose partition already holds it (MD5 compared '
                          'first). All-or-nothing per file, not per sector'),
        click.option('--compress/--no-compress', default=None,
                     help='Compress the data on the way to the device  '
                          '[default: on, unless the flasher stub is disabled]'),
        click.option('--encrypt', is_flag=True, help='Encrypt the data as it is written'),
        click.option('--force', is_flag=True,
                     help='Ignore safety and content checks (chip mismatch, secure boot, '
                          'flash size)'),
        click.option('--ignore-flash-enc-efuse', is_flag=True,
                     help='Ignore the flash encryption eFuse settings'),
        click.option('--no-progress', is_flag=True, help='Do not print progress while writing'),
    ]
    for option in reversed(options):
        f = option(f)
    return f


def option_group(command, *options):
    """Give a flashing command a 'Write options' help panel of its own.

    Without one the pass-through flags bury the handful of options the command actually
    needs, which are listed in `options` (long names only, `--help` is added).
    """
    click.rich_click.OPTION_GROUPS[f'* {command}'] = [
        {'name': 'Options', 'options': [*options, '--help']},
        {'name': 'Write options', 'options': FLASH_OPTION_FLAGS},
    ]


def split_options(options):
    """Split a command's keyword arguments into (write_flash options, everything else).

    For the commands whose ``**options`` already carry something else — ``write-fs`` and its
    per-filesystem knobs.
    """
    flash = {name: value for name, value in options.items() if name in WRITE_FLASH_OPTIONS}
    rest = {name: value for name, value in options.items() if name not in WRITE_FLASH_OPTIONS}
    return flash, rest


def write_flash_options(options, **defaults):
    """Turn a command's options into keyword arguments for esptool's ``write_flash``.

    Takes either shape: the values click produces for the flags above (where an untouched
    flag is None or False and is dropped, leaving esptool's default), or the plain esptool
    keywords a library caller passes. `defaults` are what the command itself asks for, and
    a caller-supplied option of the same name wins.
    """
    kwargs = dict(defaults)
    for name, value in options.items():
        if name not in WRITE_FLASH_OPTIONS:
            raise TypeError(f"Unknown flash option '{name}' (expected one of "
                            f"{', '.join(WRITE_FLASH_OPTIONS)})")
        if name == 'compress' and value is False:
            # esptool reads a false `compress` as "not asked either way" and still compresses
            # when the stub is in use, so say it the way esptool means it.
            kwargs['no_compress'] = True
            continue
        if value is None or value is False:
            continue
        kwargs[name] = value
    return kwargs
