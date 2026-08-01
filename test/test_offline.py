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


def test_create_nvs_accepts_prebuilt_binary(run_offline, tmp_path):
    # A pre-built NVS binary should be passed through (and padded to size), not
    # re-parsed as CSV — which used to fail with a UnicodeDecodeError.
    built = tmp_path / "built.bin"
    run_offline(f"create-nvs {SAMPLES / 'nvs.csv'} -o {built} --size 0x4000")
    passthrough = tmp_path / "passthrough.bin"
    out = run_offline(f"create-nvs {built} -o {passthrough} --size 0x6000")
    assert "NVS binary" in out
    assert passthrough.exists() and len(passthrough.read_bytes()) == 0x6000
    # First 0x4000 bytes are the original image; the tail is erased flash padding.
    assert passthrough.read_bytes()[:0x4000] == built.read_bytes()
    assert passthrough.read_bytes()[0x4000:] == b"\xff" * 0x2000


def test_create_nvs_rejects_oversized_binary(run_offline, tmp_path):
    built = tmp_path / "built.bin"
    run_offline(f"create-nvs {SAMPLES / 'nvs.csv'} -o {built} --size 0x6000")
    out = run_offline(f"create-nvs {built} -o {tmp_path / 'out.bin'} --size 0x4000",
                      expect_error=True)
    assert "exceeds partition size" in out


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


# print-image / print-bundle are file operations (no device), so they're covered here against the
# built fixtures rather than requiring a slow full-flash device dump.

def test_print_image(run_offline, assets):
    out = run_offline(f"print-image {assets / 'flash-image.bin'}")
    assert "factory" in out
    assert "idftool_test" in out  # the app descriptor was parsed


def test_partition_by_offset_no_match_is_clear(run_offline, tmp_path):
    # A numeric partition address matching nothing should give a clear error, not crash with a
    # StopIteration. Reachable offline via `create-nvs --partition <numeric>`.
    out = run_offline(
        f"--partition-table-file {SAMPLES / 'partitions.csv'} create-nvs {SAMPLES / 'nvs.csv'} "
        f"-o {tmp_path / 'out.bin'} --partition 0x999999", expect_error=True)
    assert "No partition at offset" in out
    assert "StopIteration" not in out


def test_create_and_print_bundle(run_offline, assets, tmp_path):
    bundle = tmp_path / "bundle.zip"
    run_offline(f"--partition-table-file {assets / 'partitions.csv'} create-bundle "
                f"-o {bundle} --flash-partition-table factory {assets / 'app-v1.bin'}")
    out = run_offline(f"print-bundle {bundle}")
    assert "factory" in out
    assert "idftool_test" in out


def test_library_api_importable():
    # idftool is a library too: the operation functions and core types import with no device.
    from idftool import State, Loaded, write_image, factory, ota
    assert isinstance(State, type) and isinstance(Loaded, type)
    assert all(callable(fn) for fn in (write_image, factory, ota))
