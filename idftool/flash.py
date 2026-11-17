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
from typing import IO, Any, Callable, Iterator, Mapping, Optional, Sequence, TypeVar, Union

from esptool import ESPLoader

import rich_click as click
from esptool.cmds import _update_image_flash_params, detect_flash_size, \
    write_flash as esptool_write_flash
from esp_pylib.logger import EspLog, Verbosity
from esptool.logger import log
from rich.console import Console
from rich.control import Control
from rich.segment import ControlType
from rich.text import Text
from esptool.util import get_bytes, pad_to

from idftool.apps import BOOTLOADER_DESCRIPTIONS_END, forget_device_partition_table_offset
from idftool.display import say

#: What esptool writes from: a file name, the bytes themselves, or a binary file.
ImageSource = Union[str, bytes, IO[bytes]]

_Command = TypeVar('_Command', bound=Callable[..., Any])

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


def flash_options(f: _Command) -> _Command:
    """Attach the write_flash pass-through flags shared by the flashing commands.

    Each defaults to None or False, meaning "leave the default alone", so a command that adds
    these behaves exactly as it did before unless a flag is given.
    """
    options = [
        click.option('--skip-flashed/--no-skip-flashed', default=None,
                     help='Skip each file whose partition already holds it. Compared on the '
                          'device in chunks, one sector first, so a difference usually costs '
                          'a few milliseconds to find. All-or-nothing per file, not per '
                          'sector  [default: on, except write-image]'),
        click.option('--diff/--no-diff', default=None,
                     help='Rewrite only the flash sectors that differ, found by hashing the '
                          'region a sector at a time. Abandoned for a whole-region write '
                          'once too much of it has been seen to differ for the scan to pay '
                          'for itself  [default: on, except write-image]'),
        click.option('--compress/--no-compress', default=None,
                     help='Compress the data on the way to the device  '
                          '[default: on, unless the flasher stub is disabled]'),
        click.option('--encrypt', is_flag=True, help='Encrypt the data as it is written'),
        click.option('--force', is_flag=True,
                     help='Ignore safety and content checks (chip mismatch, secure boot, '
                          'flash size, partition table offset)'),
        click.option('--ignore-flash-enc-efuse', is_flag=True,
                     help='Ignore the flash encryption eFuse settings'),
        click.option('--no-progress', is_flag=True, help='Do not print progress while writing'),
    ]
    for option in reversed(options):
        f = option(f)
    return f


def option_group(command: str, *options: str) -> None:
    """Give a flashing command a 'Write options' help panel of its own.

    Without one the pass-through flags bury the handful of options the command actually
    needs, which are listed in `options` (long names only, `--help` is added).
    """
    click.rich_click.OPTION_GROUPS[f'* {command}'] = [
        {'name': 'Options', 'options': [*options, '--help']},
        {'name': 'Write options', 'options': FLASH_OPTION_FLAGS},
    ]


