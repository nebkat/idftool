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
import sys
import time
from contextlib import contextmanager, redirect_stdout

import rich_click as click
from esptool.cmds import _update_image_flash_params, write_flash as esptool_write_flash
from esp_pylib.logger import EspLog
from esptool.logger import log
from esptool.util import get_bytes, pad_to

#: Keyword arguments of esptool's ``write_flash`` that idftool forwards. The ones without a
#: flag below take images or lists rather than a yes/no, so they are library-only:
#: ``encrypt_files`` (per-file encryption), ``diff_with`` (previously flashed images, zipped
#: with ``addr_data``) and its ``no_diff_verify``.
WRITE_FLASH_OPTIONS = (
    'skip_flashed', 'erase_all', 'compress', 'no_compress', 'encrypt', 'encrypt_files',
    'force', 'ignore_flash_enc_efuse', 'no_progress', 'diff_with', 'no_diff_verify',
)

#: Options :func:`write_flash` implements itself rather than forwarding, so they are not
#: checked against esptool's keywords.
IDFTOOL_FLASH_OPTIONS = ('diff',)

#: Options whose False is a real answer ("do not") rather than "flag untouched", so only
#: None drops them. Click gives them ``default=None`` and all three states reach esptool.
TRISTATE_OPTIONS = ('compress', 'skip_flashed', 'diff')

