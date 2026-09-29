# idftool

A CLI built on [esptool](https://github.com/espressif/esptool) that understands
the ESP-IDF partition table. Flash apps, switch OTA slots, and read or write
partitions by name, without working out offsets by hand.

**[Documentation](https://nebkat.github.io/idftool/)**

## Highlights

### Device selection on connection

Without `-p`, idftool lists the connected devices and asks which to use. The
list comes from USB, so no board is reset by looking at it; ESP
USB-Serial/JTAG ports show their MAC straight away, and `p` probes a port for
its chip. Or name a board by MAC address, wherever it's plugged in:

```bash
idftool -m 9c:13:9e:1b:d4:6c ota build/app.bin
```

### Firmware flashing

Flash the factory partition, or the next OTA slot and switch to it, the way an
OTA update from the firmware would. Writes that match what's already in flash
are skipped, and only changed sectors are rewritten.

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

Writes can't overflow their partition, app binaries are checked against the
connected chip, and a file passed where a partition belongs is caught before
connecting.

### Bundles and images

Pack several partitions (and optionally the partition table) into one ZIP and
flash them in one command, or dump and restore a whole flash image.

```bash
idftool dump-bundle backup.zip
idftool write-bundle release.zip
```

### Filesystems and NVS

Build FAT, littlefs, or SPIFFS images from a directory and flash them, or pull
them off the device. Generate NVS from CSV, read a key into a shell, or change
one on a live device.

```bash
idftool write-fs storage assets/
serial=$(idftool get-nvs nvs storage:serial)
idftool set-nvs nvs storage:serial=SN-0001
```

## Installation

```bash
pipx install idftool
```

Or download a binary from the
[Releases](https://github.com/nebkat/idftool/releases) page. See
[Installation](https://nebkat.github.io/idftool/installation/) for more.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
