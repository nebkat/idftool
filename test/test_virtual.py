"""Every device command against a virtual ESP32-S3: an in-memory flash, no hardware.

The flash starts as the committed fixture image (bootloader, partition table, and the v1 app
in `factory`), so commands read and write real partition data and the tests check the bytes.
"""
import hashlib
import zipfile

import pytest
from click.testing import CliRunner
from esptool.targets import ESP32S3ROM

from conftest import FIXTURES

CHIP = FIXTURES / "esp32s3"
FLASH_SIZE = 0x400000
MAC = bytes.fromhex("7c2c679279c0")

# From the fixture partition table.
NVS = (0x9000, 0x6000)
OTADATA = (0xf000, 0x2000)
FACTORY = (0x20000, 0x30000)
OTA_0 = (0x50000, 0x30000)
OTA_1 = (0x80000, 0x30000)
STORAGE = (0xb0000, 0x10000)


class VirtualEsp:
    """The part of esptool's ESPLoader that idftool uses, over a bytearray."""

    CHIP_NAME = "ESP32-S3"
    IMAGE_CHIP_ID = 9
    BOOTLOADER_FLASH_OFFSET = 0x0
    ESP_IMAGE_MAGIC = ESP32S3ROM.ESP_IMAGE_MAGIC
    BOOTLOADER_IMAGE = ESP32S3ROM.BOOTLOADER_IMAGE
    FLASH_SIZES = ESP32S3ROM.FLASH_SIZES
    parse_flash_size_arg = ESP32S3ROM.parse_flash_size_arg
    FLASH_SECTOR_SIZE = 0x1000
    secure_download_mode = False

    def __init__(self, image: bytes):
        self.flash = bytearray(image.ljust(FLASH_SIZE, b"\xff"))
        self.resets = 0
        self.writes = []
        self._begin = None

    def region(self, offset, size) -> bytes:
        return bytes(self.flash[offset:offset + size])

    def run_stub(self):
        return self

    def read_flash(self, offset, length, progress_fn=None):
        return self.region(offset, length)

    def erase_region(self, offset, size):
        self.flash[offset:offset + size] = b"\xff" * size

    def flash_md5sum(self, address, size):
        return hashlib.md5(self.region(address, size)).hexdigest()

    def read_mac(self, mac_type="BASE_MAC"):
        return MAC

    def get_secure_boot_enabled(self):
        return False

    def hard_reset(self):
        self.resets += 1

    def flash_begin(self, size, offset, **_):
        self._begin = offset

    def flash_block(self, data, seq, **_):
        start = self._begin + seq * len(data)
        self.flash[start:start + len(data)] = data

    def flash_finish(self, *_, **__):
        pass

    def write(self, address, data):
        self.writes.append((address, len(data)))
        self.flash[address:address + len(data)] = data


@pytest.fixture
def esp(monkeypatch):
    import idftool.commands.bundles as bundles
    import idftool.commands.images as images
    import idftool.commands.partition_io as partition_io
    import idftool.commands.table as table
    import idftool.flash as flash
    import idftool.state as state

    device = VirtualEsp((CHIP / "flash-image.bin").read_bytes())

    def write_flash(esp, addr_data, **kwargs):
        if kwargs.get("erase_all"):
            device.erase_region(0, FLASH_SIZE)
        for address, data in addr_data:
            if isinstance(data, str):
                with open(data, "rb") as f:
                    data = f.read()
            device.write(address, data)

    def read_flash(esp, address, size, output):
        with open(output, "wb") as f:
            f.write(device.region(address, size))

    monkeypatch.setattr(state, "detect_chip", lambda port, **_: device)
    for module in (state, table, images, flash, bundles):
        monkeypatch.setattr(module, "detect_flash_size", lambda esp: "4MB")
    monkeypatch.setattr(flash, "esptool_write_flash", write_flash)
    monkeypatch.setattr(images, "read_flash", read_flash)
    monkeypatch.setattr(partition_io, "read_flash", read_flash)
    monkeypatch.setattr(partition_io, "erase_region",
                        lambda esp, address, size: device.erase_region(address, size))
    return device


