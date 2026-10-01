"""Real-device idftool tests — cycles every command against a connected ESP.

    pytest test/test_device.py --port /dev/cu.usbmodem101 --chip esp32s3

WARNING: this ERASES AND REWRITES THE DEVICE'S FLASH. Every test is marked ``device`` and skipped
unless ``--port`` is given, so a plain ``pytest`` never touches a board. The binary fixtures
(app images, partition table, nvs csv, full flash image) come from ``test/fixtures/<chip>/`` and are
built by ``test/project/build_fixtures.sh``; tests skip if they're missing.

The assertions are intentionally light — we mostly check that each command succeeds and that
readbacks / round-trips are consistent, not the exact human-readable output.
"""
import json
import zipfile

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
    assert "USB ID" in out


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


def test_nvs_read_commands(idf, device, assets, tmp_path):
    idf(f"write-nvs {NVS_PARTITION} {assets / 'nvs.csv'}")

    out = idf(f"print-nvs {NVS_PARTITION}")
    for text in ("storage", "device_id", "12345", "device_name", "idftool-test"):
        assert text in out

    # get-nvs is meant to be captured in a shell, so its values must be the only thing on
    # stdout — on a device everything else (esptool's chatter, the partition table, the reset)
    # would otherwise land in the middle of them.
    value = idf(f"get-nvs {NVS_PARTITION} storage:device_id", stdout_only=True)
    assert value.strip() == "12345"

    csv = tmp_path / "readback.csv"
    idf(f"read-nvs {NVS_PARTITION} {csv}")
    text = csv.read_text()
    assert "device_id,data,u32,12345" in text
    assert "device_name,data,string,idftool-test" in text


def test_set_nvs_writes_only_the_changed_pages(idf, device, assets, tmp_path):
    """The load-bearing one: set-nvs appends in place and re-flashes just the dirty pages.

    Everything else in the partition has to survive that byte for byte, and the result has to
    still parse as a valid NVS image once it is read back off the chip.
    """
    idf(f"write-nvs {NVS_PARTITION} {assets / 'nvs.csv'}")
    before = tmp_path / "before.bin"
    idf(f"read {NVS_PARTITION} {before}")

    out = idf(f"set-nvs {NVS_PARTITION} storage:device_name=renamed-on-device")
    assert "1 of 6 page changed" in out          # appended, not compacted

    # Read the partition back and check the device holds what we think it does.
    after = tmp_path / "after.bin"
    idf(f"read {NVS_PARTITION} {after}")
    a, b = before.read_bytes(), after.read_bytes()
    assert len(a) == len(b)
    assert a[0x1000:] == b[0x1000:]              # only page 0 was touched

    listing = idf(f"print-nvs {NVS_PARTITION}")
    assert "renamed-on-device" in listing
    assert "idftool-test" not in listing         # the replaced entry is erased, not duplicated
    for text in ("device_id", "12345", "counter"):
        assert text in listing                   # everything else survived
    assert "Warning:" not in listing             # and it still parses cleanly


def test_set_nvs_adds_and_deletes_keys(idf, device, assets):
    idf(f"write-nvs {NVS_PARTITION} {assets / 'nvs.csv'}")
    idf(f"set-nvs {NVS_PARTITION} storage:serial:string=SN-DEVICE-1 -d storage:counter")
    out = idf(f"print-nvs {NVS_PARTITION}")
    assert "SN-DEVICE-1" in out
    assert "counter" not in out
    assert "Warning:" not in out


def test_set_nvs_rewrite_compacts_on_the_device(idf, device, assets):
    idf(f"write-nvs {NVS_PARTITION} {assets / 'nvs.csv'}")
    for i in range(5):
        idf(f"set-nvs {NVS_PARTITION} storage:counter={i}")
    assert "erased" in idf(f"print-nvs {NVS_PARTITION} --pages")

    out = idf(f"set-nvs {NVS_PARTITION} storage:counter=99 --rewrite")
    assert "compacted" in out
    pages = idf(f"print-nvs {NVS_PARTITION} --pages")
    assert "0 erased" in pages
    assert idf(f"get-nvs {NVS_PARTITION} storage:counter", stdout_only=True).strip() == "99"


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


