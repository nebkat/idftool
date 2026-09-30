"""Every device command against a virtual ESP32-S3: an in-memory flash, no hardware.

The flash starts as the committed fixture image (bootloader, partition table, and the v1 app
in `factory`), so commands read and write real partition data and the tests check the bytes.
"""
import hashlib
import zipfile

import pytest
from click.testing import CliRunner

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
    for module in (state, table, images):
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


def test_idf_py_passes_help_through(idf_py):
    assert idf_py("idf.py", "--help") == ["idf.py", "--help"]
