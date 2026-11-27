# Global options

These go **before** the command name:

```bash
idftool -p /dev/cu.usbmodem1101 --no-reset get-boot
```

| Option | Purpose |
|--------|---------|
| `-p`, `--port PATH` | Serial port. Without it, `idftool` asks; see [Choosing a device](../guide/choosing-a-device.md). |
| `-m`, `--mac MAC` | Use the device with this MAC address. See [by MAC address](../guide/choosing-a-device.md#by-mac-address). |
| `--usb-serial SERIAL` | Use the port with this USB serial number. See [by USB serial number](../guide/choosing-a-device.md#by-usb-serial-number). |
| `--only-device` | Use the only ESP or USB-serial adapter port connected. An error if there are none or several. |
| `-b`, `--baud N` | Serial baud rate for flashing. Default 115200, the ROM bootloader's rate. See [serial connection](https://docs.espressif.com/projects/esptool/en/latest/esp32/esptool/serial-connection.html). If given, also passed to [`monitor`](monitor.md#monitor) and [`idf.py`](idf-py.md#idfpy). |
| `--probe`, `--no-probe` | Connect to every port to identify its board. Resets them. Defaults to `IDFTOOL_PROBE` (`1` to probe). |
| `-y`, `--yes` | Don't ask which device to use. One must then be given with `-p`, `-m`, `--usb-serial` or `--only-device`. |
| `--kill-holders` | Kill other processes holding the port before connecting, without asking (macOS/Linux). Without it, a held port is only reported, with an offer to kill at a terminal. |
| `--no-reset` | Skip the [hard reset](https://docs.espressif.com/projects/esptool/en/latest/esp32/esptool/advanced-options.html#reset-after-operation-after) after a command. Also passed to [`monitor`](monitor.md#monitor). |
| `--partition-table-file PATH` | Use a CSV or binary partition table from disk instead of reading the device's. |
| `--partition-table-offset OFFSET` | Where the partition table lives in flash. Default `0x8000`. |
| `--partition-table-size SIZE` | Size of the partition table region. Default `0x1000`. |
| `--primary-bootloader-offset OFFSET` | Bootloader offset, or a chip name like `esp32s3`. Only needed to address `bootloader` by name offline; taken from the chip otherwise. |
| `--recovery-bootloader-offset OFFSET` | Recovery bootloader offset. Same scope as above. |
| `--flash-size SIZE` | The device's flash size, e.g. `16MB`, for when it can't be found from the flash chip or the bootloader. Partition tables are checked against it, and `dump-image` reads that much. |