def test_skip_flashed_skips_a_write_that_would_change_nothing(idf, device, assets):
    # The same app twice: the second write compares on-device MD5s and writes nothing at
    # all. App writes do this by default, so the second call needs no flag.
    idf(f"factory {assets / 'app-v1.bin'} --no-skip-flashed")
    out = idf(f"factory {assets / 'app-v1.bin'}")
    assert "already in flash" in out and "skipping write" in out

    # --no-skip-flashed --no-diff writes it anyway.
    out = idf(f"factory {assets / 'app-v1.bin'} --no-skip-flashed --no-diff")
    assert "skipping write" not in out

    # A different app does not match, so it is written.
    out = idf(f"factory {assets / 'app-v2.bin'}")
    assert "skipping write" not in out

    # Every other write command skips by default too.
    idf(f"write factory {assets / 'app-v1.bin'}")
    out = idf(f"write factory {assets / 'app-v1.bin'}")
    assert "skipping write" in out
    out = idf(f"write factory {assets / 'app-v1.bin'} --no-skip-flashed --no-diff")
    assert "skipping write" not in out


def test_diff_writes_only_the_sectors_that_changed(idf, device, assets):
    # App writes compare sector by sector by default and write what differs — or give up and
    # write the lot when too much of it has changed. Either is correct; which one happens
    # depends on how much these two builds have in common.
    idf(f"factory {assets / 'app-v1.bin'} --no-diff --no-skip-flashed")
    out = idf(f"factory {assets / 'app-v2.bin'}")
    assert " in 1 region" in out or " regions in " in out or "too much of it differs" in out

    # Whichever path it took, the partition has to hold app-v2 exactly afterwards — which
    # the whole-region comparison is the independent check of.
    out = idf(f"factory {assets / 'app-v2.bin'} --no-diff")
    assert "already in flash" in out


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
    with zipfile.ZipFile(bundle) as zf:
        assert zf.read("bootloader.bin") == idf_read(idf, tmp_path, "bootloader")
    assert "Bootloader: ESP32S3 (offset=0x0)" in out
    out = idf(f"write-bundle {bundle}")     # flash it all back: nothing has changed
    assert "already matches" in out


def idf_read(idf, tmp_path, partition):
    out = tmp_path / f"{partition}.read"
    idf(f"read {partition} {out}")
    return out.read_bytes()


