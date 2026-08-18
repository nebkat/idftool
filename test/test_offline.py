"""Offline idftool tests — no device required. Safe to run with a plain ``pytest``."""
import pytest

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


class _FlakyEsp:
    """ESPLoader stand-in that serves the partition table and fails every other read."""
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
        raise FatalError("Failed to read flash block (result was 01050000: Comms error)")


def _flaky_state(monkeypatch):
    from esp_idf_defs.partitions import PartitionTable
    import idftool.state as state_module

    table = PartitionTable.from_csv((SAMPLES / "partitions.csv").read_text())
    state = state_module.State(port=None, baud=115200, no_reset=False, partition_table_file=None,
                               partition_table_offset=0x8000, partition_table_size=0xc00,
                               primary_bootloader_offset=None, recovery_bootloader_offset=None)
    state.esp = _FlakyEsp(0x8000, table.to_binary())
    monkeypatch.setattr(state_module, "detect_flash_size", lambda esp: "16MB")
    return state


def test_setup_survives_failing_otadata_read(monkeypatch, capsys):
    loaded = _flaky_state(monkeypatch).setup()
    out = capsys.readouterr()

    assert loaded.partition_table.find_by_name("factory") is not None
    assert "failed to read otadata" in out.out + out.err
    assert "READ ERROR" in out.out


def test_library_api_importable():
    # idftool is a library too: the operation functions and core types import with no device.
    from idftool import State, Loaded, write_image, factory, ota
    assert isinstance(State, type) and isinstance(Loaded, type)
    assert all(callable(fn) for fn in (write_image, factory, ota))


# --- filesystems ----------------------------------------------------------------------------

FS_TYPES = ("fatfs", "littlefs", "spiffs")


@pytest.fixture
def fs_tree(tmp_path):
    """A small directory tree to pack. Names stay short enough for SPIFFS's 32-char limit."""
    root = tmp_path / "tree"
    (root / "sub").mkdir(parents=True)
    (root / "hello.txt").write_bytes(b"hello world\n")
    (root / "sub" / "nested.txt").write_bytes(b"nested\n")
    # Spans several pages/clusters, so multi-page reassembly is exercised too.
    (root / "big.bin").write_bytes(bytes(range(256)) * 100)
    return root


@pytest.mark.parametrize("fs_type", FS_TYPES)
def test_create_extract_fs_roundtrip(run_offline, tmp_path, fs_tree, fs_type):
    image = tmp_path / f"{fs_type}.bin"
    out = run_offline(f"create-fs {fs_tree} -o {image} --size 0x100000 -t {fs_type}")
    assert "hello.txt" in out and "big.bin" in out
    assert image.exists() and len(image.read_bytes()) == 0x100000

    extracted = tmp_path / "out"
    run_offline(f"extract-fs {image} {extracted}")
    for name in ("hello.txt", "big.bin", "sub/nested.txt"):
        assert (extracted / name).read_bytes() == (fs_tree / name).read_bytes()


@pytest.mark.parametrize("fs_type", FS_TYPES)
def test_print_fs_detects_type(run_offline, tmp_path, fs_tree, fs_type):
    # With no --type, the filesystem has to be recognised from the image itself.
    image = tmp_path / f"{fs_type}.bin"
    run_offline(f"create-fs {fs_tree} -o {image} --size 0x100000 -t {fs_type}")
    out = run_offline(f"print-fs {image}")
    assert fs_type in out and "hello.txt" in out


def test_create_fs_takes_size_and_type_from_partition(run_offline, tmp_path, fs_tree):
    # 'storage' in the sample table is a spiffs partition of 64K, so neither --size nor --type
    # should be needed.
    image = tmp_path / "storage.bin"
    out = run_offline(f"--partition-table-file {SAMPLES / 'partitions.csv'} create-fs {fs_tree} "
                      f"-o {image} --partition storage")
    assert "spiffs" in out
    assert len(image.read_bytes()) == 0x10000


def test_create_fs_requires_size_or_partition(run_offline, tmp_path, fs_tree):
    out = run_offline(f"create-fs {fs_tree} -o {tmp_path / 'x.bin'} -t littlefs", expect_error=True)
    assert "size" in out.lower() and "partition" in out.lower()


