"""The write_flash option pass-through — library-level tests, no device and no subprocess."""
import inspect
import re

import pytest
import rich_click as click

from conftest import SAMPLES
from idftool.flash import (FLASH_OPTION_FLAGS, IDFTOOL_FLASH_OPTIONS, WRITE_FLASH_OPTIONS,
                           split_options, write_flash_options)


def test_options_are_all_real_esptool_kwargs():
    """Pin the pass-through list to esptool's own.

    ``write_flash`` reads its options with ``kwargs.get``, so one that esptool renames or
    drops would be silently ignored — exactly the failure this list exists to prevent.
    """
    from esptool.cmds import write_flash

    source = inspect.getsource(write_flash)
    accepted = set(re.findall(r'kwargs\.get\(\s*"([a-z_]+)"', source))
    assert accepted, "could not find any kwargs.get() in esptool's write_flash"
    assert set(WRITE_FLASH_OPTIONS) <= accepted, set(WRITE_FLASH_OPTIONS) - accepted


def test_untouched_flags_are_dropped():
    # What click hands a command when none of the flags were given: nothing reaches esptool,
    # so its own defaults apply. The `--x/--no-x` pairs say "untouched" with None, because
    # their False is a real answer.
    given = dict(skip_flashed=None, compress=None, encrypt=False, force=False,
                 ignore_flash_enc_efuse=False, no_progress=False)
    assert write_flash_options(given) == {}


def test_skip_flashed_false_survives():
    # --no-skip-flashed has to reach write_flash, or it could not turn off the default the
    # app commands ask for.
    assert write_flash_options({'skip_flashed': False}) == {'skip_flashed': False}
    assert write_flash_options({'skip_flashed': False}, skip_flashed=True) == \
        {'skip_flashed': False}
    assert write_flash_options({'skip_flashed': None}, skip_flashed=True) == \
        {'skip_flashed': True}


def test_set_flags_are_forwarded():
    assert write_flash_options(dict(skip_flashed=True, no_progress=True)) == \
        {'skip_flashed': True, 'no_progress': True}


def test_compress_false_becomes_no_compress():
    # esptool reads a false `compress` as "not asked either way" and compresses anyway when
    # the stub is in use, so it has to be said the other way round.
    assert write_flash_options({'compress': False}) == {'no_compress': True}
    assert write_flash_options({'compress': True}) == {'compress': True}


def test_command_defaults_are_overridable():
    assert write_flash_options({}, erase_all=True) == {'erase_all': True}
    assert write_flash_options({'erase_all': True}, erase_all=False) == {'erase_all': True}


def test_unknown_option_is_rejected():
    with pytest.raises(TypeError, match="skip_flashd"):
        write_flash_options({'skip_flashd': True})


def test_split_options_separates_the_two_families():
    flash, rest = split_options({'skip_flashed': True, 'fat_sector_size': 0x1000})
    assert flash == {'skip_flashed': True}
    assert rest == {'fat_sector_size': 0x1000}


def test_flags_and_option_names_line_up():
    known = (*WRITE_FLASH_OPTIONS, *IDFTOOL_FLASH_OPTIONS)
    for flag in FLASH_OPTION_FLAGS:
        assert flag.lstrip('-').replace('-', '_') in known


def test_idftool_options_are_not_esptool_kwargs():
    """The ones idftool implements itself must not collide with a name esptool would read."""
    assert not set(IDFTOOL_FLASH_OPTIONS) & set(WRITE_FLASH_OPTIONS)


def test_write_image_refuses_erase_with_skip_flashed():
    from idftool import write_image

    # Checked before anything connects, so no device (and no State) is needed.
    with pytest.raises(click.UsageError, match="skip_flashed"):
        write_image(None, 'flash.img', skip_flashed=True)


def test_write_image_spells_erase_all_as_erase():
    from idftool import write_image

    with pytest.raises(click.UsageError, match="erase_all"):
        write_image(None, 'flash.img', erase_all=True)


def test_write_image_allows_skip_flashed_without_the_erase():
    from idftool import write_image

    # Gets past the option check and fails on the missing image instead.
    with pytest.raises(Exception) as e:
        write_image(None, 'flash.img', erase=False, skip_flashed=True)
    assert not isinstance(e.value, click.UsageError)


class _FakeEsp:
    """ESPLoader stand-in that serves the partition table and nothing else."""
    FLASH_SECTOR_SIZE = 0x1000
    BOOTLOADER_FLASH_OFFSET = 0x0
    CHIP_NAME = "ESP32-S3"

    def __init__(self, table_offset, table_binary):
        self.table_offset = table_offset
        self.table_binary = table_binary

    def read_flash(self, offset, length, *args, **kwargs):
        from esptool.util import FatalError
        if offset == self.table_offset:
            return self.table_binary
        raise FatalError("no flash here")


