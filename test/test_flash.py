"""The write_flash option pass-through — library-level tests, no device and no subprocess."""
import inspect
import re

import pytest
import rich_click as click

from conftest import SAMPLES
from idftool.flash import (FLASH_OPTION_FLAGS, WRITE_FLASH_OPTIONS, split_options,
                           write_flash_options)


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
    # so its own defaults apply.
    given = dict(skip_flashed=False, compress=None, encrypt=False, force=False,
                 ignore_flash_enc_efuse=False, no_progress=False)
    assert write_flash_options(given) == {}


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
    for flag in FLASH_OPTION_FLAGS:
        assert flag.lstrip('-').replace('-', '_') in WRITE_FLASH_OPTIONS


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
