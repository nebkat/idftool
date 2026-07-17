"""Offline idftool tests — no device required. Safe to run with a plain ``pytest``."""
from conftest import SAMPLES


def test_help_lists_commands(run_offline):
    out = run_offline("--help")
    for cmd in ("convert-table", "print-table", "write-table", "create-nvs"):
        assert cmd in out


def test_unknown_command_errors(run_offline):
    out = run_offline("definitely-not-a-command", expect_error=True)
    assert "No such command" in out


def test_print_table_from_csv(run_offline):
    out = run_offline(f"print-table {SAMPLES / 'partitions.csv'}")
    for name in ("nvs", "factory", "ota_0", "storage"):
        assert name in out


def test_convert_table_roundtrip(run_offline, tmp_path):
    binary = tmp_path / "pt.bin"
    csv = tmp_path / "pt.csv"
    run_offline(f"convert-table {SAMPLES / 'partitions.csv'} {binary}")
    assert binary.exists() and binary.read_bytes()[:2] == b"\xaa\x50"
    run_offline(f"convert-table {binary} {csv}")
    assert "factory" in csv.read_text()


def test_convert_table_infers_format_error(run_offline, tmp_path):
    out = run_offline(f"convert-table {SAMPLES / 'partitions.csv'} {tmp_path / 'out.dat'}",
                      expect_error=True)
    assert "format" in out.lower()


def test_create_nvs_offline(run_offline, tmp_path):
    out = tmp_path / "nvs.bin"
    run_offline(f"create-nvs {SAMPLES / 'nvs.csv'} -o {out} --size 0x6000")
    assert out.exists() and len(out.read_bytes()) == 0x6000


def test_create_nvs_requires_size_or_partition(run_offline, tmp_path):
    out = run_offline(f"create-nvs {SAMPLES / 'nvs.csv'} -o {tmp_path / 'nvs.bin'}",
                      expect_error=True)
    assert "size" in out.lower() and "partition" in out.lower()


def test_missing_bootloader_offset_is_clear(run_offline):
    # A CSV with a bootloader row and no offset, and no --primary-bootloader-offset, should give a
    # clear, actionable error (not the library's cryptic one).
    out = run_offline(f"print-table {SAMPLES / 'bootloader-template.csv'}", expect_error=True)
    assert "bootloader" in out.lower() and "--primary-bootloader-offset" in out


def test_bootloader_offset_from_chip_name(run_offline):
    # Supplying the chip name resolves the primary bootloader offset (esp32s3 -> 0x0).
    out = run_offline(
        f"--primary-bootloader-offset esp32s3 print-table {SAMPLES / 'bootloader-template.csv'}")
    assert "bootloader" in out