def test_options_reach_esptool(monkeypatch, tmp_path):
    """End to end through a command: what the caller asked for is what write_flash gets."""
    from esp_idf_defs.partitions import PartitionTable
    import idftool.commands.nvs as nvs_command
    import idftool.state as state_module

    table = PartitionTable.from_csv((SAMPLES / "partitions.csv").read_text())
    state = state_module.State(port=None, baud=115200, no_reset=False, partition_table_file=None,
                               partition_table_offset=0x8000, partition_table_size=0xc00,
                               primary_bootloader_offset=None, recovery_bootloader_offset=None)
    state.esp = _FakeEsp(0x8000, table.to_binary())
    monkeypatch.setattr(state_module, "detect_flash_size", lambda esp: "16MB")

    written = {}
    monkeypatch.setattr(nvs_command, "write_flash",
                        lambda **kwargs: written.update(kwargs))

    nvs_command.write_nvs(state, "nvs", str(SAMPLES / "nvs.csv"),
                          skip_flashed=True, compress=False, force=False, no_progress=None)

    assert written["skip_flashed"] is True
    assert written["no_compress"] is True     # `compress=False` said esptool's way
    assert "force" not in written             # untouched flags keep esptool's defaults
    assert "no_progress" not in written


class _FakeFlash:
    """ESPLoader stand-in holding flash contents, answering the on-device MD5 command.

    Records every ``flash_md5sum`` call so a test can assert what was hashed and, more to the
    point, what was not: the comparison exists to stop early.
    """
    CHIP_NAME = "ESP32-S3"
    BOOTLOADER_FLASH_OFFSET = 0x0
    IS_STUB = True
    secure_download_mode = False

    def __init__(self, contents=b"", base=0x10000):
        self.base = base
        self.contents = bytearray(contents)
        self.calls = []

    def get_secure_boot_enabled(self):
        return False

    def flash_md5sum(self, addr, size):
        import hashlib
        self.calls.append((addr, size))
        start = addr - self.base
        region = bytes(self.contents[start:start + size])
        region += b"\xff" * (size - len(region))  # unwritten flash reads as erased
        return hashlib.md5(region).hexdigest()


def test_flash_matches_identical_content():
    from idftool.flash import flash_matches

    data = bytes(range(256)) * 400  # 100 KB
    esp = _FakeFlash(data)
    assert flash_matches(esp, 0x10000, data)
    # One sector, then 64 KB chunks, then the remainder — and every byte accounted for.
    assert [size for _, size in esp.calls] == [0x1000, 0x10000, 100 * 1024 - 0x11000]
    assert sum(size for _, size in esp.calls) == len(data)


def test_flash_matches_stops_at_the_first_differing_chunk():
    from idftool.flash import flash_matches

    data = bytes(range(256)) * 4000  # 1000 KB
    flashed = bytearray(data)
    flashed[0x800] ^= 0xFF  # a difference inside the first sector
    esp = _FakeFlash(bytes(flashed))

    assert not flash_matches(esp, 0x10000, data)
    # The whole point: one 4 KB hash, not a megabyte of them.
    assert esp.calls == [(0x10000, 0x1000)]


def test_flash_matches_finds_a_late_difference():
    from idftool.flash import flash_matches

    data = bytes(range(256)) * 4000
    flashed = bytearray(data)
    flashed[-1] ^= 0xFF  # last byte, so every chunk has to be hashed
    esp = _FakeFlash(bytes(flashed))

    assert not flash_matches(esp, 0x10000, data)
    assert sum(size for _, size in esp.calls) == len(data)


def test_flash_matches_blank_flash():
    from idftool.flash import flash_matches

    esp = _FakeFlash(b"")  # erased: reads back as 0xFF
    assert not flash_matches(esp, 0x10000, b"\x00" * 0x4000)
    assert esp.calls == [(0x10000, 0x1000)]


def test_flash_matches_shorter_than_one_chunk():
    from idftool.flash import flash_matches

    data = b"partition table"
    esp = _FakeFlash(data)
    assert flash_matches(esp, 0x10000, data)
    assert esp.calls == [(0x10000, len(data))]


@pytest.mark.parametrize("options, reason", [
    ({'erase_all': True}, 'erased'),
    ({'encrypt': True}, 'encrypted'),
    ({'encrypt_files': [(0, b'x')]}, 'encrypted'),
    ({'diff_with': ['old.bin']}, 'differential'),
])
def test_flash_check_is_unavailable_when_it_would_lie(options, reason):
    from idftool.flash import flash_check_unavailable

    assert reason in flash_check_unavailable(_FakeFlash(), options)


def test_flash_check_is_available_by_default():
    from idftool.flash import flash_check_unavailable

    assert flash_check_unavailable(_FakeFlash(), {}) is None


def test_flash_check_needs_a_readable_flash():
    from idftool.flash import flash_check_unavailable

    esp = _FakeFlash()
    esp.secure_download_mode = True
    assert 'secure download' in flash_check_unavailable(esp, {})

    esp = _FakeFlash()
    esp.CHIP_NAME, esp.IS_STUB = 'ESP8266', False
    assert 'ESP8266' in flash_check_unavailable(esp, {})


