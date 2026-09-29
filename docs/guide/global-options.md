# Global options

These go **before** the command name:

```bash
idftool -p /dev/cu.usbmodem1101 --no-reset get-boot
```

| Option | Purpose |
|--------|---------|
| `-p`, `--port PATH` | Serial port. Without it, idftool asks; see [Choosing a device](choosing-a-device.md). |
| `-m`, `--mac MAC` | Use the device with this MAC address. |
| `-b`, `--baud N` | Serial baud rate for flashing. Default 115200, esptool's ROM baud. |
| `--probe` | Connect to every port to identify its board. Resets them. |
| `-y`, `--yes` | Don't ask which device to use. |
| `--no-reset` | Skip the hard reset after a command. Also passed to [`monitor`](../commands/misc.md#monitor). |
| `--partition-table-file PATH` | Use a CSV or binary partition table from disk instead of reading the device's. |
| `--partition-table-offset OFFSET` | Where the partition table lives in flash. Default `0x8000`. |
| `--partition-table-size SIZE` | Size of the partition table region. Default `0x1000`. |
| `--primary-bootloader-offset OFFSET` | Bootloader offset, or a chip name like `esp32s3`. Only needed to address `bootloader` by name offline; taken from the chip otherwise. |
| `--recovery-bootloader-offset OFFSET` | Recovery bootloader offset. Same scope as above. |
