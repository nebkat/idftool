# idftool test suite

Two separate suites, in the esptool style:

| File              | Needs hardware? | Run with |
|-------------------|-----------------|----------|
| `test_offline.py` | No              | `pytest test/test_offline.py` (or `make test-offline`) |
| `test_device.py`  | Yes — a board   | `pytest test/test_device.py --port <PORT> --chip <CHIP>` (or `make test-device PORT=...`) |

Device tests are marked `device` and **skipped automatically** unless `--port` is given, so a bare
`pytest` only runs the offline tests and never touches a board.

## Offline tests

Exercise the file/CSV/help/error paths with no device — fast and safe to run anywhere. Sample
inputs live in `samples/`. `print-image` and `print-bundle` are covered here too (they only read a
file), against the built fixtures, so they don't depend on a slow device dump.

## Device tests

> **These erase and rewrite the connected device's flash.**

They provision a known state (`write-image` of a bootloader + partition table + app image) and then
cycle every command: partition table read/write, raw partition read/write/erase, NVS,
factory/OTA/boot selection, and the bundle / full-image round-trips.

They need binary **fixtures** in `fixtures/<chip>/`, built once from the tiny ESP-IDF app in
`project/`:

```sh
. $IDF_PATH/export.sh
test/project/build_fixtures.sh esp32s3
make test-device PORT=/dev/cu.usbmodem101 CHIP=esp32s3        # fast core
make test-device-full PORT=/dev/cu.usbmodem101 CHIP=esp32s3   # + slow full-flash round-trips
```

`make test-device` runs the quick per-command tests. The **slow** full-flash tests (`dump-image`
of the whole flash, and the bundle round-trip) are opt-in via `test-device-full` (or dropping the
`-m "not slow"` filter) — they move megabytes and take many minutes.

**Baud note:** the suite uses esptool's default (115200), which is reliable everywhere. Higher rates
speed up UART boards but corrupt sustained transfers on USB-Serial-JTAG chips — pass `--baud` only
if you know your board tolerates it.
