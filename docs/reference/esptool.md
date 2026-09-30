# esptool

## `esptool`

Run [`esptool`](https://docs.espressif.com/projects/esptool/en/latest/esp32/)
using `idftool`'s [device selection](../guide/choosing-a-device.md).
Commands that don't need a device, like `merge-bin` or `image-info`, run
straight away.

```bash
idftool esptool chip-id
idftool -m 9c:13:9e:1b:d4:6c esptool read-flash 0 0x1000 boot.bin
```

Everything after `esptool` goes to `esptool`. A `-p` or `--port-filter` given
there is used as-is, without the picker. `-b` and `--no-reset`, if given, are
passed on as `--baud` and `--after no-reset`.