def test_write_flash_skips_the_files_already_there(monkeypatch, capsys):
    import idftool.flash as flash_module

    same, different = b"\xa5" * 0x2000, bytes(range(256)) * 32
    esp = _FakeFlash(same + b"\x00" * 0x2000, base=0x10000)

    forwarded = {}
    monkeypatch.setattr(flash_module, "esptool_write_flash",
                        lambda **kwargs: forwarded.update(kwargs))

    flash_module.write_flash(esp=esp, addr_data=[(0x10000, same), (0x12000, different)],
                             skip_flashed=True)

    # Only the file that really differs is handed on, and esptool is not asked to repeat
    # the comparison that was just done.
    assert forwarded['addr_data'] == [(0x12000, different)]
    assert 'skip_flashed' not in forwarded
    assert "already in flash" in capsys.readouterr().out.lower()


def test_write_flash_is_not_called_when_everything_matches(monkeypatch):
    import idftool.flash as flash_module

    data = b"\xa5" * 0x2000
    esp = _FakeFlash(data, base=0x10000)
    called = []
    monkeypatch.setattr(flash_module, "esptool_write_flash",
                        lambda **kwargs: called.append(kwargs))

    flash_module.write_flash(esp=esp, addr_data=[(0x10000, data)], skip_flashed=True)
    assert called == []


def test_write_flash_without_skip_flashed_hashes_nothing(monkeypatch):
    import idftool.flash as flash_module

    esp = _FakeFlash(b"\xa5" * 0x2000, base=0x10000)
    monkeypatch.setattr(flash_module, "esptool_write_flash", lambda **kwargs: None)

    flash_module.write_flash(esp=esp, addr_data=[(0x10000, b"\xa5" * 0x2000)])
    assert esp.calls == []


def test_write_flash_says_why_it_did_not_check(monkeypatch, capsys):
    import idftool.flash as flash_module

    esp = _FakeFlash(b"\xa5" * 0x2000, base=0x10000)
    monkeypatch.setattr(flash_module, "esptool_write_flash", lambda **kwargs: None)

    flash_module.write_flash(esp=esp, addr_data=[(0x10000, b"\xa5" * 0x2000)],
                             skip_flashed=True, erase_all=True)
    assert esp.calls == []
    assert "erased" in capsys.readouterr().out


def test_app_commands_skip_by_default():
    """factory and ota ask for the check themselves."""
    import inspect
    import idftool.commands.firmware as firmware

    for name in ('flash_factory', 'flash_ota'):
        source = inspect.getsource(getattr(firmware, name))
        assert 'skip_flashed=True' in source, name


# --- the differential scan -------------------------------------------------------------

def _region(sectors, sector_size=0x1000):
    """A region of `sectors` distinguishable sectors."""
    # Never zero, so a sector is always distinguishable from erased-to-zero test flash.
    return b"".join(bytes([i % 251 + 1]) * sector_size for i in range(sectors))


def _dirty(data, *sectors):
    """`data` with the first byte of each named sector flipped."""
    out = bytearray(data)
    for sector in sectors:
        out[sector * 0x1000] ^= 0xFF
    return bytes(out)


def _plan(esp, data, **kwargs):
    from idftool.flash import plan_sector_writes
    return list(plan_sector_writes(esp, 0x10000, data, **kwargs))


def test_plan_merges_consecutive_changed_sectors():
    data = _region(64)
    esp = _FakeFlash(_dirty(data, 5, 6, 7, 40))

    assert _plan(esp, data) == [(5 * 0x1000, 3 * 0x1000, 'changed'),
                                (40 * 0x1000, 0x1000, 'changed')]
    assert len(esp.calls) == 64  # the whole region, a sector at a time


def test_plan_bridges_a_small_gap_but_not_a_large_one():
    """Starting another write costs more than rewriting a sector or two in the run."""
    data = _region(64)

    # Two matching sectors between: cheaper to rewrite them than to start a second write.
    esp = _FakeFlash(_dirty(data, 10, 13))
    assert _plan(esp, data) == [(10 * 0x1000, 4 * 0x1000, 'changed')]

    # Three between: cheaper to stop and start again.
    esp = _FakeFlash(_dirty(data, 10, 14))
    assert _plan(esp, data) == [(10 * 0x1000, 0x1000, 'changed'),
                                (14 * 0x1000, 0x1000, 'changed')]


def test_plan_yields_nothing_when_identical():
    data = _region(64)
    assert _plan(_FakeFlash(data), data) == []


def test_plan_abandons_a_region_that_differs_throughout():
    from idftool.flash import DIFF_MIN_SAMPLE_FRACTION

    data = _region(256)  # 1 MB -> minimum sample is an eighth of it, 32 sectors
    esp = _FakeFlash(bytes(len(data)))

    assert _plan(esp, data) == [(0, len(data), 'remainder')]
    # Given up at the first opportunity, not after hashing the lot.
    assert len(esp.calls) == 256 // DIFF_MIN_SAMPLE_FRACTION


