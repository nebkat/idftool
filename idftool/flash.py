"""The pass-through options shared by every command that writes to flash.

esptool's ``write_flash`` takes its knobs as ``**kwargs`` and reads each one with
``kwargs.get``, so a name it does not recognise is silently ignored rather than rejected —
a misspelled option would be a write that quietly did something else. Everything idftool
forwards is therefore checked against :data:`WRITE_FLASH_OPTIONS` first.

Commands expose the useful subset as flags via :func:`flash_options` and hand what they
were given to :func:`write_flash_options`; library callers pass the same names as keyword
arguments::

    write_image(state, 'flash.img', erase=False, skip_flashed=True)

:func:`write_flash` here wraps esptool's, implementing ``skip_flashed`` itself (see
:func:`flash_matches`) rather than forwarding it.
"""
import hashlib
import io
from contextlib import redirect_stdout

import rich_click as click
from esptool.cmds import _update_image_flash_params, write_flash as esptool_write_flash
from esptool.util import get_bytes, pad_to

#: Keyword arguments of esptool's ``write_flash`` that idftool forwards. The ones without a
#: flag below take images or lists rather than a yes/no, so they are library-only:
#: ``encrypt_files`` (per-file encryption), ``diff_with`` (previously flashed images, zipped
#: with ``addr_data``) and its ``no_diff_verify``.
WRITE_FLASH_OPTIONS = (
    'skip_flashed', 'erase_all', 'compress', 'no_compress', 'encrypt', 'encrypt_files',
    'force', 'ignore_flash_enc_efuse', 'no_progress', 'diff_with', 'no_diff_verify',
)

#: Options whose False is a real answer ("do not") rather than "flag untouched", so only
#: None drops them. Click gives them ``default=None`` and all three states reach esptool.
TRISTATE_OPTIONS = ('compress', 'skip_flashed')

#: The flags :func:`flash_options` adds, for the help panel. Long names only: listing a short
#: alias too renders the option twice, and a `--x/--no-x` pair is named by its first half.
#: The panel is not called "Flash options" because esptool registers a global group by that
#: name (for --flash-freq and friends), and same-named panels overwrite each other.
FLASH_OPTION_FLAGS = ['--skip-flashed', '--compress', '--encrypt', '--force',
                      '--ignore-flash-enc-efuse', '--no-progress']

#: Size of the first chunk :func:`flash_matches` compares — one flash sector. An app image
#: keeps its header at 0 and its ``esp_app_desc_t`` (project name, version, and the ELF
#: SHA-256) at 0x20, so a different build almost always differs inside this first sector and
#: is caught by a single ~15 ms hash instead of one covering the whole image.
CHECK_FIRST_CHUNK_SIZE = 0x1000

#: Size of every chunk after the first. ``flash_md5sum`` costs a flat ~2 ms per call on top
#: of a linear ~3.3 s/MB (measured on an ESP32-S3 stub), so chunking a 2 MB image this way
#: adds about 1% to the cost of a full match and buys a 32x earlier exit on a mismatch.
CHECK_CHUNK_SIZE = 0x10000