#: The flags :func:`flash_options` adds, for the help panel. Long names only: listing a short
#: alias too renders the option twice, and a `--x/--no-x` pair is named by its first half.
#: The panel is not called "Flash options" because esptool registers a global group by that
#: name (for --flash-freq and friends), and same-named panels overwrite each other.
FLASH_OPTION_FLAGS = ['--skip-flashed', '--diff', '--compress', '--encrypt', '--force',
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

#: Granularity of the differential scan — the flash erase sector, the smallest region that
#: can be rewritten on its own, so there is nothing to gain from going finer.
DIFF_SECTOR_SIZE = 0x1000

#: hash / (write + verify), the constant the bail-out rule is built on. Measured on an
#: ESP32-S3 over its native USB serial: hashing 3.72 s/MB at sector granularity, writing
#: 8.60 s/MB, verifying 3.27 s/MB — a true ratio of 0.318. The value used here is a little
#: higher, which puts the threshold at exactly 0.70 at the first decision point.
#:
#: It is the fast-transport figure, and deliberately so: on a real USB-UART bridge the write
#: is link-bound rather than flash-bound (~50 s/MB at 115200) and the true ratio falls to
#: about 0.07, where scanning is almost always worth it. Erring towards the fast case means
#: never gambling more than the quickest link would justify.
DIFF_COST_RATIO = 0.343

#: How much of a region is scanned before the bail-out rule is first consulted, as a
#: fraction, and the bounds that fraction is clamped to.
#:
#: Some minimum is essential. Changes cluster at the front of an app image — a rebuild with
#: nothing but a new version string rewrites the ``esp_app_desc_t`` and its ELF SHA-256 and
#: leaves every other sector alone. Judged on its first sector that image looks 100%
#: changed, and a rule without a floor would abandon the scan and rewrite megabytes to avoid
#: writing one sector.
DIFF_MIN_SAMPLE_FRACTION = 8
DIFF_MIN_SAMPLE_MIN = 0x10000
DIFF_MIN_SAMPLE_MAX = 0x40000

#: How many matching sectors are worth rewriting to keep one run going rather than starting
#: another. Each separate write costs about 69 ms of fixed overhead (erase command,
#: compression, round trips, its own verify) against about 31 ms to erase and write one more
#: sector inside a run it is already writing — so bridging a gap of two pays for itself, and
#: three does not. Both figures measured on an ESP32-S3: 4 KB alone 0.10 s, 64 KB in one run
#: 0.50 s.
DIFF_COALESCE_SECTORS = 2


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
        click.option('--diff/--no-diff', default=None,
                     help='Rewrite only the flash sectors that differ, found by hashing the '
                          'region a sector at a time. Abandoned for a whole-region write '
                          'once too much of it has been seen to differ for the scan to pay '
                          'for itself  [default: on for app writes]'),
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
    known = (*WRITE_FLASH_OPTIONS, *IDFTOOL_FLASH_OPTIONS)
    flash = {name: value for name, value in options.items() if name in known}
    rest = {name: value for name, value in options.items() if name not in known}
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
        if name not in WRITE_FLASH_OPTIONS and name not in IDFTOOL_FLASH_OPTIONS:
            raise TypeError(f"Unknown flash option '{name}' (expected one of "
                            f"{', '.join((*WRITE_FLASH_OPTIONS, *IDFTOOL_FLASH_OPTIONS))})")
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


def plan_sector_writes(esp, address, data, on_scanned=None, sector_size=DIFF_SECTOR_SIZE,
                       coalesce=DIFF_COALESCE_SECTORS):
    """Yield the writes a sector-by-sector comparison calls for, as it finds them.

    Yields ``(offset, length, reason)`` relative to `address`. `reason` is ``'changed'`` for
    a run of sectors that differ, or ``'remainder'`` for everything left when the scan is
    abandoned — after which nothing more is yielded. A region already in flash yields
    nothing at all. `data` must be :func:`bytes_as_written` and `address` sector-aligned.

    Yielding as it goes, rather than returning a plan, is what lets the caller write each run
    while the scan continues: one pass over the region, and one progress bar.

    Scanning is not free (~3.7 s/MB) and pays only if enough of the region matches, which is
    not known up front but is learned while scanning. After `fraction` of the region has been
    scanned with `dirty` of it differing, the hashing already done is spent either way and
    only the rest matters: carrying on costs the remaining hash plus writing what differs,
    giving up costs writing all that is left. So it is worth carrying on while::

        dirty < 1 - DIFF_COST_RATIO * (1 - fraction)

    The threshold rises as the scan proceeds — 0.70 an eighth of the way in, 0.83 at half,
    0.97 at nine tenths — because a late abandonment throws away nearly all of the hashing
    and saves nearly none of it. Early abandonment is the cheap one, which is why it is the
    only one the rule is eager about, and why :data:`DIFF_MIN_SAMPLE_FRACTION` holds it off
    until enough has been seen to be worth believing.
    """
    total = len(data)
    minimum = min(max(total // DIFF_MIN_SAMPLE_FRACTION, DIFF_MIN_SAMPLE_MIN),
                  DIFF_MIN_SAMPLE_MAX)
    run_start = run_end = None
    dirty = sectors = offset = 0

    while offset < total:
        size = min(sector_size, total - offset)
        if esp.flash_md5sum(address + offset, size) != \
                hashlib.md5(data[offset:offset + size]).hexdigest():
            if run_start is None:
                run_start = offset
            run_end = offset + size
            dirty += 1
        elif run_end is not None and offset - run_end >= coalesce * sector_size:
            # Far enough past the open run that a fresh write is cheaper than bridging.
            yield run_start, run_end - run_start, 'changed'
            run_start = run_end = None
        offset += size
        sectors += 1
        if on_scanned is not None:
            on_scanned(offset)

        if minimum <= offset < total and \
                dirty / sectors >= 1 - DIFF_COST_RATIO * (1 - offset / total):
            start = run_start if run_start is not None else offset
            yield start, total - start, 'remainder'
            return

    if run_start is not None:
        yield run_start, run_end - run_start, 'changed'


class Progress:
    """One progress bar for a whole operation, drawn by esptool's own logger.

    The differential path scans and writes in the same pass, so its progress is two numbers
    that advance independently — bytes compared, and bytes written. Both go on one bar, which
    tracks the scan (whose total is known from the start) and reports the writes alongside.

    Rendering goes through ``log.progress_bar`` rather than being written out here, so this
    inherits esptool's decisions rather than second-guessing them: whether the terminal takes
    ANSI escapes, the Windows fallback, and the verbosity setting, which is what ``silent``
    has to reach to be worth anything. It also means the bar looks like every other bar the
    user sees from a flashing tool.
    """

    def __init__(self, prefix, total, enabled=True):
        self.prefix, self.total, self.enabled = prefix, total, enabled and total > 0
        self.scanned = self.written = 0
        self._shown = None

    @staticmethod
    def size(count):
        for unit, scale in (('M', 1 << 20), ('K', 1 << 10)):
            if count >= scale:
                return f'{count / scale:.1f}{unit}'.replace('.0', '')
        return f'{count}B'

    @staticmethod
    def rate(count, seconds):
        return f'{count / seconds * 8 / 1000:.1f} kbit/s' if seconds > 0 else 'instant'

    def update(self, scanned=None, written=None):
        """Redraw, but only when the percentage has actually moved.

        A megabyte is 256 sectors and a redraw apiece is wasted work on a smart terminal and
        256 lines of scroll on one that cannot overwrite.
        """
        if scanned is not None:
            self.scanned = scanned
        if written is not None:
            self.written = written
        if not self.enabled:
            return
        percent = 100 * min(self.scanned, self.total) // self.total
        if percent == self._shown:
            return
        self._shown = percent
        suffix = f' compared {self.size(self.scanned)}/{self.size(self.total)}'
        if self.written:
            suffix += f', written {self.size(self.written)}'
        log.progress_bar(min(self.scanned, self.total), self.total,
                         prefix=f'{self.prefix} ', suffix=suffix)

    def finish(self):
        """End the bar's line if it stopped short — an abandoned scan never reaches 100%."""
        if self._shown is not None and self._shown != 100:
            log.print("")
        self._shown = None


def _say(prefix, args, file=None):
    print(prefix + " ".join(str(a) for a in args), file=file or sys.stdout)


#: What :func:`quiet_esptool` overrides: routine narration and progress go, while notes,
#: warnings and errors are printed directly rather than through the silenced `print`, so
#: this suppresses by kind rather than by matching message text.
_QUIET = {
    "print": lambda self, *args, **kwargs: None,
    "progress_bar": lambda self, *args, **kwargs: None,
    "stage": lambda self, *args, **kwargs: None,
    "note": lambda self, *args, **kwargs: _say("Note: ", args),
    "warning": lambda self, *args, **kwargs: _say("Warning: ", args),
    "warn": lambda self, *args, **kwargs: _say("Warning: ", args),
    "error": lambda self, *args, **kwargs: _say("", args, sys.stderr),
    "err": lambda self, *args, **kwargs: _say("", args, sys.stderr),
}


@contextmanager
def quiet_esptool():
    """Silence esptool's routine progress while it writes, keeping notes and warnings.

    A differential write calls ``write_flash`` once per run of changed sectors, and each call
    narrates itself — erase range, compressed size, its own progress bar, bytes written,
    verification — which is six lines and a flickering bar per run. idftool draws one bar
    across the whole operation instead (:class:`Progress`), so the per-run narration has to
    go, but only while esptool is inside a run: the bar itself is drawn between them, through
    the real logger.
    """
    log.print  # noqa: B018  (esptool's `log` is a proxy; this makes sure its logger exists)
    target = EspLog.instance
    original = type(target)
    # A subclass of the logger's own class, so the swap is allowed whatever that class is.
    target.__class__ = type("_QuietEsptool", (original,), _QUIET)
    try:
        yield
    finally:
        target.__class__ = original


def write_flash(esp, addr_data, flash_freq='keep', flash_mode='keep', flash_size='keep',
                **kwargs):
    """esptool's ``write_flash``, with ``skip_flashed`` implemented here and ``diff`` added.

    esptool's own ``skip_flashed`` hashes the whole region in one go before every write, so a
    file that turns out to differ costs a full pass (~3.3 s/MB) for nothing. This compares in
    chunks (:func:`flash_matches`) and hands esptool only the files that really need writing,
    with ``skip_flashed`` off so it does not repeat the work.

    ``diff`` goes further and compares a sector at a time (:func:`plan_sector_writes`),
    writing each run of changed sectors as the scan reaches it. Runs are written separately
    so that esptool's post-write verification is over what was written rather than the whole
    region. It supersedes ``skip_flashed``, which is the same comparison with the region as
    its only unit.

    Every other keyword argument goes straight through.
    """
    skip_flashed = kwargs.pop('skip_flashed', False)
    diff = kwargs.pop('diff', False)
    if skip_flashed or diff:
        unavailable = flash_check_unavailable(esp, kwargs)
        if unavailable:
            print(f"Note: not checking what is already in flash, {unavailable}")
        else:
            addr_data = _plan_writes(esp, addr_data, diff, flash_freq, flash_mode,
                                     flash_size, kwargs)
            if not addr_data:
                return

    return esptool_write_flash(esp=esp, addr_data=addr_data, flash_freq=flash_freq,
                               flash_mode=flash_mode, flash_size=flash_size, **kwargs)


def _plan_writes(esp, addr_data, diff, flash_freq, flash_mode, flash_size, kwargs):
    """What of `addr_data` is left for esptool to write, after comparing it against flash.

    Entries compared a sector at a time are written here, as the comparison finds them, and
    do not come back. The rest are returned as they came in, so that esptool still sees the
    file names it prints and re-reads them itself.
    """
    planned = []
    for entry in addr_data:
        address, source = entry
        data, name = get_bytes(source)
        described = 'Input bytes' if name is None else f"'{name}'"
        if not data:
            planned.append(entry)
            continue

        prepared = bytes_as_written(esp, address, data, flash_freq, flash_mode, flash_size)

        # A region that does not start on a sector boundary cannot be rewritten a sector at
        # a time: the write would erase the sector it starts inside, taking whatever shares
        # it. Rare (partition offsets are sector-aligned), and the whole-region comparison
        # below still applies.
        if diff and address % DIFF_SECTOR_SIZE == 0:
            if _write_changed_sectors(esp, address, prepared, described, kwargs):
                continue

        if len(prepared) > CHECK_FIRST_CHUNK_SIZE:
            print(f"Comparing {described} against flash at {address:#010x}...")
        if flash_matches(esp, address, prepared):
            print(f"{described} at {address:#010x} is already in flash, skipping write")
        else:
            planned.append(entry)
    return planned


def _write_changed_sectors(esp, address, prepared, described, kwargs):
    """Compare `prepared` against flash a sector at a time, writing what differs as it goes.

    Returns False when the scan was abandoned before writing anything, leaving the caller to
    fall back to comparing the region as a whole.
    """
    print(f"Comparing {described} against flash at {address:#010x}...")
    progress = Progress(described, len(prepared), enabled=not kwargs.get('no_progress'))
    written = runs = 0
    elapsed = 0.0
    abandoned = False

    try:
        for offset, length, reason in plan_sector_writes(
                esp, address, prepared, on_scanned=lambda n: progress.update(scanned=n)):
            if reason == 'remainder':
                abandoned = True
                if written == 0:
                    return False        # nothing written yet: let the caller write it whole
            # `flash_size` stays 'keep': `prepared` has already been through
            # bytes_as_written, and re-detecting the size costs a round trip per run.
            run = {**kwargs, 'no_progress': True}
            started = time.time()
            with quiet_esptool():
                esptool_write_flash(
                    esp=esp, flash_size='keep',
                    addr_data=[(address + offset, prepared[offset:offset + length])], **run)
            elapsed += time.time() - started
            written += length
            runs += 1
            progress.update(written=written)
    finally:
        progress.finish()

    # esptool reports what it wrote and how fast after every write; a differential one is
    # made of many, so the same figures are totalled up and reported once here instead.
    if written == 0:
        print(f"{described} at {address:#010x} is already in flash, skipping write")
    elif abandoned:
        print(f"{described}: too much of it differs to keep comparing, wrote the remaining "
              f"{Progress.size(written)} in full in {elapsed:.1f} seconds "
              f"({Progress.rate(written, elapsed)})")
    else:
        print(f"{described}: wrote {Progress.size(written)} of "
              f"{Progress.size(len(prepared))} in {runs} region{'s' if runs != 1 else ''} "
              f"in {elapsed:.1f} seconds ({Progress.rate(written, elapsed)})")
    return True
