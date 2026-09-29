# `idftool`

[![PyPI](https://img.shields.io/pypi/v/idftool.svg)](https://pypi.org/project/idftool/) [![CI](https://github.com/nebkat/idftool/actions/workflows/ci.yml/badge.svg)](https://github.com/nebkat/idftool/actions/workflows/ci.yml) [![Coverage status](https://coveralls.io/repos/github/nebkat/idftool/badge.svg?branch=main)](https://coveralls.io/github/nebkat/idftool?branch=main) [![Docs](https://img.shields.io/badge/docs-latest-blue.svg)](https://nebkat.github.io/idftool/)

The ultimate CLI tool for interacting with Espressif devices. Built on
[`esptool`](https://docs.espressif.com/projects/esptool/en/latest/esp32/) and aware of the ESP-IDF
partition table: flash apps, switch OTA slots, and read or write partitions by
name, without working out offsets by hand.

**[Documentation](https://nebkat.github.io/idftool/)**

> [!TIP]
> **Install `idftool` with [pipx](https://pipx.pypa.io)**
>
> ```bash
> pipx install idftool
> ```
>
> Or download a binary from [Releases](https://github.com/nebkat/idftool/releases).
> See [Installation](https://nebkat.github.io/idftool/latest/installation/) for more.

> [!NOTE]
> **Or use it in the browser**
>
> [esp-web-toolkit](https://nebkat.github.io/esp-web-toolkit/) is the
> browser-based equivalent: the same tools with a UI, nothing to install.

## Highlights

### Device selection on connection

No more guessing which device is on which port. Run any command without `-p`
and `idftool` lists the connected devices and asks which to use:

```console
$ idftool ota build/app.bin
? Select device (↑↓ move · ↵ select · p probe · r refresh · k kill · q quit)
 » /dev/cu.usbmodem11401         ESP USB-Serial/JTAG   7c:2c:67:92:79:c0
   /dev/cu.usbmodem2101          ESP USB-Serial/JTAG   b8:f8:62:49:ea:14
   /dev/cu.PL2303G-USBtoUART10   PL2303GT
   ✎ Enter a port manually…
   ↻ Refresh
   ✕ Quit
```

Pick one, and it tells you how to skip the question next time:

```console
Device: /dev/cu.usbmodem11401 — ESP USB-Serial/JTAG · 7c:2c:67:92:79:c0
Re-run with: idftool -p /dev/cu.usbmodem11401 ota build/app.bin
         or: idftool -m 7c:2c:67:92:79:c0 ota build/app.bin
...
Writing 'my-app v1.2.0' to partition 'ota_1'...
Setting boot partition to 'ota_1'...
```

The `-m` form finds the board by its MAC address, wherever it's plugged in.

### Firmware flashing

Flash to the default (factory) or next available (OTA) app partition, with no
offsets to memorise. An OTA flash switches to the new slot the way a real
update would, and only the sectors that changed are rewritten.

```bash
idftool factory build/app.bin
idftool ota build/app.bin
idftool set-boot ota_1
```

### Partition-name addressing

Read, write, erase, or hex-dump a partition by name, or a slice of one:

```bash
idftool read nvs nvs.bin
idftool write storage build/spiffs.bin
idftool view 'log[-0x1000:]'
```

### Safety checks

Everything is checked before flash is touched:

- Data has to fit the partition it's written to.
- Apps have to be valid images, built for the connected chip.
- A file name given where a partition name belongs is caught before
  connecting, with the corrected command.

### Bundles and images

Pack several partitions (and optionally the partition table) into one ZIP and
flash them in one command, or dump and restore a whole flash image.

```bash
idftool dump-bundle backup.zip
idftool write-bundle release.zip
```

### Filesystems and NVS

Build FAT, littlefs, or SPIFFS images from a directory and flash them, or pull
them off the device. The filesystem type comes from the partition table, so
you don't need to say which. Generate NVS from CSV, read a key into a shell, or change
one on a live device.

```bash
idftool write-fs storage assets/
serial=$(idftool get-nvs nvs storage:serial)
idftool set-nvs nvs storage:serial=SN-0001
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