@pytest.fixture
def run(esp, tmp_path, monkeypatch):
    """Run an idftool command against the virtual device, in `tmp_path`."""
    from idftool.cli import cli
    import idftool.commands  # noqa: F401  (registers the commands)

    monkeypatch.chdir(tmp_path)

    def invoke(*args, ok=True):
        result = CliRunner().invoke(cli, ["-p", "/dev/virtual", *map(str, args)])
        if ok:
            assert result.exit_code == 0, result.output + repr(result.exception)
        else:
            assert result.exit_code != 0, result.output
            return result.output + str(result.exception)
        return result.output

    return invoke


def app(version):
    return (CHIP / f"app-v{version}.bin").read_bytes()


# --- partition table ------------------------------------------------------------------------

def test_list_shows_the_table_and_the_factory_app(run):
    out = run("list")
    assert "factory" in out and "idftool_test 1.0.0" in out
    assert "| ota_0" in out  # plain table, since the output isn't a terminal


def test_dump_table_round_trips(run, tmp_path, esp):
    run("dump-table", "table.bin")
    assert (tmp_path / "table.bin").read_bytes()[:0xc00] == esp.region(0x8000, 0xc00)
    run("dump-table", "table.csv")
    assert "factory" in (tmp_path / "table.csv").read_text()


def test_write_table_writes_the_partition_table(run, esp):
    esp.erase_region(0x8000, 0x1000)
    run("write-table", CHIP / "partition-table.bin")
    assert esp.region(0x8000, 0xc00) == (CHIP / "partition-table.bin").read_bytes()[:0xc00]


# --- partition I/O --------------------------------------------------------------------------

def test_read_copies_a_partition(run, tmp_path, esp):
    run("read", "nvs", "nvs.bin")
    assert (tmp_path / "nvs.bin").read_bytes() == esp.region(*NVS)


def test_read_takes_a_slice(run, tmp_path, esp):
    run("read", "factory[0x100:+0x80]", "slice.bin")
    assert (tmp_path / "slice.bin").read_bytes() == esp.region(FACTORY[0] + 0x100, 0x80)


def test_write_puts_a_file_in_a_partition(run, esp):
    run("write", "ota_0", CHIP / "app-v2.bin")
    assert esp.region(OTA_0[0], len(app(2))) == app(2)


def test_write_refuses_a_file_too_big_for_the_partition(run, tmp_path, esp):
    (tmp_path / "big.bin").write_bytes(b"\0" * (STORAGE[1] + 1))
    before = esp.region(*STORAGE)
    run("write", "storage", "big.bin", ok=False)
    assert esp.region(*STORAGE) == before


def test_erase_blanks_a_partition(run, esp):
    run("erase", "nvs")
    assert esp.region(*NVS) == b"\xff" * NVS[1]


def test_view_dumps_hex_and_text(run):
    assert "e9" in run("view", "factory[0:0x10]").lower()
    assert "idftool_test" in run("view", "factory[0x20:0x80]", "-s")


# --- firmware -------------------------------------------------------------------------------

def test_ota_writes_the_next_slot_and_boots_it(run, esp):
    out = run("ota", CHIP / "app-v2.bin")
    assert "to partition 'ota_0'" in out
    assert esp.region(OTA_0[0], len(app(2))) == app(2)
    assert "OTA slot 'ota_0'" in run("get-boot")


def test_a_second_ota_goes_to_the_other_slot(run, esp):
    run("ota", CHIP / "app-v1.bin")
    out = run("ota", CHIP / "app-v2.bin")
    assert "to partition 'ota_1'" in out
    assert "OTA slot 'ota_1'" in run("get-boot")


def test_factory_writes_the_factory_partition_and_clears_otadata(run, esp):
    run("ota", CHIP / "app-v1.bin")
    run("factory", CHIP / "app-v2.bin")
    assert esp.region(FACTORY[0], len(app(2))) == app(2)
    assert esp.region(*OTADATA) == b"\xff" * OTADATA[1]


def test_reflashing_the_same_app_writes_nothing(run, esp):
    out = run("factory", CHIP / "app-v1.bin")  # already in the fixture's factory slot
    assert "already in flash" in out
    assert not [w for w in esp.writes if w[0] == FACTORY[0]]


def test_an_app_for_another_chip_is_refused(run, esp):
    esp.IMAGE_CHIP_ID = 0  # pretend the device is an ESP32
    run("ota", CHIP / "app-v2.bin", ok=False)
    assert esp.region(*OTA_0) == b"\xff" * OTA_0[1]


