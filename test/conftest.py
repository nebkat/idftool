"""Shared pytest configuration for the idftool test suite.

Two kinds of tests, kept in separate files (like esptool):

* ``test_offline.py`` — no hardware; runs on a plain ``pytest``.
* ``test_device.py``  — needs a real ESP connected via ``--port``; marked ``device`` and skipped
  otherwise, so a bare ``pytest`` never touches a board.

Run the device tests with:

    pytest test/test_device.py --port /dev/cu.usbmodem101 --chip esp32s3
"""
import subprocess
import sys
from pathlib import Path

import pytest
from esptool import ESPLoader

TEST_DIR = Path(__file__).parent
FIXTURES = TEST_DIR / "fixtures"
SAMPLES = TEST_DIR / "samples"


def pytest_addoption(parser):
    parser.addoption("--port", action="store", default=None,
                     help="Serial port of a connected ESP device (enables the device tests)")
    parser.addoption("--chip", action="store", default="esp32s3",
                     help="Chip type the fixtures were built for (default: esp32s3)")
    parser.addoption("--baud", action="store", default=ESPLoader.ESP_ROM_BAUD, help="Baud rate")


def pytest_configure(config):
    config.addinivalue_line("markers", "device: requires a real ESP connected via --port")
    config.addinivalue_line("markers", "slow: slow device test (full-flash dump / reflash)")


def pytest_collection_modifyitems(config, items):
    # Skip device tests unless --port is given, so `pytest` with no args is always safe.
    if config.getoption("--port"):
        return
    skip = pytest.mark.skip(reason="needs a real device: pass --port")
    for item in items:
        if "device" in item.keywords:
            item.add_marker(skip)


def _make_runner(port, baud):
    def run(args, expect_error=False, timeout=2400):
        """Run the idftool CLI as a subprocess; return combined stdout+stderr.

        `args` is a string ("read nvs out.bin") or a list. Device commands get --port/--baud
        prepended automatically when a port is configured. Asserts exit status (0, or non-zero
        when expect_error=True).

        The timeout is generous because a full-flash dump/reflash of a large flash over a slow
        serial link (some USB-serial bridges are only stable at 115200) can take many minutes.
        """
        cmd = [sys.executable, "-m", "idftool"]
        if port:
            cmd += ["--port", port, "--baud", str(baud)]
        cmd += args.split() if isinstance(args, str) else [str(a) for a in args]
        proc = subprocess.run(cmd, cwd=TEST_DIR, capture_output=True, text=True, timeout=timeout)
        out = proc.stdout + proc.stderr
        if expect_error:
            assert proc.returncode != 0, f"`idftool {args}` unexpectedly succeeded:\n{out}"
        else:
            assert proc.returncode == 0, f"`idftool {args}` failed ({proc.returncode}):\n{out}"
        return out
    return run


@pytest.fixture(scope="session")
def port(pytestconfig):
    return pytestconfig.getoption("--port")


@pytest.fixture(scope="session")
def chip(pytestconfig):
    return pytestconfig.getoption("--chip")


@pytest.fixture(scope="session")
def baud(pytestconfig):
    return pytestconfig.getoption("--baud")


@pytest.fixture(scope="session")
def idf(port, baud):
    """Runner for device tests — includes --port/--baud so commands talk to the board."""
    return _make_runner(port, baud)


@pytest.fixture(scope="session")
def run_offline(baud):
    """Runner for offline tests — never adds --port, so nothing connects to a device."""
    return _make_runner(None, baud)


@pytest.fixture(scope="session")
def assets(chip):
    """Path to the built fixtures for the target chip; skips if they haven't been built yet."""
    d = FIXTURES / chip
    if not (d / "flash-image.bin").exists():
        pytest.skip(f"no fixtures in {d} — build them with test/project/build_fixtures.sh {chip}")
    return d
