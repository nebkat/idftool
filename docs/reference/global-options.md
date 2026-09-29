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
| `-b`, `--baud N` | Serial baud rate for flashing. Default 115200, the ROM bootloader's rate. See [serial connection](https://docs.espressif.com/projects/esptool/en/latest/esp32/esptool/serial-connection.html). |
| `--probe` | Connect to every port to identify its board. Resets them. |
| `-y`, `--yes` | Don't ask which device to use. |
| `--no-reset` | Skip the [hard reset](https://docs.espressif.com/projects/esptool/en/latest/esp32/esptool/advanced-options.html#reset-after-operation-after) after a command. Also passed to [`monitor`](monitor.md#monitor). |
| `--partition-table-file PATH` | Use a CSV or binary partition table from disk instead of reading the device's. |
| `--partition-table-offset OFFSET` | Where the partition table lives in flash. Default `0x8000`. |
| `--partition-table-size SIZE` | Size of the partition table region. Default `0x1000`. |
| `--primary-bootloader-offset OFFSET` | Bootloader offset, or a chip name like `esp32s3`. Only needed to address `bootloader` by name offline; taken from the chip otherwise. |
| `--recovery-bootloader-offset OFFSET` | Recovery bootloader offset. Same scope as above. |