def test_rewriting_a_partition_writes_nothing(run, tmp_path, esp):
    (tmp_path / "same.bin").write_bytes(esp.region(*STORAGE))
    run("write", "storage", "same.bin")
    assert not esp.writes


def test_a_bootloader_for_another_chip_is_refused(run, esp):
    esp.IMAGE_CHIP_ID = 0  # pretend the device is an ESP32
    before = esp.region(0, 0x8000)
    run("write", "bootloader", CHIP / "bootloader.bin", ok=False)
    assert esp.region(0, 0x8000) == before


def test_set_boot_and_clear_boot(run, esp):
    run("ota", CHIP / "app-v1.bin")
    run("ota", CHIP / "app-v2.bin")
    run("set-boot", "ota_0")
    assert "OTA slot 'ota_0'" in run("get-boot")
    run("clear-boot")
    assert esp.region(*OTADATA) == b"\xff" * OTADATA[1]


# --- images and bundles ---------------------------------------------------------------------

def test_dump_image_then_write_image_restores_the_flash(run, tmp_path, esp):
    run("dump-image", "backup.img", "--size", 0x100000)
    original = esp.region(0, 0x100000)
    esp.erase_region(0, FLASH_SIZE)
    run("write-image", "backup.img")
    assert esp.region(0, 0x100000) == original


def test_rewriting_the_same_image_with_diff_writes_nothing(run, esp):
    run("write-image", "--no-erase", "--diff", CHIP / "flash-image.bin")
    assert not esp.writes


def test_dump_image_is_named_after_the_chip_and_mac(run, tmp_path):
    run("dump-image", "--size", 0x10000)
    assert [p.name for p in tmp_path.glob("esp32-s3-7c2c679279c0-*.img")]


def test_dump_bundle_then_write_bundle_restores_the_partitions(run, tmp_path, esp):
    run("dump-bundle", "backup.zip")
    names = zipfile.ZipFile(tmp_path / "backup.zip").namelist()
    assert "partition_table.csv" in names and "factory.bin" in names
    original = esp.region(*FACTORY)
    esp.erase_region(*FACTORY)
    run("write-bundle", "backup.zip")
    assert esp.region(*FACTORY) == original


# --- NVS and filesystems --------------------------------------------------------------------

def test_dump_bundle_includes_the_bootloader(run, esp):
    run("dump-bundle", "backup.zip")
    with zipfile.ZipFile("backup.zip") as zf:
        assert zf.read("bootloader.bin") == esp.region(0, 0x8000)
    assert "Bootloader: ESP32S3 (offset=0x0)" in run("print-bundle", "-f", "backup.zip")


def test_write_bundle_checks_the_bootloader_before_writing(run, esp):
    run("dump-bundle", "backup.zip")
    esp.IMAGE_CHIP_ID = 0  # pretend the device is an ESP32
    run("write-bundle", "backup.zip", ok=False)
    assert not esp.writes


def bundle(path, entries):
    """Write a bundle ZIP: name → bytes, or a dict for manifest.json."""
    import json
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, json.dumps(data) if isinstance(data, dict) else data)
    return path


TABLE = (CHIP / "partitions.csv").read_text()


def test_an_ota_role_file_writes_the_next_slot_and_boots_it(run, esp):
    bundle("b.zip", {"@ota.bin": app(2)})
    run("write-bundle", "b.zip")
    assert esp.region(OTA_0[0], len(app(2))) == app(2)
    assert "OTA slot 'ota_0'" in run("get-boot")


def test_a_factory_role_file_writes_factory_and_clears_otadata(run, esp):
    run("ota", CHIP / "app-v1.bin")
    bundle("b.zip", {"@factory.bin": app(2)})
    run("write-bundle", "b.zip")
    assert esp.region(FACTORY[0], len(app(2))) == app(2)
    assert esp.region(*OTADATA) == b"\xff" * OTADATA[1]


