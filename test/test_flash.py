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
    assert "already in flash" in capsys.readouterr().out


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
    """factory and ota ask for the check themselves; everything else leaves it off."""
    import inspect
    import idftool.commands.firmware as firmware

    for name in ('factory', 'ota'):
        source = inspect.getsource(getattr(firmware, name))
        assert 'skip_flashed=True' in source, name


# --- the differential scan -------------------------------------------------------------

def _region(sectors, sector_size=0x1000):
    """A region of `sectors` distinguishable sectors."""
    # Never zero, so a sector is always distinguishable from erased-to-zero test flash.
    return b"".join(bytes([i % 251 + 1]) * sector_size for i in range(sectors))


def test_changed_sectors_finds_exactly_what_differs():
    from idftool.flash import changed_sectors

    data = _region(256)  # 1 MB
    flashed = bytearray(data)
    for sector in (3, 4, 200):
        flashed[sector * 0x1000] ^= 0xFF
    esp = _FakeFlash(bytes(flashed))

    assert changed_sectors(esp, 0x10000, data) == [3 * 0x1000, 4 * 0x1000, 200 * 0x1000]
    assert len(esp.calls) == 256  # the whole region, a sector at a time


def test_changed_sectors_reports_nothing_when_identical():
    from idftool.flash import changed_sectors

    data = _region(64)
    assert changed_sectors(_FakeFlash(data), 0x10000, data) == []


def test_changed_sectors_abandons_a_region_that_differs_throughout():
    from idftool.flash import changed_sectors, DIFF_MIN_SAMPLE_FRACTION

    data = _region(256)  # 1 MB -> minimum sample is 1/8 of it, 32 sectors
    esp = _FakeFlash(bytes(len(data)))  # nothing matches

    assert changed_sectors(esp, 0x10000, data) is None
    # Abandoned at the first opportunity, not after hashing the lot.
    assert len(esp.calls) == 256 // DIFF_MIN_SAMPLE_FRACTION


def test_a_version_bump_is_not_mistaken_for_a_rewrite():
    """The case the minimum sample exists for.

    A rebuild that changes only the version string rewrites the app descriptor in sector 0
    and leaves everything else alone. Judged on its first sector that image looks entirely
    changed — and abandoning there would rewrite a megabyte to avoid writing 4 KB.
    """
    from idftool.flash import changed_sectors

    data = _region(256)
    flashed = bytearray(data)
    flashed[0x20:0x120] = b"\xa5" * 0x100  # the descriptor, and nothing else
    esp = _FakeFlash(bytes(flashed))

    assert changed_sectors(esp, 0x10000, data) == [0]
    assert len(esp.calls) == 256  # scanned to the end rather than giving up on sector 0


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


def test_a_late_run_of_differences_does_not_abandon_the_scan():
    """What a fixed 'quit after N differences' counter would get wrong.

    Everything past the halfway mark differs, which is well over the 0.70 the rule starts
    at — but by the time it is seen, most of the hashing is spent and finishing is cheaper.
    """
    from idftool.flash import changed_sectors

    data = _region(256)
    flashed = bytearray(data)
    flashed[128 * 0x1000:] = bytes(128 * 0x1000)
    esp = _FakeFlash(bytes(flashed))

    offsets = changed_sectors(esp, 0x10000, data)
    assert offsets is not None and len(offsets) == 128


def test_sector_ranges_merges_consecutive_sectors():
    from idftool.flash import sector_ranges

    assert sector_ranges([0x0, 0x1000, 0x3000], 0x4000) == [(0x0, 0x2000), (0x3000, 0x1000)]
    assert sector_ranges([0x2000, 0x0, 0x1000], 0x3000) == [(0x0, 0x3000)]  # sorted first
    assert sector_ranges([], 0x3000) == []


def test_sector_ranges_does_not_run_past_the_end():
    from idftool.flash import sector_ranges

    # A region that does not fill its last sector is written short, not padded.
    assert sector_ranges([0x3000], 0x3800) == [(0x3000, 0x800)]


def test_write_flash_hands_on_only_the_changed_sectors(monkeypatch, capsys):
    import idftool.flash as flash_module

    data = _region(64)
    flashed = bytearray(data)
    flashed[5 * 0x1000] ^= 0xFF
    flashed[6 * 0x1000] ^= 0xFF
    flashed[40 * 0x1000] ^= 0xFF
    esp = _FakeFlash(bytes(flashed))

    forwarded = {}
    monkeypatch.setattr(flash_module, "esptool_write_flash",
                        lambda **kwargs: forwarded.update(kwargs))
    flash_module.write_flash(esp=esp, addr_data=[(0x10000, data)], diff=True)

    # Two runs: the consecutive pair merged, and the lone one on its own. Sent as separate
    # entries so esptool verifies what it wrote rather than the whole region.
    assert forwarded['addr_data'] == [
        (0x10000 + 5 * 0x1000, data[5 * 0x1000:7 * 0x1000]),
        (0x10000 + 40 * 0x1000, data[40 * 0x1000:41 * 0x1000]),
    ]
    assert "3 of 64 sectors differ" in capsys.readouterr().out


def test_diff_falls_back_to_a_whole_region_write_when_abandoned(monkeypatch, capsys):
    import idftool.flash as flash_module

    data = _region(256)
    esp = _FakeFlash(bytes(len(data)))
    forwarded = {}
    monkeypatch.setattr(flash_module, "esptool_write_flash",
                        lambda **kwargs: forwarded.update(kwargs))

    flash_module.write_flash(esp=esp, addr_data=[(0x10000, data)], diff=True)

    assert forwarded['addr_data'] == [(0x10000, data)]  # the entry as it came in
    assert "differs too widely" in capsys.readouterr().out


def test_diff_skips_a_region_that_matches_entirely(monkeypatch):
    import idftool.flash as flash_module

    data = _region(64)
    esp = _FakeFlash(data)
    called = []
    monkeypatch.setattr(flash_module, "esptool_write_flash",
                        lambda **kwargs: called.append(kwargs))

    flash_module.write_flash(esp=esp, addr_data=[(0x10000, data)], diff=True)
    assert called == []


def test_diff_leaves_an_unaligned_region_alone(monkeypatch):
    """Rewriting a sector at a time would erase the sector the region starts inside."""
    import idftool.flash as flash_module

    data = _region(8)
    esp = _FakeFlash(data, base=0x10800)
    monkeypatch.setattr(flash_module, "esptool_write_flash", lambda **kwargs: None)

    flash_module.write_flash(esp=esp, addr_data=[(0x10800, data)], diff=True)
    # Fell back to the whole-region comparison: chunked, not sector by sector.
    assert [size for _, size in esp.calls] == [0x1000, 0x7000]


def test_app_commands_diff_by_default():
    import inspect
    import idftool.commands.firmware as firmware

    for name in ('factory', 'ota'):
        assert 'diff=True' in inspect.getsource(getattr(firmware, name)), name