def split_options(options: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a command's keyword arguments into (write_flash options, everything else).

    For the commands whose ``**options`` already carry something else — ``write-fs`` and its
    per-filesystem knobs.
    """
    known = (*WRITE_FLASH_OPTIONS, *IDFTOOL_FLASH_OPTIONS)
    flash = {name: value for name, value in options.items() if name in known}
    rest = {name: value for name, value in options.items() if name not in known}
    return flash, rest


def write_flash_options(options: Mapping[str, Any], **defaults: Any) -> dict[str, Any]:
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


def bytes_as_written(esp: ESPLoader, address: int, data: bytes, flash_freq: str = 'keep',
                     flash_mode: str = 'keep', flash_size: str = 'keep') -> bytes:
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


def flash_matches(esp: ESPLoader, address: int, data: bytes,
                  first_chunk_size: int = CHECK_FIRST_CHUNK_SIZE,
                  chunk_size: int = CHECK_CHUNK_SIZE) -> bool:
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


def flash_check_unavailable(esp: ESPLoader, options: Mapping[str, Any]) -> Optional[str]:
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


def plan_sector_writes(esp: ESPLoader, address: int, data: bytes,
                       on_compared: Optional[Callable[[int, int, bool], None]] = None,
                       sector_size: int = DIFF_SECTOR_SIZE,
                       coalesce: int = DIFF_COALESCE_SECTORS) -> Iterator[tuple[int, int, str]]:
    """Yield the writes a sector-by-sector comparison calls for, as it finds them.

    Yields ``(offset, length, reason)`` relative to `address`. `reason` is ``'changed'`` for
    a run of sectors that differ, or ``'remainder'`` for everything left when the scan is
    abandoned — after which nothing more is yielded. A region already in flash yields
    nothing at all. `data` must be :func:`bytes_as_written` and `address` sector-aligned.

    Yielding as it goes, rather than returning a plan, is what lets the caller write each run
    while the scan continues: one pass over the region, and one progress bar.

    `on_compared` is called with ``(offset, size, matches)`` as each sector is compared,
    before any run that sector closes is yielded.

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
    run_start: Optional[int] = None
    run_end = 0
    dirty = sectors = offset = 0

    while offset < total:
        size = min(sector_size, total - offset)
        matches = esp.flash_md5sum(address + offset, size) == \
            hashlib.md5(data[offset:offset + size]).hexdigest()
        if on_compared is not None:
            on_compared(offset, size, matches)
        if not matches:
            if run_start is None:
                run_start = offset
            run_end = offset + size
            dirty += 1
        elif run_start is not None and offset - run_end >= coalesce * sector_size:
            # Far enough past the open run that a fresh write is cheaper than bridging.
            yield run_start, run_end - run_start, 'changed'
            run_start = None
        offset += size
        sectors += 1

        if minimum <= offset < total and \
                dirty / sectors >= 1 - DIFF_COST_RATIO * (1 - offset / total):
            start = run_start if run_start is not None else offset
            yield start, total - start, 'remainder'
            return

    if run_start is not None:
        yield run_start, run_end - run_start, 'changed'


def format_size(count: int) -> str:
    """A byte count for a person, e.g. ``512 B``, ``320 kB``, ``1.54 MB``."""
    if count < 1024:
        return f'{count} B'
    if count < 1024 * 1024:
        return f'{count / 1024:.0f} kB'
    return f'{count / 1024 / 1024:.2f} MB'


def _clock(seconds: float) -> str:
    whole = int(seconds)
    return f'{whole}s' if whole < 60 else f'{whole // 60}:{whole % 60:02d}'


def _interactive_console() -> Optional[Console]:
    """esptool's console for stdout, when a bar can be redrawn in place on it.

    Not when esptool is silent or verbose (verbose output would land in the middle of the
    bar), not when stdout is not a terminal, and not under a logger of the caller's own that
    has no console: those get the summary line alone. Taking esptool's console keeps its
    decisions on colour and terminal detection, ``--no-color`` and ``FORCE_COLOR`` among them.
    """
    log.print  # noqa: B018  (esptool's `log` is a proxy; this makes sure its logger exists)
    logger = EspLog.instance
    if getattr(logger, '_verbosity', None) != Verbosity.NORMAL:
        return None
    console = getattr(logger, 'stdout', None)
    return console if isinstance(console, Console) and console.is_terminal else None


class Progress:
    """The differential write's bar: a map of the region, sector by sector.

    The scan and the writes go through the region in the same pass, so a single fill would
    have to stand still either while the scan crosses changed sectors or while they are
    written. The map shows both instead: each cell is unchanged, waiting to be written,
    written, or not compared yet, in its own colour. Under it, the figures in the same colours
    double as the legend, and become the summary once the region is done. Without colour the
    cells are ``#`` written, ``~`` to write, ``=`` unchanged and ``.`` not compared yet::

        ==========######~~~~~~~~~~~~~~=========...........................   29%     4s
        64 kB written · 136 kB to write · 224 kB unchanged · of 1.00 MB

    Drawn only where it can be redrawn in place (:func:`_interactive_console`); anywhere
    else, only the summary is printed.
    """
    UNIT = DIFF_SECTOR_SIZE
    #: The longest a line gets, however wide the terminal.
    MAX_WIDTH = 120
    #: The most often the bar is redrawn, in seconds between draws.
    INTERVAL = 0.05

    UNSCANNED, UNCHANGED, TO_WRITE, WRITTEN = range(4)
    STYLES = {UNSCANNED: 'color(237)', UNCHANGED: 'color(67)', TO_WRITE: 'color(214)',
              WRITTEN: 'color(42)'}
    GLYPHS = {UNSCANNED: '.', UNCHANGED: '=', TO_WRITE: '~', WRITTEN: '#'}

    #: The bar on screen, if any: what :meth:`paused` clears for a line printed mid-write.
    live: Optional['Progress'] = None

    def __init__(self, total: int, enabled: bool = True,
                 console: Optional[Console] = None) -> None:
        self.total = total
        self.console = (console or _interactive_console()) if enabled and total > 0 else None
        self.cells = bytearray((total + self.UNIT - 1) // self.UNIT)
        self.counts = {state: 0 for state in self.GLYPHS}
        self.counts[self.UNSCANNED] = total
        self.scanned = 0
        self._started = time.monotonic()
        self._drawn_at: Optional[float] = None
        self._finished = False

    def __enter__(self) -> 'Progress':
        if self.console is not None:
            Progress.live = self
        return self

    def __exit__(self, *exc: Any) -> None:
        if Progress.live is self:
            Progress.live = None
        if self.console is not None and not self._finished and self._drawn_at is not None:
            # Failed part way: leave the map as it stands, figures and all.
            self._draw(force=True)
            self._print('\n')

    # --- what the region holds --------------------------------------------------------

    def _mark(self, start: int, end: int, state: int, over: Sequence[int] = ()) -> None:
        for cell in range(start // self.UNIT, min(-(-end // self.UNIT), len(self.cells))):
            if over and self.cells[cell] not in over:
                continue
            length = min(self.UNIT, self.total - cell * self.UNIT)
            self.counts[self.cells[cell]] -= length
            self.counts[state] += length
            self.cells[cell] = state
        self._draw()

    def compared(self, offset: int, size: int, matches: bool) -> None:
        """A sector has been compared: it is either right already or has to be written."""
        self.scanned = max(self.scanned, offset + size)
        self._mark(offset, offset + size, self.UNCHANGED if matches else self.TO_WRITE)

    def to_write(self, offset: int, length: int) -> None:
        """A run is about to be written: sectors it bridges and any not compared yet are
        written too, and what is already written stays so."""
        self._mark(offset, offset + length, self.TO_WRITE,
                   over=(self.UNSCANNED, self.UNCHANGED))

    def written(self, offset: int, length: int) -> None:
        """`length` bytes from `offset` are written. Only whole sectors count, so a run that
        is reported up to its last byte is not drawn done before it is."""
        end = offset + length
        if end < self.total:
            end -= end % self.UNIT
        self._mark(offset, end, self.WRITTEN)

    @property
    def done(self) -> int:
        """Bytes known to be right: unchanged, or written."""
        return self.counts[self.UNCHANGED] + self.counts[self.WRITTEN]

    # --- drawing ------------------------------------------------------------------------

    def figures(self) -> list[Text]:
        """What the map shows, in its colours, as the line under it."""
        shown = [(self.WRITTEN, 'written'), (self.TO_WRITE, 'to write'),
                 (self.UNCHANGED, 'unchanged')]
        parts = [self._styled(state, f'{format_size(self.counts[state])} {label}')
                 for state, label in shown if self.counts[state] or state == self.UNCHANGED]
        return parts + [Text(f'of {format_size(self.total)}')]

    def finish(self, parts: Sequence[Union[str, Text]], styles: Sequence[int] = ()) -> None:
        """End with `parts` as the line under the map, styled as the states in `styles`
        (one each, or fewer), or print them as a line of their own when there is no map."""
        self._finished = True
        texts = [self._styled(styles[i], str(part)) if i < len(styles) else Text(str(part))
                 for i, part in enumerate(parts)]
        if self.console is None or self._drawn_at is None:
            say(' · '.join(t.plain for t in texts))
            return
        self._draw(force=True, figures=texts)
        self._print('\n')

    def bar(self, width: int) -> Text:
        """The map, `width` cells wide. A cell holding anything still to write shows that,
        then anything written, so even one changed sector stays visible."""
        bar = Text()
        per = len(self.cells) / width
        for column in range(width):
            start = int(column * per)
            cells = self.cells[start:max(int((column + 1) * per), start + 1)]
            if self.TO_WRITE in cells:
                state = self.TO_WRITE
            elif self.WRITTEN in cells:
                state = self.WRITTEN
            elif 2 * cells.count(self.UNCHANGED) >= len(cells):
                state = self.UNCHANGED
            else:
                state = self.UNSCANNED
            bar.append_text(self._styled(state, '━' if self._colour else self.GLYPHS[state]))
        return bar

    @property
    def _colour(self) -> bool:
        return self.console is not None and not self.console.no_color and \
            self.console.color_system is not None

    def _styled(self, state: int, text: str) -> Text:
        return Text(text, style=self.STYLES[state] if self._colour else '')

    def _width(self) -> int:
        """The longest line that fits: a column short of the terminal, so that a full line
        never wraps, and no more than :data:`MAX_WIDTH`."""
        assert self.console is not None
        return min(self.console.width - 1, self.MAX_WIDTH)

    def lines(self, figures: Optional[Sequence[Text]] = None) -> tuple[Text, Text]:
        """The map with its percentage and time, and the figures under it, dropping the
        last figures rather than wrapping."""
        width = self._width()
        elapsed = _clock(time.monotonic() - self._started)
        tail = f'  {100 * self.done // self.total:3d}%  {elapsed:>5}'
        top = self.bar(max(10, width - len(tail)))
        top.append(tail)
        parts = list(figures if figures is not None else self.figures())
        while len(parts) > 1 and len(' · '.join(p.plain for p in parts)) > width:
            parts.pop()
        return top, Text(' · ').join(parts)

    def _draw(self, force: bool = False, figures: Optional[Sequence[Text]] = None) -> None:
        if self.console is None:
            return
        now = time.monotonic()
        if not force and self._drawn_at is not None and now - self._drawn_at < self.INTERVAL:
            return
        top, bottom = self.lines(figures)
        up = [Control((ControlType.CURSOR_UP, 1))] if self._drawn_at is not None else []
        erase = Control((ControlType.ERASE_IN_LINE, 0))
        self._print(Control(ControlType.CARRIAGE_RETURN), *up, top, erase, '\n', bottom, erase)
        self._drawn_at = now

    def _print(self, *renderables: Any) -> None:
        assert self.console is not None
        self.console.print(*renderables, sep='', end='', soft_wrap=True, highlight=False)
        self.console.file.flush()

    @classmethod
    @contextmanager
    def paused(cls) -> Iterator[None]:
        """Clear the bar for a line printed while it is up, then draw it again below."""
        bar = cls.live
        if bar is None or bar._drawn_at is None:
            yield
            return
        erase = Control((ControlType.ERASE_IN_LINE, 0))
        bar._print(Control(ControlType.CARRIAGE_RETURN), erase,
                   Control((ControlType.CURSOR_UP, 1)), erase)
        bar._drawn_at = None
        try:
            yield
        finally:
            bar._draw(force=True)


#: Called with ``(done, total)`` as esptool works through a write: how far it is, in units
#: of its own choosing (compressed bytes sent, when compressing).
OnProgress = Callable[[int, int], None]


class _ForwardedProgress:
    """The task esptool's ``log.progress`` hands back inside :func:`quiet_esptool`: what
    esptool would draw on a bar of its own goes to `on_progress` instead."""

    def __init__(self, total: int, on_progress: Optional[OnProgress]) -> None:
        self.total, self.done, self.on_progress = total, 0, on_progress

    def update(self, advance: int = 1, description: Optional[str] = None) -> None:
        self.done = max(0, min(self.done + advance, self.total))
        if self.on_progress is not None and self.total > 0:
            try:
                self.on_progress(self.done, self.total)
            except Exception:
                # Raising here would stop esptool part way through a write and leave the
                # region half written. Stop drawing instead: a fault that lasts, like a
                # closed pipe, comes back at the next draw, once esptool has returned.
                self.on_progress = None


def _quiet(original: Any, on_progress: Optional[OnProgress]) -> dict[str, Callable[..., Any]]:
    """What :func:`quiet_esptool` overrides on `original`, the logger's own class: routine
    narration goes, esptool's own bar goes to `on_progress`, and notes, warnings and errors
    are kept. They are printed through `original`'s `print`, the one silenced here, so they
    still reach whichever logger is installed, and this suppresses by kind rather than by
    matching message text. A line kept while idftool's bar is up is printed above it."""
    def keep(prefix: str, **print_options: Any) -> Callable[..., None]:
        def method(self: Any, *args: object, **kwargs: Any) -> None:
            with Progress.paused():
                original.print(self, prefix + " ".join(str(a) for a in args), **print_options)
        return method

    @contextmanager
    def progress(self: Any, total: int, *args: Any, disable: bool = False,
                 **kwargs: Any) -> Iterator[_ForwardedProgress]:
        yield _ForwardedProgress(total, None if disable else on_progress)

    return {
        "print": lambda self, *args, **kwargs: None,
        "progress": progress,
        "progress_bar": lambda self, *args, **kwargs: None,
        "stage": lambda self, *args, **kwargs: None,
        "note": keep("Note: "),
        "warning": keep("Warning: "),
        "warn": keep("Warning: "),
        "error": keep("", file=sys.stderr),
        "err": keep("", file=sys.stderr),
    }


@contextmanager
def quiet_esptool(on_progress: Optional[OnProgress] = None) -> Iterator[None]:
    """Silence esptool's routine progress while it writes, keeping notes and warnings.

    A differential write calls ``write_flash`` once per run of changed sectors, and each call
    narrates itself — erase range, compressed size, its own progress bar, bytes written,
    verification — which is six lines and a flickering bar per run. idftool draws one bar
    across the whole operation instead (:class:`Progress`), so the per-run narration has to
    go. What esptool's own bar would have shown goes to `on_progress`, so that the one bar can
    move while a run is written.
    """
    log.print  # noqa: B018  (esptool's `log` is a proxy; this makes sure its logger exists)
    target = EspLog.instance
    original = type(target)
    # A subclass of the logger's own class, so the swap is allowed whatever that class is.
    target.__class__ = type("_QuietEsptool", (original,), _quiet(original, on_progress))
    try:
        yield
    finally:
        target.__class__ = original


def write_flash(esp: ESPLoader, addr_data: Sequence[tuple[int, ImageSource]],
                flash_freq: str = 'keep', flash_mode: str = 'keep', flash_size: str = 'keep',
                **kwargs: Any) -> None:
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
    if any(address < BOOTLOADER_DESCRIPTIONS_END for address, _ in addr_data):
        forget_device_partition_table_offset(esp)
    skip_flashed = kwargs.pop('skip_flashed', False)
    diff = kwargs.pop('diff', False)
    if skip_flashed or diff:
        unavailable = flash_check_unavailable(esp, kwargs)
        if unavailable:
            say(f"Note: not checking what is already in flash, {unavailable}")
        else:
            if flash_size == 'detect':
                # The comparison needs the bootloader header esptool will write.
                flash_size = detect_flash_size(esp) or '4MB'
            addr_data = _plan_writes(esp, addr_data, diff, flash_freq, flash_mode,
                                     flash_size, kwargs)
            if not addr_data:
                return

    esptool_write_flash(esp=esp, addr_data=addr_data, flash_freq=flash_freq,
                        flash_mode=flash_mode, flash_size=flash_size, **kwargs)


def _plan_writes(esp: ESPLoader, addr_data: Sequence[tuple[int, ImageSource]], diff: bool,
                 flash_freq: str, flash_mode: str, flash_size: str,
                 kwargs: Mapping[str, Any]) -> list[tuple[int, ImageSource]]:
    """What of `addr_data` is left for esptool to write, after comparing it against flash.

    Entries compared a sector at a time are written here, as the comparison finds them, and
    do not come back. The rest are returned as they came in, so that esptool still sees the
    file names it prints and re-reads them itself.
    """
    planned: list[tuple[int, ImageSource]] = []
    for entry in addr_data:
        address, source = entry
        data, name = get_bytes(source)
        if not data:
            planned.append(entry)
            continue

        prepared = bytes_as_written(esp, address, data, flash_freq, flash_mode, flash_size)
        subject = f"'{name}' " if name is not None else ""

        # A region that does not start on a sector boundary cannot be rewritten a sector at
        # a time: the write would erase the sector it starts inside, taking whatever shares
        # it. Rare (partition offsets are sector-aligned), and the whole-region comparison
        # below still applies.
        if diff and address % DIFF_SECTOR_SIZE == 0:
            say(f"Comparing {subject}against flash at {address:#010x}...")
            if not _write_changed_sectors(esp, address, prepared, kwargs):
                planned.append(entry)   # differs throughout: esptool writes it whole
            continue

        if len(prepared) > CHECK_FIRST_CHUNK_SIZE:
            say(f"Comparing {subject}against flash at {address:#010x}...")
        if flash_matches(esp, address, prepared):
            say(f"'{name}' at {address:#010x} is already in flash, skipping write"
                if name is not None else f"Already in flash at {address:#010x}, skipping write")
        else:
            planned.append(entry)
    return planned


def _write_changed_sectors(esp: ESPLoader, address: int, prepared: bytes,
                           kwargs: Mapping[str, Any]) -> bool:
    """Compare `prepared` against flash a sector at a time, writing what differs as it goes.

    Returns False when the scan was abandoned before writing anything, leaving the caller to
    have esptool write the region whole.
    """
    written = runs = 0
    elapsed = 0.0
    abandoned = False

    with Progress(len(prepared), enabled=not kwargs.get('no_progress')) as progress:
        for offset, length, reason in plan_sector_writes(esp, address, prepared,
                                                         on_compared=progress.compared):
            if reason == 'remainder':
                abandoned = True
                if written == 0:
                    progress.finish([f"too much differs after {format_size(progress.scanned)}, "
                                     "writing it whole"], [Progress.TO_WRITE])
                    return False
            progress.to_write(offset, length)

            def on_block(done: int, total: int, offset: int = offset,
                         length: int = length) -> None:
                # esptool counts what it has sent. The run is written only once esptool
                # returns, having finished and verified it, so its last block stops short.
                progress.written(offset, min(length * done // total, length - 1))

            # `flash_size` stays 'keep': `prepared` has already been through
            # bytes_as_written, and re-detecting the size costs a round trip per run.
            started = time.time()
            with quiet_esptool(on_block):
                esptool_write_flash(
                    esp=esp, flash_size='keep',
                    addr_data=[(address + offset, prepared[offset:offset + length])], **kwargs)
            elapsed += time.time() - started
            written += length
            runs += 1
            progress.written(offset, length)

        # esptool reports what it wrote and how fast after every write; a differential one
        # is made of many, so the same figures are totalled up and reported once here.
        unchanged = f"{format_size(progress.counts[Progress.UNCHANGED])} unchanged"
        if written == 0:
            progress.finish(["already in flash", unchanged],
                            [Progress.WRITTEN, Progress.UNCHANGED])
        else:
            how = "too much differs, the rest whole" if abandoned else \
                f"{runs} region{'s' if runs != 1 else ''}"
            progress.finish([f"{format_size(written)} written ({how})", unchanged,
                             f"{elapsed:.1f} s, {_rate(written, elapsed)}"],
                            [Progress.WRITTEN, Progress.UNCHANGED])
    return True


def _rate(count: int, seconds: float) -> str:
    return f'{count / seconds * 8 / 1000:.0f} kbit/s' if seconds > 0 else 'instant'