@pytest.mark.parametrize("entries, message", [
    ({"@ota.bin": app(2), "@factory.bin": app(2)}, "both"),
    ({"@foo.bin": app(2)}, "Unknown role file"),
    ({"@ota.bin": app(2), "ota_1.bin": app(1)}, "may pick it"),
    ({"@factory.bin": app(2), "factory.bin": app(1)}, "by name and by @factory.bin"),
    ({"nope.bin": b"x"}, "'nope' is not a partition"),
    ({"manifest.json": {"ops": [{"op": "frobnicate"}]}}, "op 1: unknown op"),
    ({"manifest.json": {"ops": [{"op": "write", "partition": "storage", "file": "files/x"}]}},
     "'files/x' is not in the bundle"),
    ({"manifest.json": {"chip": "esp32c3", "ops": [{"op": "clear-boot"}]}}, "is for esp32c3"),
    ({"manifest.json": {"ops": [{"op": "set-boot", "partition": "factory"}]}}, "not an OTA app"),
])
def test_a_bad_bundle_is_refused_before_anything_is_written(run, esp, entries, message):
    bundle("b.zip", entries)
    assert message in run("write-bundle", "b.zip", ok=False)
    assert not esp.writes


def test_a_partition_name_may_not_start_with_an_at(run, tmp_path):
    (tmp_path / "t.csv").write_text("@x, data, nvs, 0x9000, 0x6000,\n")
    assert "reserved" in run("print-table", "-f", "t.csv", ok=False)


def test_manifest_ops_run_after_the_files(run, tmp_path, esp):
    (tmp_path / "example.csv").write_text(
        "key,type,encoding,value\ncfg,namespace,,\nchannel,data,string,beta\n")
    run("write-nvs", "nvs", "example.csv")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "old.txt").write_text("old")
    run("write-fs", "storage", "assets")
    run("ota", CHIP / "app-v1.bin")
    esp.writes.clear()

    bundle("b.zip", {"ota_1.bin": app(2), "files/config.json": b'{"a": 1}', "manifest.json": {
        "name": "Test update",
        "chip": "ESP32-S3",
        "ops": [
            {"op": "set-nvs", "set": {"cfg:channel": "stable", "cfg:count": "u8:3"}},
            {"op": "edit-fs", "partition": "storage", "put": {"/config.json": "files/config.json"},
             "delete": ["old.txt"]},
            {"op": "set-boot", "partition": "ota_1"},
        ]}})
    out = run("write-bundle", "b.zip")
    assert "Bundle: Test update" in out
    assert "OTA slot 'ota_1'" in run("get-boot")
    nvs = run("print-nvs", "nvs")
    assert "stable" in nvs and "count" in nvs
    listing = run("print-fs", "storage")
    assert "config.json" in listing and "old.txt" not in listing


def test_erase_and_clear_boot_ops(run, esp):
    run("ota", CHIP / "app-v1.bin")
    bundle("b.zip", {"manifest.json": {"ops": [
        {"op": "erase", "partition": "ota_0"}, {"op": "clear-boot"}]}})
    run("write-bundle", "b.zip")
    assert esp.region(*OTA_0) == b"\xff" * OTA_0[1]
    assert esp.region(*OTADATA) == b"\xff" * OTADATA[1]


def test_a_matching_table_is_not_written(run, esp):
    bundle("b.zip", {"partition_table.csv": TABLE, "storage.bin": b"x"})
    assert "already matches" in run("write-bundle", "b.zip")
    assert not [w for w in esp.writes if w[0] == 0x8000]


MOVED = TABLE.replace("storage,    data, spiffs,   0xb0000", "storage,    data, spiffs,   0xc0000")


def test_a_differing_table_is_written_by_default(run, esp):
    bundle("b.zip", {"partition_table.csv": MOVED})
    out = run("write-bundle", "b.zip")
    assert "storage: moved 0xb0000 → 0xc0000" in out
    assert [w for w in esp.writes if w[0] == 0x8000]


def test_require_refuses_a_differing_table(run, esp):
    bundle("b.zip", {"partition_table.csv": MOVED, "manifest.json": {"table": "require",
                                                                     "ops": [{"op": "clear-boot"}]}})
    assert "different from the one this bundle requires" in run("write-bundle", "b.zip", ok=False)
    assert not esp.writes


def test_require_used_keeps_a_layout_that_differs_elsewhere(run, esp):
    bundle("b.zip", {"partition_table.csv": MOVED, "ota_0.bin": app(1), "manifest.json": {
        "table": "require", "tableMatch": "used"}})
    assert "the device's is kept" in run("write-bundle", "b.zip")
    assert not [w for w in esp.writes if w[0] == 0x8000]
    assert esp.region(OTA_0[0], len(app(1))) == app(1)