def _mostly_changed_after_a_clean_patch():
    """A region whose first sectors differ, then match, then differ all the way out.

    The early run is found and written before the rule has seen enough to give up, so this
    is the case where abandoning happens with writes already done.
    """
    data = _region(256)
    flashed = bytearray(data)
    for sector in (0, 1):
        flashed[sector * 0x1000] ^= 0xFF        # an early run, flushed by the clean sectors
    flashed[6 * 0x1000:] = bytes(250 * 0x1000)  # and nothing matches from here on
    return data, _FakeFlash(bytes(flashed))


def test_plan_hands_back_only_what_is_left_when_it_abandons():
    """Sectors already written stay written; the remainder starts where the scan stopped."""
    data, esp = _mostly_changed_after_a_clean_patch()

    runs = _plan(esp, data)
    assert runs[0] == (0, 2 * 0x1000, 'changed')
    offset, length, reason = runs[-1]
    assert reason == 'remainder'
    assert offset == 6 * 0x1000                  # from where the changes resumed
    assert offset + length == len(data)          # out to the end


def test_a_version_bump_is_not_mistaken_for_a_rewrite():
    """The case the minimum sample exists for.

    A rebuild that changes only the version string rewrites the app descriptor in sector 0
    and leaves everything else alone. Judged on its first sector that image looks entirely
    changed — and giving up there would rewrite a megabyte to avoid writing 4 KB.
    """
    data = _region(256)
    flashed = bytearray(data)
    flashed[0x20:0x120] = b"\xa5" * 0x100  # the descriptor, and nothing else
    esp = _FakeFlash(bytes(flashed))

    assert _plan(esp, data) == [(0, 0x1000, 'changed')]
    assert len(esp.calls) == 256  # scanned to the end rather than giving up on sector 0


def test_a_late_run_of_differences_does_not_abandon_the_scan():
    """What a fixed 'quit after N differences' counter would get wrong.

    Everything past the halfway mark differs, which is well over the 0.70 the rule starts
    at — but by the time it is seen, most of the hashing is spent and finishing is cheaper.
    """
    data = _region(256)
    flashed = bytearray(data)
    flashed[128 * 0x1000:] = bytes(128 * 0x1000)
    esp = _FakeFlash(bytes(flashed))

    runs = _plan(esp, data)
    assert [reason for _, _, reason in runs] == ['changed']
    assert runs[0] == (128 * 0x1000, 128 * 0x1000, 'changed')


def test_the_bail_threshold_relaxes_as_the_scan_proceeds():
    """The documented shape: eager to give up early, reluctant late."""
    from idftool.flash import DIFF_COST_RATIO

    def threshold(fraction):
        return 1 - DIFF_COST_RATIO * (1 - fraction)

    assert threshold(0.125) == pytest.approx(0.70, abs=0.005)
    assert threshold(0.5) == pytest.approx(0.83, abs=0.005)
    assert threshold(0.9) == pytest.approx(0.97, abs=0.005)
    # Never below the cheapest transport's break-even, or it would give up on scans that pay.
    assert threshold(0) > 0.65


def test_the_scan_reports_each_sector_before_the_run_it_closes():
    """So the map shows a sector compared before the write that sector set off."""
    from idftool.flash import plan_sector_writes

    data = _region(12)
    events = []
    for write in plan_sector_writes(_FakeFlash(_dirty(data, 2, 10)), 0x10000, data,
                                    on_compared=lambda *seen: events.append(seen)):
        events.append(write)

    def compared(*sectors):
        return [(n * 0x1000, 0x1000, n not in (2, 10)) for n in sectors]

    assert events == [
        *compared(0, 1, 2, 3, 4, 5), (0x2000, 0x1000, 'changed'),
        *compared(6, 7, 8, 9, 10, 11), (0xa000, 0x1000, 'changed'),
    ]


# --- what reaches esptool --------------------------------------------------------------

def _record_writes(monkeypatch):
    import idftool.flash as flash_module
    calls = []
    monkeypatch.setattr(flash_module, "esptool_write_flash",
                        lambda **kwargs: calls.append(kwargs))
    return flash_module, calls


def test_changed_sectors_are_written_as_they_are_found(monkeypatch, capsys):
    flash_module, calls = _record_writes(monkeypatch)
    data = _region(64)
    esp = _FakeFlash(_dirty(data, 5, 6, 40))

    flash_module.write_flash(esp=esp, addr_data=[(0x10000, data)], diff=True)

    # One write per run, each its own call so esptool verifies what it wrote rather than
    # the whole region.
    assert [call['addr_data'] for call in calls] == [
        [(0x10000 + 5 * 0x1000, data[5 * 0x1000:7 * 0x1000])],
        [(0x10000 + 40 * 0x1000, data[40 * 0x1000:41 * 0x1000])],
    ]
    assert "12 kB written (2 regions) · 244 kB unchanged" in capsys.readouterr().out


