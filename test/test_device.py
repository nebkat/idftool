"""Real-device idftool tests — cycles every command against a connected ESP.

    pytest test/test_device.py --port /dev/cu.usbmodem101 --chip esp32s3

WARNING: this ERASES AND REWRITES THE DEVICE'S FLASH. Every test is marked ``device`` and skipped
unless ``--port`` is given, so a plain ``pytest`` never touches a board. The binary fixtures
(app images, partition table, nvs csv, full flash image) come from ``test/fixtures/<chip>/`` and are
built by ``test/project/build_fixtures.sh``; tests skip if they're missing.

The assertions are intentionally light — we mostly check that each command succeeds and that
readbacks / round-trips are consistent, not the exact human-readable output.
"""
import pytest

pytestmark = pytest.mark.device

# Partitions from test/project/partitions.csv, used across the tests.
DATA_PARTITION = "storage"      # a plain data partition for raw read/write/erase
NVS_PARTITION = "nvs"


@pytest.fixture(scope="module")
def device(idf, assets):
    """Provision the board once into a known state: flash the full image (bootloader + partition
    table + factory app). Returns the fixtures dir. Everything downstream builds on this."""
    idf(f"write-image {assets / 'flash-image.bin'}")
    return assets


# --- discovery / partition table ------------------------------------------------------------

def test_devices(idf):
    out = idf("devices")
    assert "||" in out  # "<port> || <description> || <hwid>"


def test_list_shows_our_partitions(idf, device):
    out = idf("list")
    for name in ("nvs", "otadata", "factory", "ota_0", "ota_1", "storage"):
        assert name in out


def test_dump_table_roundtrips(idf, device, tmp_path):
    dumped = tmp_path / "dumped.csv"
    idf(f"dump-table {dumped}")
    text = dumped.read_text()
    for name in ("nvs", "factory", "ota_0", "ota_1", "storage"):
        assert name in text


def test_write_table(idf, device, assets):
    # Re-flash just the partition table from the CSV fixture; then confirm it reads back.
    idf(f"write-table {assets / 'partitions.csv'}")
    assert "storage" in idf("list")


# --- raw partition I/O ----------------------------------------------------------------------

def test_partition_write_read_erase(idf, device, tmp_path):
    data = bytes((i * 7) & 0xFF for i in range(0x1000))
    src = tmp_path / "data.bin"
    src.write_bytes(data)

    idf(f"write {DATA_PARTITION} {src}")

    back = tmp_path / "back.bin"
    idf(f"read {DATA_PARTITION}[0:0x1000] {back}")
    assert back.read_bytes() == data

    idf(f"erase {DATA_PARTITION}[0:0x1000]")
    idf(f"read {DATA_PARTITION}[0:0x1000] {back}")
    assert set(back.read_bytes()) == {0xFF}


def test_view(idf, device):
    # Just needs to run in both output modes.
    idf(f"view {DATA_PARTITION}[0:0x100]")
    idf(f"view {DATA_PARTITION}[0:0x100] -s")


# --- NVS ------------------------------------------------------------------------------------

def test_write_nvs(idf, device, assets, tmp_path):
    idf(f"write-nvs {NVS_PARTITION} {assets / 'nvs.csv'}")
    back = tmp_path / "nvs.bin"
    idf(f"read {NVS_PARTITION}[0:0x1000] {back}")
    assert set(back.read_bytes()) != {0xFF}  # something was written


# --- filesystems --------------------------------------------------------------------------

@pytest.fixture
def fs_tree(tmp_path):
    """A small tree to flash. Names stay short enough for SPIFFS's 32-character limit."""
    root = tmp_path / "tree"
    (root / "sub").mkdir(parents=True)
    (root / "hello.txt").write_bytes(b"hello world\n")
    (root / "sub" / "nested.txt").write_bytes(b"nested\n")
    (root / "big.bin").write_bytes(bytes(range(256)) * 20)
    return root


@pytest.mark.parametrize("fs_type", ["spiffs", "littlefs", "fatfs"])
def test_write_and_read_fs(idf, device, tmp_path, fs_tree, fs_type):
    # `storage` is declared spiffs in the test table, so the other two need --type. All three
    # are worth flashing: the point is the image survives the round trip through flash.
    idf(f"write-fs {DATA_PARTITION} {fs_tree} --type {fs_type}")

    out = idf(f"print-fs {DATA_PARTITION} --type {fs_type}")
    assert "hello.txt" in out and "big.bin" in out

    extracted = tmp_path / f"out-{fs_type}"
    idf(f"read-fs {DATA_PARTITION} {extracted} --type {fs_type}")
    for name in ("hello.txt", "big.bin", "sub/nested.txt"):
        assert (extracted / name).read_bytes() == (fs_tree / name).read_bytes()


def test_write_fs_accepts_a_prebuilt_image(idf, device, tmp_path, fs_tree):
    image = tmp_path / "storage.bin"
    idf(f"create-fs {fs_tree} -o {image} --size 0x10000 --type spiffs")
    out = idf(f"write-fs {DATA_PARTITION} {image}")
    assert "Using spiffs image" in out

    extracted = tmp_path / "out"
    idf(f"read-fs {DATA_PARTITION} {extracted}")
    assert (extracted / "hello.txt").read_bytes() == (fs_tree / "hello.txt").read_bytes()


# --- firmware / boot selection --------------------------------------------------------------

def test_factory(idf, device, assets):
    idf(f"factory {assets / 'app-v1.bin'}")
    # factory erases otadata, so no OTA slot should be selected afterwards.
    assert "not set" in idf("get-boot").lower()


def test_ota_and_boot(idf, device, assets):
    idf("clear-boot")  # normalise: start from erased otadata

    idf(f"ota {assets / 'app-v1.bin'}")
    assert "ota_0" in idf("get-boot")

    idf(f"ota {assets / 'app-v2.bin'}")
    assert "ota_1" in idf("get-boot")

    idf("set-boot ota_0")
    assert "ota_0" in idf("get-boot")

    idf("clear-boot")
    assert "not set" in idf("get-boot").lower()


# --- bundles --------------------------------------------------------------------------------

@pytest.mark.slow
def test_bundle_roundtrip(idf, device, tmp_path):
    bundle = tmp_path / "bundle.zip"
    idf(f"dump-bundle {bundle}")            # reads every partition (slow)
    out = idf(f"print-bundle -f {bundle}")
    assert "factory" in out
    idf(f"write-bundle {bundle}")           # flash it all back


# --- full flash image -----------------------------------------------------------------------

@pytest.mark.slow
def test_dump_and_print_image(idf, device, tmp_path):
    # write-image is already exercised by the `device` provisioning fixture (and reflash is its
    # alias), so this just dumps the whole flash and inspects it — a full 16 MB reflash on top would
    # be redundant and very slow.
    image = tmp_path / "full.img"
    idf(f"dump-image {image}")              # reads the whole flash (slow)
    out = idf(f"print-image -f {image}")
    assert "factory" in out


# --- misc -----------------------------------------------------------------------------------

def test_enter_bootloader(idf, device):
    # Leaves the chip parked in ROM download mode; run last.
    out = idf("enter-bootloader")
    assert "download mode" in out.lower()