def test_require_used_refuses_a_layout_that_differs_where_it_writes(run, esp):
    bundle("b.zip", {"partition_table.csv": MOVED, "storage.bin": b"x", "manifest.json": {
        "table": "require", "tableMatch": "used"}})
    assert "differs where this bundle writes" in run("write-bundle", "b.zip", ok=False)


def test_ask_needs_an_answer(run, esp):
    from click.testing import CliRunner
    from idftool.cli import cli

    bundle("b.zip", {"partition_table.csv": MOVED, "manifest.json": {"table": "ask",
                                                                     "ops": [{"op": "clear-boot"}]}})
    assert "pass -y" in run("write-bundle", "b.zip", ok=False)
    assert not esp.writes
    result = CliRunner().invoke(cli, ["-y", "-p", "/dev/virtual", "write-bundle", "b.zip"])
    assert result.exit_code == 0, result.output
    assert [w for w in esp.writes if w[0] == 0x8000]


def test_create_bundle_with_a_role_file_and_a_manifest(run_offline, tmp_path):
    import json
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "config.json").write_text("{}")
    (tmp_path / "manifest.json").write_text(json.dumps({"ops": [
        {"op": "edit-fs", "partition": "storage", "put": {"/config.json": "files/config.json"}}]}))
    out = tmp_path / "b.zip"
    run_offline(f"--partition-table-file {CHIP / 'partitions.csv'} create-bundle -o {out} "
                f"--manifest {tmp_path / 'manifest.json'} @ota {CHIP / 'app-v2.bin'}")
    with zipfile.ZipFile(out) as zf:
        assert sorted(zf.namelist()) == ["@ota.bin", "files/config.json", "manifest.json"]


def test_print_bundle_without_a_table(run_offline, tmp_path):
    bundle(tmp_path / "b.zip", {"@ota.bin": app(2), "manifest.json": {
        "name": "Field update", "ops": [{"op": "clear-boot"}]}})
    out = run_offline(f"print-bundle -f {tmp_path / 'b.zip'}")
    assert "Name: Field update" in out
    assert "Partition table: none, uses the device's" in out
    assert "Write @ota.bin to the next OTA slot" in out
    assert "Clear the OTA selection" in out
    assert "2.0.0" in out


def test_nvs_on_the_device(run, tmp_path, esp):
    (tmp_path / "example.csv").write_text(
        "key,type,encoding,value\nconfig,namespace,,\nserial,data,string,SN-0042\n")
    run("write-nvs", "nvs", "example.csv")
    # stdout holds just the value; the rest went to stderr, which the runner mixes in.
    assert run("get-nvs", "nvs", "config:serial").splitlines()[0] == "SN-0042"
    run("set-nvs", "nvs", "config:serial=SN-0043")
    assert "SN-0043" in run("print-nvs", "nvs")
    run("read-nvs", "nvs", "back.csv")
    assert "SN-0043" in (tmp_path / "back.csv").read_text()


KEY = bytes(range(32)).hex()


def test_encrypted_nvs_on_the_device(run, tmp_path, esp):
    (tmp_path / "example.csv").write_text(
        "key,type,encoding,value\nconfig,namespace,,\nserial,data,string,SN-0042\n")
    run("write-nvs", "nvs", "example.csv", "--hmac-key", KEY)
    assert b"SN-0042" not in esp.region(*NVS)
    assert "looks encrypted; pass --hmac-key" in run("print-nvs", "nvs", ok=False)
    assert "does not decrypt" in run("print-nvs", "nvs", "--hmac-key", "00" * 32, ok=False)
    run("set-nvs", "nvs", "config:serial=SN-0043", "--hmac-key", KEY)
    assert run("get-nvs", "nvs", "config:serial", "--hmac-key", KEY).splitlines()[0] == "SN-0043"

    bundle("b.zip", {"manifest.json": {"ops": [{"op": "set-nvs", "set": {"config:serial": "SN-0044"}}]}})
    esp.writes.clear()
    assert "pass --hmac-key" in run("write-bundle", "b.zip", ok=False)
    assert not esp.writes
    run("write-bundle", "b.zip", "--hmac-key", KEY)
    run("read-nvs", "nvs", "back.csv", "--hmac-key", KEY)
    assert "SN-0044" in (tmp_path / "back.csv").read_text()