def _write_like_esptool(monkeypatch, on_block=lambda: None):
    """Stand in for esptool's write_flash, drawing its progress the way esptool 5.6 does:
    one ``log.progress`` per call, advanced a block at a time. `on_block` runs once as the
    write starts and again after every block."""
    import idftool.flash as flash_module
    from esptool.logger import log

    calls = []

    def write(addr_data, no_progress=False, **kwargs):
        calls.append(dict(kwargs, addr_data=addr_data, no_progress=no_progress))
        (_, run), = addr_data
        on_block()
        with log.progress(total=len(run), disable=no_progress, unit="B") as task:
            for _ in range(0, len(run), 0x1000):
                task.update(advance=0x1000, description="Writing")
                on_block()

    monkeypatch.setattr(flash_module, "esptool_write_flash", write)
    return flash_module, calls


def _terminal(monkeypatch, width=100, colour=False):
    """A terminal for the bar to draw on, read back with :func:`_screen`. Every change is
    drawn, so that what the screen shows is what the bar holds."""
    import io
    import idftool.flash as flash_module
    from rich.console import Console

    monkeypatch.setattr(flash_module.Progress, "INTERVAL", 0)
    console = Console(file=io.StringIO(), force_terminal=True, width=width,
                      color_system="256" if colour else None)
    monkeypatch.setattr(flash_module, "_interactive_console", lambda: console)
    return console


def _screen(console):
    """The lines a terminal shows after what was printed on `console`: carriage returns,
    cursor-up and erase-line applied, colours dropped."""
    lines, row, col = [[]], 0, 0
    for match in re.finditer(r"\x1b\[([\d;]*)([A-Za-z])|(.)", console.file.getvalue(), re.S):
        count, command, char = match.groups()
        if command == "A":
            row = max(0, row - int(count or 1))
        elif command == "K":
            del lines[row][col:]
        elif command:
            pass                                    # colours
        elif char == "\r":
            col = 0
        elif char == "\n":
            row, col = row + 1, 0
            lines.extend([] for _ in range(row + 1 - len(lines)))
        else:
            line = lines[row]
            line.extend(" " * (col - len(line)))
            line[col:col + 1] = [char]
            col += 1
    return ["".join(line).rstrip() for line in lines if "".join(line).strip()]


