# idftool test suite

Two separate suites, in the esptool style:

| File              | Needs hardware? | Run with |
|-------------------|-----------------|----------|
| `test_offline.py` | No              | `pytest test/test_offline.py` (or `make test-offline`) |
| `test_device.py`  | Yes — a board   | `pytest test/test_device.py --port <PORT> --chip <CHIP>` (or `make test-device PORT=...`) |

Device tests are marked `device` and **skipped automatically** unless `--port` is given, so a bare
`pytest` only runs the offline tests and never touches a board.

## Offline tests

Exercise the file/CSV/help/error paths with no device. Fast; safe to run anywhere. Sample inputs
live in `samples/`.

## Device tests

> **These erase and rewrite the connected device's flash.**

They provision a known state (`write-image` of a bootloader + partition table + app image) and then cycle every command:
partition table read/write, raw partition read/write/erase, NVS, factory/OTA/boot selection,
bundle round-trip, and full-image round-trip.

They need binary **fixtures** in `fixtures/<chip>/`, built once from the tiny ESP-IDF app in
`project/`:

```sh
. $IDF_PATH/export.sh
test/project/build_fixtures.sh esp32s3
pytest test/test_device.py --port /dev/cu.usbmodem101 --chip esp32s3
```

Skip the slow full-flash tests with `-m "not slow"`.