def test_encrypted_nvs_files(run_offline, tmp_path):
    (tmp_path / "example.csv").write_text(
        "key,type,encoding,value\nconfig,namespace,,\nserial,data,string,SN-0042\n")
    image = tmp_path / "nvs.bin"
    run_offline(f"create-nvs {tmp_path / 'example.csv'} -o {image} --size 0x6000 --hmac-key {KEY}")
    run_offline(f"set-nvs -f {image} config:serial=SN-0043 --hmac-key {KEY}")
    run_offline(f"extract-nvs -f {image} {tmp_path / 'out.csv'} --hmac-key {KEY}")
    assert "SN-0043" in (tmp_path / "out.csv").read_text()


def test_filesystem_on_the_device(run, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "config.json").write_text('{"a": 1}')
    run("write-fs", "storage", "assets")
    assert "config.json" in run("print-fs", "storage")
    run("read-fs", "storage", "out")
    assert (tmp_path / "out" / "config.json").read_text() == '{"a": 1}'


# --- discovery ------------------------------------------------------------------------------

def test_devices_lists_ports_and_probes_them(monkeypatch):
    from click.testing import CliRunner

    import idftool.commands.misc as misc
    from idftool.cli import cli

    port = type("Port", (), dict(device="/dev/jtag", vid=0x303A, pid=0x1001,
                                 serial_number="7C:2C:67:92:79:C0", location="1-1.4",
                                 description="", product=None))()
    monkeypatch.setattr(misc, "get_port_list", lambda: [port])
    monkeypatch.setattr(misc, "probe_port", lambda device, baud, identify=None: {
        "port": device, "chip": "ESP32-S3", "mac": "7c:2c:67:92:79:c0", "identity": None,
        "error": None})

    out = CliRunner().invoke(cli, ["ports"]).output
    assert "ESP USB-Serial/JTAG" in out and "7C:2C:67:92:79:C0" in out
    out = CliRunner().invoke(cli, ["devices", "--probe"]).output
    assert "ESP32-S3" in out and "7c:2c:67:92:79:c0" in out
    assert "Device" not in out


def test_probe_defaults_to_env_and_no_probe_overrides(monkeypatch):
    from click.testing import CliRunner

    import idftool.commands.misc as misc
    from idftool.cli import cli

    seen = []
    monkeypatch.setattr(misc, "list_devices", lambda state, probe: seen.append(probe))
    env = {"IDFTOOL_PROBE": "1"}
    for args, probe in ([["devices"], True], [["--no-probe", "devices"], False],
                        [["devices", "--no-probe"], False]):
        seen.clear()
        CliRunner().invoke(cli, args, env=env)
        assert seen == [probe], args


def test_devices_probe_shows_what_a_plugin_names_the_device(monkeypatch):
    import idftool.commands.misc as misc
    import idftool.plugins as plugins
    from idftool.cli import cli

    port = type("Port", (), dict(device="/dev/jtag", vid=0x303A, pid=0x1001,
                                 serial_number="7C:2C:67:92:79:C0", location="1-1.4",
                                 description="", product=None))()
    monkeypatch.setattr(misc, "get_port_list", lambda: [port])
    monkeypatch.setattr(plugins, "_loaded", [("board", lambda esp: "Harvest Controller v3")])
    monkeypatch.setattr(misc, "probe_port", lambda device, baud, identify=None: {
        "port": device, "chip": "ESP32-S3", "mac": "7c:2c:67:92:79:c0",
        "identity": identify(None), "error": None})

    out = CliRunner().invoke(cli, ["devices", "--probe"]).output
    assert "Device" in out and "Harvest Controller v3" in out


def test_enter_bootloader_waits_for_the_port(monkeypatch):
    import idftool.commands.misc as misc
    from idftool.cli import cli

    seen = []
    monkeypatch.setattr(misc.os.path, "exists", lambda path: True)
    monkeypatch.setattr(misc, "detect_chip", lambda port, **_: seen.append(port) or VirtualEsp(b""))
    out = CliRunner().invoke(cli, ["-p", "/dev/virtual", "enter-bootloader"]).output
    assert seen == ["/dev/virtual"] and "In download mode: ESP32-S3" in out