def test_the_map_shows_what_was_written_and_what_was_left(monkeypatch):
    console = _terminal(monkeypatch, width=81)
    flash_module, _ = _write_like_esptool(monkeypatch)
    data = _region(64)

    flash_module.write_flash(esp=_FakeFlash(_dirty(data, *range(32, 48))),
                             addr_data=[(0x10000, data)], diff=True)

    top, bottom = _screen(console)
    bar = top.split()[0]
    assert len(top) == 80                                   # a column short of the terminal
    assert set(bar[:len(bar) * 9 // 20]) == {"="} and set(bar[len(bar) * 4 // 5:]) == {"="}
    assert "#" * (len(bar) // 4 - 2) in bar and set(bar) == {"=", "#"}
    assert top.endswith("100%     0s")
    assert bottom.startswith("64 kB written (1 region) · 192 kB unchanged · ")


def test_changed_sectors_are_marked_to_write_until_written(monkeypatch):
    """The scan reaches a run before it is written: the bar moves on regardless, with the run
    shown waiting, and fills it in as esptool writes it."""
    console = _terminal(monkeypatch)
    screens = []
    flash_module, _ = _write_like_esptool(monkeypatch, lambda: screens.append(_screen(console)))
    data = _region(64)

    flash_module.write_flash(esp=_FakeFlash(_dirty(data, *range(4, 12))),
                             addr_data=[(0x10000, data)], diff=True)

    first, last = screens[0], screens[-1]                   # as the write starts, at its end
    assert "~" in first[0] and "#" not in first[0]
    assert first[1].startswith("32 kB to write · ")
    assert "#" in last[0] and last[1].startswith("28 kB written · 4 kB to write · ")
    written = [int(re.match(r"(\d+) kB written", bottom).group(1))
               for _, bottom in screens[2:]]
    assert written == sorted(written) and len(set(written)) >= 6


def test_one_changed_sector_shows_however_large_the_region(monkeypatch):
    from idftool.flash import Progress

    console = _terminal(monkeypatch, width=41)
    with Progress(1024 * 0x1000) as progress:
        for n in range(1024):
            progress.compared(n * 0x1000, 0x1000, n != 500)
        progress.finish(["done"])
    bar = _screen(console)[0].split()[0]
    assert bar.count("~") == 1 and set(bar) == {"=", "~"}


def test_a_run_is_not_drawn_written_before_esptool_returns(monkeypatch):
    """esptool reports a run's last block before it has finished and verified the write."""
    from idftool.flash import Progress

    _terminal(monkeypatch)
    with Progress(8 * 0x1000) as progress:
        progress.to_write(4 * 0x1000, 4 * 0x1000)
        progress.written(4 * 0x1000, 4 * 0x1000 - 1)        # every block sent
        assert progress.counts[Progress.WRITTEN] == 3 * 0x1000
        progress.written(4 * 0x1000, 4 * 0x1000)            # and esptool returned
        assert progress.counts[Progress.WRITTEN] == 4 * 0x1000
        progress.finish(["done"])


def test_a_run_that_fails_after_its_last_block_is_not_drawn_done(monkeypatch):
    console = _terminal(monkeypatch)
    blocks = []
    changed = range(60, 64)

    def fail_verifying():
        blocks.append(None)
        if len(blocks) == 1 + len(changed):     # the start, then every block of the run
            raise RuntimeError("MD5 of file does not match data in flash!")

    flash_module, _ = _write_like_esptool(monkeypatch, fail_verifying)
    data = _region(64)

    with pytest.raises(RuntimeError):
        flash_module.write_flash(esp=_FakeFlash(_dirty(data, *changed)),
                                 addr_data=[(0x10000, data)], diff=True)
    top, bottom = _screen(console)
    assert top.endswith(" 98%     0s") and bottom.startswith("12 kB written · 4 kB to write · ")


def test_an_abandoned_scan_that_wrote_its_remainder_is_drawn_done(monkeypatch):
    console = _terminal(monkeypatch)
    flash_module, _ = _write_like_esptool(monkeypatch)
    data, esp = _mostly_changed_after_a_clean_patch()

    flash_module.write_flash(esp=esp, addr_data=[(0x10000, data)], diff=True)

    top, bottom = _screen(console)
    assert "100%" in top and "too much differs, the rest whole" in bottom


def test_an_abandoned_scan_that_wrote_nothing_says_why(monkeypatch):
    console = _terminal(monkeypatch)
    flash_module, calls = _write_like_esptool(monkeypatch)
    data = _region(256)

    flash_module.write_flash(esp=_FakeFlash(bytes(len(data))), addr_data=[(0x10000, data)],
                             diff=True)

    assert _screen(console)[-1].startswith("too much differs after ")
    assert [call["addr_data"] for call in calls] == [[(0x10000, data)]]


def test_a_bar_that_cannot_draw_does_not_cut_a_run_short(monkeypatch):
    """A run cut part way leaves the slot half written, so a draw that fails while a run is
    written stops the write only once the run is finished."""
    from idftool.flash import Progress

    _terminal(monkeypatch)
    broken, blocks = [], []
    draw = Progress._print

    def print_or_break(self, *renderables):
        if broken:
            raise BrokenPipeError
        draw(self, *renderables)

    monkeypatch.setattr(Progress, "_print", print_or_break)
    monkeypatch.setattr(Progress, "INTERVAL", 0)
    flash_module, _ = _write_like_esptool(
        monkeypatch, lambda: (blocks.append(None), broken.append(True)))
    data = _region(64)
    changed = range(4, 12)

    with pytest.raises(BrokenPipeError):
        flash_module.write_flash(esp=_FakeFlash(_dirty(data, *changed)),
                                 addr_data=[(0x10000, data)], diff=True)
    assert len(blocks) == 1 + len(changed)     # the start, then every block of the run


def test_a_warning_mid_write_is_printed_above_the_bar(monkeypatch):
    from esp_pylib.logger import EspLog
    from esptool.logger import log

    console = _terminal(monkeypatch)
    log.print  # noqa: B018  (makes sure esptool's logger exists)
    monkeypatch.setattr(type(EspLog.instance), "stdout", property(lambda self: console))
    warned = []

    def warn_once():
        if not warned:
            warned.append(log.warning("flash chip is getting warm"))

    flash_module, _ = _write_like_esptool(monkeypatch, warn_once)
    data = _region(64)

    flash_module.write_flash(esp=_FakeFlash(_dirty(data, *range(4, 12))),
                             addr_data=[(0x10000, data)], diff=True)

    screen = _screen(console)
    assert screen[0].startswith("Comparing against flash")
    assert screen[1] == "Warning: flash chip is getting warm"
    assert screen[3].startswith("32 kB written") and len(screen) == 4


@pytest.mark.parametrize("width, longest", [(60, 59), (81, 80), (200, 120)])
def test_no_line_is_wider_than_the_terminal_or_120(monkeypatch, width, longest):
    console = _terminal(monkeypatch, width=width)
    flash_module, _ = _write_like_esptool(monkeypatch)
    data = _region(256)

    flash_module.write_flash(esp=_FakeFlash(_dirty(data, *range(40, 90))),
                             addr_data=[(0x10000, data)], diff=True)

    top, bottom = _screen(console)
    assert len(top) == longest and len(bottom) <= longest


def test_the_map_is_drawn_in_colour_where_there_is_colour(monkeypatch):
    console = _terminal(monkeypatch, colour=True)
    flash_module, _ = _write_like_esptool(monkeypatch)
    data = _region(64)

    flash_module.write_flash(esp=_FakeFlash(_dirty(data, 5)), addr_data=[(0x10000, data)],
                             diff=True)

    out = console.file.getvalue()
    assert "━" in out and "#" not in out
    assert "\x1b[38;5;42m" in out and "\x1b[38;5;67m" in out     # written, unchanged


class _FakeLoader(_FakeFlash):
    """A _FakeFlash that esptool's own write_flash can write to over the stub's compressed
    commands. Writes land in `contents`, so esptool's verification reads back what it wrote.
    `on_block` runs as a write starts, after every block and as it finishes."""
    FLASH_WRITE_SIZE = 0x1000
    FLASH_SECTOR_SIZE = 0x1000
    FLASH_32BIT_ADDR_SUPPORTED = False
    WRITE_FLASH_ATTEMPTS = 2

    def on_block(self):
        pass

    def flash_set_parameters(self, size):
        pass

    def get_flash_encryption_enabled(self):
        return False

    def get_secure_boot_v1_enabled(self):
        return False

    def get_encrypted_download_disabled(self):
        return False

    def flash_id(self, cache=True):
        """A 4 MB chip: esptool reads the size from the capacity byte, bits 16 to 23."""
        from esptool.cmds import DETECTED_FLASH_SIZES
        return next(code for code, size in DETECTED_FLASH_SIZES.items() if size == "4MB") << 16

    def flash_defl_begin(self, size, compsize, offset, encrypted_write=False):
        import zlib
        self._at, self._inflate = offset - self.base, zlib.decompressobj()
        self.on_block()

    def flash_defl_block(self, data, seq, timeout=None):
        chunk = self._inflate.decompress(data)
        self.contents[self._at:self._at + len(chunk)] = chunk
        self._at += len(chunk)
        self.on_block()

    def flash_defl_finish(self, reboot=False, timeout=None):
        self.on_block()


def test_esptools_own_write_moves_the_bar_and_completes(monkeypatch):
    """Pins what quiet_esptool hands esptool against esptool's real write loop, where a
    mismatch would abort a write part way and leave the slot half written."""
    import random
    import idftool.flash as flash_module

    console = _terminal(monkeypatch)
    pick = random.Random(0).choice
    data = bytes(pick(b"0123456789abcdef") for _ in range(64 * 0x1000))
    esp = _FakeLoader(_dirty(data, *range(4, 12)))
    screens = []
    esp.on_block = lambda: screens.append(_screen(console))

    flash_module.write_flash(esp=esp, addr_data=[(0x10000, data)], diff=True)

    assert bytes(esp.contents) == data
    written = [int(m.group(1)) for _, bottom in screens
               if (m := re.match(r"(\d+) kB written", bottom))]
    assert len(set(written)) > 1 and written == sorted(written)


def test_an_abandoned_scan_writes_the_region_whole(monkeypatch, capsys):
    flash_module, calls = _record_writes(monkeypatch)
    data = _region(256)
    esp = _FakeFlash(bytes(len(data)))

    flash_module.write_flash(esp=esp, addr_data=[(0x10000, data)], diff=True)

    # Nothing had been written when it gave up, so the entry goes through as it came in —
    # letting esptool compress and write it in one go rather than sector by sector.
    assert [call['addr_data'] for call in calls] == [[(0x10000, data)]]
    assert "already in flash" not in capsys.readouterr().out


def test_abandoning_after_writing_finishes_the_job(monkeypatch, capsys):
    """Giving up mid-scan must still leave the region correct, not half-written."""
    flash_module, calls = _record_writes(monkeypatch)
    data, esp = _mostly_changed_after_a_clean_patch()

    flash_module.write_flash(esp=esp, addr_data=[(0x10000, data)], diff=True)

    written = [entry for call in calls for entry in call['addr_data']]
    assert written == [(0x10000, data[:2 * 0x1000]),
                       (0x10000 + 6 * 0x1000, data[6 * 0x1000:])]
    # Every byte of the region is either written here or was checked and matched.
    assert "too much differs, the rest whole" in capsys.readouterr().out


@pytest.mark.parametrize("option", ['no_progress', 'compress', 'force'])
def test_diff_forwards_the_other_write_options_to_each_run(monkeypatch, option):
    """Each run is its own write_flash call, so the options have to be passed on."""
    flash_module, calls = _record_writes(monkeypatch)
    data = _region(64)
    esp = _FakeFlash(_dirty(data, 5))

    flash_module.write_flash(esp=esp, addr_data=[(0x10000, data)], diff=True, **{option: True})
    assert calls and all(call[option] is True for call in calls)


def test_diff_skips_a_region_that_matches_entirely(monkeypatch, capsys):
    flash_module, calls = _record_writes(monkeypatch)
    data = _region(64)

    flash_module.write_flash(esp=_FakeFlash(data), addr_data=[(0x10000, data)], diff=True)
    assert calls == []
    assert "already in flash" in capsys.readouterr().out


def test_diff_leaves_an_unaligned_region_alone(monkeypatch):
    """Rewriting a sector at a time would erase the sector the region starts inside."""
    flash_module, calls = _record_writes(monkeypatch)
    data = _region(8)
    esp = _FakeFlash(data, base=0x10800)

    flash_module.write_flash(esp=esp, addr_data=[(0x10800, data)], diff=True)
    # Fell back to the whole-region comparison: chunked, not sector by sector.
    assert [size for _, size in esp.calls] == [0x1000, 0x7000]


def test_app_commands_diff_by_default():
    import inspect
    import idftool.commands.firmware as firmware

    for name in ('flash_factory', 'flash_ota'):
        assert 'diff=True' in inspect.getsource(getattr(firmware, name)), name


# --- output -----------------------------------------------------------------------------

def test_without_a_terminal_only_the_summary_is_printed(monkeypatch, capsys):
    flash_module, _ = _write_like_esptool(monkeypatch)
    data = _region(64)

    flash_module.write_flash(esp=_FakeFlash(_dirty(data, 5)), addr_data=[(0x10000, data)],
                             diff=True)
    comparing, summary = capsys.readouterr().out.splitlines()
    assert comparing == "Comparing against flash at 0x00010000..."
    assert summary.startswith("4 kB written (1 region) · 252 kB unchanged · ")


def test_no_progress_draws_no_bar(monkeypatch, capsys):
    console = _terminal(monkeypatch)
    flash_module, _ = _write_like_esptool(monkeypatch)
    data = _region(64)

    flash_module.write_flash(esp=_FakeFlash(_dirty(data, 5)), addr_data=[(0x10000, data)],
                             diff=True, no_progress=True)
    assert console.file.getvalue() == ""
    assert "4 kB written (1 region)" in capsys.readouterr().out


def test_progress_obeys_esptools_verbosity(capsys):
    from esptool.logger import log
    from idftool.flash import _interactive_console

    log.set_verbosity('silent')
    try:
        assert _interactive_console() is None
    finally:
        log.set_verbosity('auto')


def test_progress_redraws_at_most_every_interval(monkeypatch):
    """A megabyte is 256 sectors, compared faster than a terminal is worth redrawing."""
    import idftool.flash as flash_module
    from idftool.flash import Progress

    console = _terminal(monkeypatch)
    monkeypatch.setattr(Progress, "INTERVAL", 0.05)
    monkeypatch.setattr(flash_module.time, "monotonic", lambda: 100.0)
    with Progress(256 * 0x1000) as progress:
        for n in range(256):
            progress.compared(n * 0x1000, 0x1000, True)
        assert console.file.getvalue().count("%") == 1
        progress.finish(["done"])
    assert console.file.getvalue().count("%") == 2          # and the last, whenever it comes


def test_sizes_read_as_a_person_would_write_them():
    from idftool.flash import format_size

    assert [format_size(n) for n in (512, 0x1000, 0x40000, 0x100000, 0x180000)] == \
        ["512 B", "4 kB", "256 kB", "1.00 MB", "1.50 MB"]


def test_quiet_esptool_keeps_what_matters_and_restores_itself(capsys):
    from esptool.logger import log
    from idftool.flash import quiet_esptool

    before = type(log)
    with quiet_esptool():
        log.print("routine chatter")
        with log.progress(total=2, unit="B") as task:     # how esptool draws a write's bar
            task.update()
        log.warning("something worth knowing")
    assert type(log) is before

    out = capsys.readouterr().out
    assert "routine chatter" not in out
    assert "%" not in out
    assert "something worth knowing" in out


def test_an_attached_state_drives_the_callers_connection_and_never_resets(monkeypatch):
    import types

    from esp_idf_defs.partitions import PartitionTable
    import idftool.state as state_module

    table = PartitionTable.from_csv((SAMPLES / "partitions.csv").read_text())
    esp = _FakeEsp(0x8000, table.to_binary())
    esp.serial_port = "/dev/ttyACM0"
    esp._port = types.SimpleNamespace(baudrate=921600)
    monkeypatch.setattr(state_module, "get_esp", lambda **_kwargs: pytest.fail("must not connect"))

    state = state_module.State.attached(esp)

    assert state.connect() is esp
    assert (state.port, state.baud, state.no_reset) == ("/dev/ttyACM0", 921600, True)
    assert state.primary_bootloader_offset == esp.BOOTLOADER_FLASH_OFFSET


def test_an_image_is_written_as_one_region_per_partition():
    import types

    from idftool.commands.images import image_regions

    def partition(offset, size):
        return types.SimpleNamespace(offset=offset, size=size)

    image = bytes(range(256)) * (0x30000 // 256)
    regions = image_regions(image, 0x0, [
        partition(0x0, 0x8000),        # bootloader
        partition(0x8000, 0xc00),      # partition table, ending inside a sector
        partition(0x9000, 0x7000),     # nvs
        partition(0x10000, 0x100000),  # app, running past the image's end
        None,
    ], 0x1000)

    assert [(offset, len(data)) for offset, data in regions] == [
        (0x0, 0x8000), (0x8000, 0x1000), (0x9000, 0x7000), (0x10000, 0x20000),
    ]
    assert b"".join(data for _, data in regions) == image