def make_bundle(path, entries):
    """name → bytes, or a dict for manifest.json."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, json.dumps(data) if isinstance(data, dict) else data)
    return path


def test_bootloader_checks(idf, device, assets, tmp_path):
    # The same bootloader is accepted (and skipped, it's already there).
    assert "already in flash" in idf(f"write bootloader {assets / 'bootloader.bin'}")
    # An app is not a bootloader, whichever chip it was built for.
    small_app = make_bundle(tmp_path / "b.zip", {"bootloader.bin": (assets / "app-v1.bin").read_bytes()[:0x8000]})
    assert "Invalid bootloader binary" in idf(f"write-bundle {small_app}", expect_error=True)
    # A bootloader is not an app, unless forced.
    assert "is a bootloader, not an app" in idf(f"write ota_0 {assets / 'bootloader.bin'}",
                                                 expect_error=True)
    assert "writing it anyway" in idf(f"write ota_0 {assets / 'bootloader.bin'} --force")
    assert "IDF v" in idf(f"app-info -f {assets / 'bootloader.bin'}")


def test_role_bundles(idf, device, assets, tmp_path):
    idf("clear-boot")
    ota = make_bundle(tmp_path / "ota.zip", {"@ota.bin": (assets / "app-v2.bin").read_bytes()})
    assert "Write @ota.bin to the next OTA slot" in idf(f"print-bundle -f {ota}")
    idf(f"write-bundle {ota}")
    assert "ota_0" in idf("get-boot")
    assert "idftool_test 2.0.0" in idf("list")

    factory = make_bundle(tmp_path / "factory.zip", {"@factory.bin": (assets / "app-v1.bin").read_bytes()})
    idf(f"write-bundle {factory}")
    assert "not set" in idf("get-boot").lower()

    clash = make_bundle(tmp_path / "clash.zip", {"@ota.bin": b"x", "ota_1.bin": b"y"})
    assert "may pick it" in idf(f"write-bundle {clash}", expect_error=True)


def test_manifest_bundle(idf, device, assets, tmp_path, fs_tree):
    idf(f"write-nvs {NVS_PARTITION} {assets / 'nvs.csv'}")
    idf(f"write-fs {DATA_PARTITION} {fs_tree} --type spiffs")
    idf("clear-boot")
    bundle = make_bundle(tmp_path / "b.zip", {
        "partition_table.csv": (assets / "partitions.csv").read_text(),
        "ota_0.bin": (assets / "app-v2.bin").read_bytes(),
        "files/config.json": b'{"channel": "stable"}',
        "manifest.json": {
            "name": "Hardware test", "chip": "esp32s3", "table": "require",
            "ops": [
                {"op": "set-nvs", "set": {"storage:device_id": "99", "storage:added": "u8:7"}},
                {"op": "edit-fs", "partition": DATA_PARTITION,
                 "put": {"/config.json": "files/config.json"}, "delete": ["hello.txt"]},
                {"op": "set-boot", "partition": "ota_0"},
            ]}})
    out = idf(f"write-bundle {bundle}")
    assert "Bundle: Hardware test" in out and "already matches" in out
    assert "ota_0" in idf("get-boot")
    assert idf(f"get-nvs {NVS_PARTITION} storage:device_id storage:added",
               stdout_only=True).split() == ["99", "7"]
    listing = idf(f"print-fs {DATA_PARTITION}")
    assert "config.json" in listing and "hello.txt" not in listing


def test_bundle_table_rules(idf, device, assets, tmp_path):
    moved = (assets / "partitions.csv").read_text().replace(
        "storage,    data, spiffs,   0xb0000", "storage,    data, spiffs,   0xc0000")
    require = make_bundle(tmp_path / "require.zip", {"partition_table.csv": moved, "manifest.json": {
        "table": "require", "ops": [{"op": "clear-boot"}]}})
    assert "requires" in idf(f"write-bundle {require}", expect_error=True)
    assert "0xb0000" in idf("list")

    ask = make_bundle(tmp_path / "ask.zip", {"partition_table.csv": moved, "manifest.json": {
        "table": "ask", "ops": [{"op": "clear-boot"}]}})
    assert "pass -y" in idf(f"write-bundle {ask}", expect_error=True)
    out = idf(f"-y write-bundle {ask}")
    assert "storage: moved 0xb0000 → 0xc0000" in out
    assert "0xc0000" in idf("list")

    idf(f"write-table {assets / 'partitions.csv'}")  # put it back
    assert "0xb0000" in idf("list")


def test_encrypted_nvs(idf, device, assets, tmp_path):
    key = bytes(range(32)).hex()
    idf(f"write-nvs {NVS_PARTITION} {assets / 'nvs.csv'} --hmac-key {key}")
    assert "pass --hmac-key" in idf(f"print-nvs {NVS_PARTITION}", expect_error=True)
    assert "does not decrypt" in idf(f"print-nvs {NVS_PARTITION} --hmac-key {'00' * 32}",
                                     expect_error=True)
    idf(f"set-nvs {NVS_PARTITION} storage:device_id=4242 --hmac-key {key}")
    assert idf(f"get-nvs {NVS_PARTITION} storage:device_id --hmac-key {key}",
               stdout_only=True).strip() == "4242"
    idf(f"write-nvs {NVS_PARTITION} {assets / 'nvs.csv'}")  # back to plaintext


# --- full flash image -----------------------------------------------------------------------

def test_dump_and_print_image(idf, device, tmp_path):
    # write-image is already exercised by the `device` provisioning fixture (and reflash is its
    # alias), so this just dumps flash and inspects it.
    #
    # Bounded to the region the fixture table covers (see partitions.csv — the last partition
    # ends at 0xc0000). Dumping the whole chip is pointless and actively unreliable: a board can
    # have 32 MB of which all but the first 768 KB is erased, and pulling that back as one serial
    # read is slow and long enough to time out.
    image = tmp_path / "flash.img"
    idf(f"dump-image {image} --size 0xc0000")
    assert image.stat().st_size == 0xc0000
    out = idf(f"print-image -f {image}")
    assert "factory" in out
    assert "Bootloader: ESP32S3 (offset=0x0)" in out


def test_write_image_without_erase_skips_what_matches(idf, device, assets):
    idf(f"write-image --no-erase {assets / 'flash-image.bin'}")
    # The app may have touched its own data since; either way the bootloader diff must work.
    out = idf(f"write-image --no-erase --diff {assets / 'flash-image.bin'}")
    assert "already in flash" in out or " region" in out


# --- misc -----------------------------------------------------------------------------------

def test_enter_bootloader(idf, device):
    # Leaves the chip parked in ROM download mode; run last.
    out = idf("enter-bootloader")
    assert "download mode" in out.lower()