def test_create_fs_without_type_is_clear(run_offline, tmp_path, fs_tree):
    out = run_offline(f"create-fs {fs_tree} -o {tmp_path / 'x.bin'} --size 0x100000",
                      expect_error=True)
    assert "--type" in out


@pytest.mark.parametrize("fs_type", FS_TYPES)
def test_create_fs_too_small_is_clear(run_offline, tmp_path, fs_tree, fs_type):
    # The filesystem itself fits at this size but the ~25K of content doesn't, which has to be
    # reported — FAT in particular used to allocate clusters past the end of the image instead.
    size = "0x10000" if fs_type == "fatfs" else "0x6000"
    out = run_offline(f"create-fs {fs_tree} -o {tmp_path / 'x.bin'} --size {size} -t {fs_type}",
                      expect_error=True)
    assert "do not fit" in out


def test_create_fs_unusably_small_is_clear(run_offline, tmp_path, fs_tree):
    out = run_offline(f"create-fs {fs_tree} -o {tmp_path / 'x.bin'} --size 0x8000 -t fatfs",
                      expect_error=True)
    assert "too small" in out


def test_fat_image_is_wear_levelled_by_default(run_offline, tmp_path, fs_tree):
    # ESP-IDF mounts a `fat` partition in SPI flash through the wear levelling layer, so a
    # created image starts with the dummy sector rather than the boot sector.
    from idftool.fs import wl

    image = tmp_path / "wl.bin"
    run_offline(f"create-fs {fs_tree} -o {image} --size 0x100000 -t fatfs")
    data = image.read_bytes()
    assert wl.looks_like_wl(data)
    assert data[:wl.SECTOR_SIZE] == b"\xff" * wl.SECTOR_SIZE
    assert wl.unwrap(data)[510:512] == b"\x55\xaa"

    plain = tmp_path / "plain.bin"
    run_offline(f"create-fs {fs_tree} -o {plain} --size 0x100000 -t fatfs --no-fat-wear-levelling")
    assert not wl.looks_like_wl(plain.read_bytes())
    assert plain.read_bytes()[510:512] == b"\x55\xaa"


def test_wear_levelling_overhead_scales_with_partition_size():
    # The state sector holds one record per sector of the partition, so the overhead grows
    # past the four sectors a small partition needs.
    from idftool.fs import wl

    assert wl.overhead_sectors(0x10000) == 4
    assert wl.overhead_sectors(0x100000) == 6
    assert wl.filesystem_size(0x100000) == 0x100000 - 6 * wl.SECTOR_SIZE


def test_fat_width_follows_the_cluster_count():
    # FatFs derives FAT12 vs FAT16 from the cluster count alone, so the FAT idftool writes has
    # to match what a reader will derive. Picking the width whose range the natural count falls
    # in also matters for capacity: forcing FAT12 onto a large volume means padding the FAT out
    # until the count drops under 4085, which threw away most of a 64M partition.
    from idftool.fs import fatfs

    for size, expected in ((0x100000, 12), (0x400000, 12), (0x1000000, 16), (0x4000000, 16)):
        geometry = fatfs._geometry(size, 0x1000, 1, 2, 512, None)
        assert geometry['bits'] == expected, (size, geometry)
        low, high = (1, 4084) if expected == 12 else (4085, 65524)
        assert low <= geometry['clusters'] <= high
        if size >= 0x100000:
            assert geometry['clusters'] * 0x1000 > size * 0.9, "most of the volume should be usable"


def test_fat_too_large_for_fat16_is_explained():
    from idftool.fs import fatfs, FsError

    with pytest.raises(FsError, match="--fat-sectors-per-cluster"):
        fatfs._geometry(0x20000000, 0x1000, 1, 2, 512, None)
    # A larger cluster brings it back in range.
    assert fatfs._geometry(0x20000000, 0x1000, 4, 2, 512, None)['bits'] == 16


def test_spiffs_name_limit_is_explained(run_offline, tmp_path):
    root = tmp_path / "tree"
    (root / "a-fairly-deep-directory").mkdir(parents=True)
    (root / "a-fairly-deep-directory" / "with-a-long-name.txt").write_bytes(b"x")
    out = run_offline(f"create-fs {root} -o {tmp_path / 'x.bin'} --size 0x10000 -t spiffs",
                      expect_error=True)
    assert "--spiffs-obj-name-len" in out