def flash_options(f):
    """Attach the write_flash pass-through flags shared by the flashing commands.

    Each defaults to None or False, meaning "leave the default alone", so a command that adds
    these behaves exactly as it did before unless a flag is given.
    """
    options = [
        click.option('--skip-flashed/--no-skip-flashed', default=None,
                     help='Skip each file whose partition already holds it. Compared on the '
                          'device in chunks, one sector first, so a difference usually costs '
                          'a few milliseconds to find. All-or-nothing per file, not per '
                          'sector  [default: on for app writes]'),
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
    """Turn a command's options into keyword arguments for :func:`write_flash`.

    Takes either shape: the values click produces for the flags above (where an untouched
    flag is None or False and is dropped, leaving the default), or the plain esptool keywords
    a library caller passes. `defaults` are what the command itself asks for, and a
    caller-supplied option of the same name wins — including a :data:`TRISTATE_OPTIONS` one
    given as False, which is how `--no-skip-flashed` turns off a command's default.
    """
    kwargs = dict(defaults)
    for name, value in options.items():
        if name not in WRITE_FLASH_OPTIONS:
            raise TypeError(f"Unknown flash option '{name}' (expected one of "
                            f"{', '.join(WRITE_FLASH_OPTIONS)})")
        if name in TRISTATE_OPTIONS:
            if value is None:
                continue  # flag untouched, the default stands
            if name == 'compress' and value is False:
                # esptool reads a false `compress` as "not asked either way" and still
                # compresses when the stub is in use, so say it the way esptool means it.
                kwargs['no_compress'] = True
                continue
            kwargs[name] = value
            continue
        if value is None or value is False:
            continue
        kwargs[name] = value
    return kwargs


def bytes_as_written(esp, address, data, flash_freq='keep', flash_mode='keep',
                     flash_size='keep'):
    """The bytes esptool would put in flash for `data` at `address`.

    Not always the bytes handed in: writes are padded to a 4-byte boundary, and an image at
    the bootloader offset has its flash mode/size/frequency header bytes rewritten (and its
    appended SHA-256 recomputed) to match what was asked for. Comparing against flash means
    comparing against these, or a bootloader that is already in flash never matches.

    The rewrite prints a line of its own, which esptool prints again for real when the write
    goes ahead, so it is swallowed here.
    """
    data = pad_to(data, 4)
    if not getattr(esp, 'secure_download_mode', False) and not esp.get_secure_boot_enabled():
        with redirect_stdout(io.StringIO()):
            data = _update_image_flash_params(esp, address, flash_freq, flash_mode,
                                              flash_size, data)
    return data


def flash_matches(esp, address, data, first_chunk_size=CHECK_FIRST_CHUNK_SIZE,
                  chunk_size=CHECK_CHUNK_SIZE):
    """Whether flash at `address` already holds `data`, without reading any of it back.

    ``SPI_FLASH_MD5`` hashes a region on the device and returns the 16 bytes, so the cost is
    the device's own flash read, not the serial link — reading the same data back to hash it
    here is two orders of magnitude slower.

    The region is compared in chunks so a mismatch ends the comparison instead of hashing the
    whole thing: one sector first (which for an app image covers the header and the app
    descriptor, so a different build is normally caught there), then
    :data:`CHECK_CHUNK_SIZE` at a time. `data` must already be
    :func:`bytes_as_written`.

    There is no guard here for the cases where the comparison cannot be trusted or is not
    supported: callers ask :func:`flash_check_unavailable` first, and a chip whose loader has
    no MD5 command raises out of esptool rather than answering False.
    """
    offset = 0
    while offset < len(data):
        size = min(first_chunk_size if offset == 0 else chunk_size, len(data) - offset)
        chunk = data[offset:offset + size]
        if esp.flash_md5sum(address + offset, size) != hashlib.md5(chunk).hexdigest():
            return False
        offset += size
    return True


def flash_check_unavailable(esp, options):
    """Why flash contents cannot be compared before writing, or None when they can.

    Mirrors the conditions esptool disables its own MD5 comparison under, plus ``diff_with``:
    that is zipped positionally with ``addr_data``, so dropping an entry here would pair the
    rest with the wrong diff images. esptool does its own comparison in that case anyway.
    """
    if options.get('erase_all'):
        return 'the flash is erased first'
    if options.get('encrypt') or options.get('encrypt_files'):
        return 'the data is encrypted on the way in'
    if options.get('diff_with'):
        return "esptool's own differential reflashing is in use"
    if getattr(esp, 'secure_download_mode', False):
        return 'secure download mode is enabled'
    if esp.CHIP_NAME == 'ESP8266' and not getattr(esp, 'IS_STUB', False):
        return 'the ESP8266 ROM bootloader cannot hash flash'
    return None


def write_flash(esp, addr_data, flash_freq='keep', flash_mode='keep', flash_size='keep',
                **kwargs):
    """esptool's ``write_flash``, with ``skip_flashed`` implemented here instead.

    esptool's own ``skip_flashed`` hashes the whole region in one go before every write, so a
    file that turns out to differ costs a full pass (~3.3 s/MB) for nothing. This compares in
    chunks (:func:`flash_matches`) and hands esptool only the files that really need writing,
    with ``skip_flashed`` off so it does not repeat the work.

    Every other keyword argument goes straight through.
    """
    skip_flashed = kwargs.pop('skip_flashed', False)
    if skip_flashed:
        unavailable = flash_check_unavailable(esp, kwargs)
        if unavailable:
            print(f"Note: not checking what is already in flash, {unavailable}")
        else:
            addr_data = _drop_already_flashed(esp, addr_data, flash_freq, flash_mode,
                                              flash_size)
            if not addr_data:
                return

    return esptool_write_flash(esp=esp, addr_data=addr_data, flash_freq=flash_freq,
                               flash_mode=flash_mode, flash_size=flash_size, **kwargs)


def _drop_already_flashed(esp, addr_data, flash_freq, flash_mode, flash_size):
    """The entries of `addr_data` whose content is not already in flash.

    Entries are returned as they came in, not as the bytes they were compared as, so esptool
    still sees the file names it prints and re-reads them itself.
    """
    remaining = []
    for entry in addr_data:
        address, source = entry
        data, name = get_bytes(source)
        described = 'Input bytes' if name is None else f"'{name}'"
        if not data:
            remaining.append(entry)
            continue
        data = bytes_as_written(esp, address, data, flash_freq, flash_mode, flash_size)
        if len(data) > CHECK_FIRST_CHUNK_SIZE:
            print(f"Comparing {described} against flash at {address:#010x}...")
        if flash_matches(esp, address, data):
            print(f"{described} at {address:#010x} is already in flash, skipping write")
        else:
            remaining.append(entry)
    return remaining