# --- app-info -------------------------------------------------------------------------------

def test_app_info_describes_a_binary():
    from idftool.cli import cli

    out = CliRunner().invoke(cli, ["app-info", "-f", str(CHIP / "app-v1.bin")]).output
    assert "idftool_test" in out and "1.0.0" in out and "ESP32S3" in out


def test_app_info_describes_a_bootloader():
    from idftool.cli import cli

    out = CliRunner().invoke(cli, ["app-info", "-f", str(CHIP / "bootloader.bin")]).output
    assert "Bootloader: ESP32S3 (offset=0x0)" in out


def test_app_info_refuses_a_non_app(tmp_path):
    from idftool.cli import cli

    (tmp_path / "junk.bin").write_bytes(b"\xff" * 64)
    assert CliRunner().invoke(cli, ["app-info", "-f", str(tmp_path / "junk.bin")]).exit_code


# --- the device picker ----------------------------------------------------------------------

@pytest.fixture
def picker(monkeypatch):
    """Drive the device picker with keystrokes: two ESP ports and an adapter port."""
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    import idftool.ports as ports

    def port(device, vid, pid, serial):
        return type("Port", (), dict(device=device, vid=vid, pid=pid, serial_number=serial,
                                     description="", product=None))()

    listed = [port("/dev/jtag0", 0x303A, 0x1001, "7C:2C:67:92:79:C0"),
              port("/dev/jtag1", 0x303A, 0x1001, "B8:F8:62:49:EA:14"),
              port("/dev/bridge", 0x067B, 0x23C3, "DPEDf103Y23")]
    probed = []
    monkeypatch.setattr(ports.serial_ports, "get_port_list", lambda: listed)
    monkeypatch.setattr(ports, "held_reason", lambda port: None)
    monkeypatch.setattr(ports, "probe_port", lambda port, baud, identify=None: (
        probed.append(port) or {"port": port, "chip": "ESP32-S3", "mac": "7c:2c:67:92:79:c0",
                                "identity": None, "error": None}))
    monkeypatch.setattr(ports.sys.stdin, "isatty", lambda: True, raising=False)

    def pick(keys, **kwargs):
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
            pipe.send_text(keys)
            return ports.select_device(115200, **kwargs)

    pick.probed = probed
    return pick


def test_the_picker_chooses_without_connecting(picker):
    found = picker("\r")
    assert found["port"] == "/dev/jtag0" and found["mac"] == "7c:2c:67:92:79:c0"
    assert picker.probed == []


def test_the_picker_moves_down(picker):
    assert picker("\x1b[B\x1b[B\r")["port"] == "/dev/bridge"


def test_p_probes_only_the_highlighted_port(picker):
    found = picker("p\r")
    assert picker.probed == ["/dev/jtag0"]
    assert found["chip"] == "ESP32-S3"


def test_probe_connects_to_every_port(picker):
    picker("\r", probe=True)
    assert sorted(picker.probed) == ["/dev/bridge", "/dev/jtag0", "/dev/jtag1"]


def test_identify_probes_every_port_unless_probe_is_false(picker):
    picker("\r", identify=lambda esp: None)
    assert sorted(picker.probed) == ["/dev/bridge", "/dev/jtag0", "/dev/jtag1"]
    picker.probed.clear()
    picker("\r", identify=lambda esp: None, probe=False)
    assert picker.probed == []


def test_r_refreshes_and_q_quits(picker):
    import rich_click as click

    assert picker("r\r")["port"] == "/dev/jtag0"
    with pytest.raises(click.exceptions.Abort):
        picker("q")


def test_a_held_port_is_shown_and_refused(picker, monkeypatch):
    import rich_click as click

    import idftool.ports as ports

    monkeypatch.setattr(ports, "held_reason", lambda port: "held by idf.py (pid 1)")
    with pytest.raises(click.ClickException, match="held by idf.py"):
        picker("\r")


def test_no_device_is_offered_only_when_asked(picker):
    none = "\x1b[B" * 3 + "\r"  # past the three ports
    assert picker(none, allow_none=True) == {"port": None}
    # Without it, the same keys land on "Enter a port manually…".
    assert picker(none + "/dev/typed\r")["port"] == "/dev/typed"


# --- idf.py ---------------------------------------------------------------------------------

@pytest.mark.parametrize("args, needed", [
    (["build"], False),
    (["menuconfig"], False),
    (["-C", "project", "build", "size"], False),
    (["dfu-flash"], False),
    (["efuse-common-table"], False),
    (["flash"], True),
    (["build", "flash", "monitor"], True),
    (["app-flash"], True),
    (["storage-flash"], True),
    (["erase_flash"], True),
    (["efuse-summary"], True),
    (["read-otadata"], True),
])
def test_idf_py_asks_only_for_actions_that_need_a_device(args, needed):
    from idftool.commands.misc import idf_py_needs_device

    assert idf_py_needs_device(args) is needed


@pytest.fixture
def idf_py(monkeypatch):
    """Run the idf.py command; returns what idf.py would have been run with."""
    import idftool.commands.misc as misc
    from idftool.cli import cli

    ran = []
    monkeypatch.setattr(misc, "_idf_py", lambda: ["idf.py"])
    monkeypatch.setattr(misc, "_exec", ran.append)

    def invoke(*args, port=None):
        import idftool.state as state

        monkeypatch.setattr(state.State, "resolve_port", lambda self, allow_none=False: port)
        ran.clear()
        result = CliRunner().invoke(cli, list(args))
        assert result.exit_code == 0, result.output + repr(result.exception)
        return ran[0]

    return invoke


def test_idf_py_adds_the_chosen_port(idf_py):
    assert idf_py("idf.py", "flash", "monitor", port="/dev/a") == \
        ["idf.py", "-p", "/dev/a", "flash", "monitor"]


def test_idf_py_passes_through_when_no_device_is_needed_or_chosen(idf_py):
    assert idf_py("idf.py", "build", port="/dev/a") == ["idf.py", "build"]
    assert idf_py("idf.py", "flash", port=None) == ["idf.py", "flash"]


def test_idf_py_keeps_a_port_it_was_given(idf_py):
    assert idf_py("idf.py", "-p", "/dev/b", "flash", port="/dev/a") == \
        ["idf.py", "-p", "/dev/b", "flash"]


def test_idf_py_is_given_b_only_when_idftool_was(idf_py):
    assert idf_py("-b", "921600", "idf.py", "flash", port="/dev/a") == \
        ["idf.py", "-b", "921600", "-p", "/dev/a", "flash"]
    assert idf_py("-b", "921600", "idf.py", "-b", "460800", "flash", port="/dev/a") == \
        ["idf.py", "-p", "/dev/a", "-b", "460800", "flash"]
    assert idf_py("idf.py", "flash", port="/dev/a") == ["idf.py", "-p", "/dev/a", "flash"]


def test_monitor_is_given_b_only_when_idftool_was(monkeypatch):
    import sys

    import idftool.state as state
    from esp_idf_monitor import idf_monitor
    from idftool.cli import cli

    seen = []
    monkeypatch.setattr(idf_monitor, "main", lambda: seen.append(sys.argv[1:]))
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    monkeypatch.setattr(state.State, "resolve_port", lambda self, allow_none=False: "/dev/a")
    for args in (["-b", "921600", "monitor"], ["monitor"], ["-b", "115200", "monitor"]):
        assert CliRunner().invoke(cli, args).exit_code == 0
    assert seen == [["--baud", "921600", "--port", "/dev/a"], ["--port", "/dev/a"],
                    ["--baud", "115200", "--port", "/dev/a"]]


@pytest.mark.parametrize("name", ["esp-idf-monitor", "idf-monitor"])
def test_monitor_aliases(monkeypatch, name):
    import sys

    import idftool.state as state
    from esp_idf_monitor import idf_monitor
    from idftool.cli import cli

    seen = []
    monkeypatch.setattr(idf_monitor, "main", lambda: seen.append(sys.argv[1:]))
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    monkeypatch.setattr(state.State, "resolve_port", lambda self, allow_none=False: "/dev/a")
    assert CliRunner().invoke(cli, [name, "--timestamps"]).exit_code == 0
    assert seen == [["--port", "/dev/a", "--timestamps"]]


def test_idf_py_passes_help_through(idf_py):
    assert idf_py("idf.py", "--help") == ["idf.py", "--help"]
