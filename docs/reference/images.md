# Images

An image is one contiguous copy of flash: bootloader, partition table, and
partitions in a single file. Useful for archiving a known-good unit, recovering
a bricked one, or feeding production programmers that don't speak the
[`esptool`](https://docs.espressif.com/projects/esptool/en/latest/esp32/) protocol.

!!! note "Images from other tools"
    [`idf.py merge-bin`](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/tools/idf-py.html#merge-binaries-merge-bin)
    and [`esptool merge-bin`](https://docs.espressif.com/projects/esptool/en/latest/esp32/esptool/basic-commands.html#merge-bin) make
    images too, and `idftool`
    reads and writes them. Those tools can merge any set of binaries at any
    addresses; `idftool` assumes an image starts at `0x0`, so the partition
    table is at its usual offset in the file. Only merge-bin's default raw
    output with no `--target-offset` works here.

## `create-image`

Combine partition binaries into a flash image, offline, from local files and a
partition table.

```bash
idftool --partition-table-file partitions.csv create-image \
  -o merged.img --flash-partition-table \
  ota_0 build/app.bin storage build/spiffs.bin
```

## `dump-image`

Read the entire flash into an image file. Without a filename it's named
`{chip}-{mac}-{timestamp}.img`, e.g. `esp32-s3-aabbccddeeff-20260522-143000.img`.
Works even when the device's partition table is corrupt.

```bash
idftool dump-image                  # auto-named
idftool dump-image my-backup.img
```

## `write-image`

Erase the whole chip and write a flash image to it. The counterpart of
`dump-image`. Alias: `reflash`.

```bash
idftool write-image build/full-flash.img
idftool write-image --no-erase --diff build/full-flash.img
```

!!! warning "Full chip erase"
    The erase covers the whole chip, not just the span the image covers, so
    anything past the end of the image is lost too. `--no-erase` writes only
    what the image contains.

`--skip-flashed` needs `--no-erase`: nothing can already match a chip that was
just wiped, so asking for both is refused.

`--skip-flashed` treats the image as one region, so it only skips when the
whole image matches, which stops being true the moment the device writes its
own NVS. `--diff` compares sector by sector and rewrites only what changed.
To check whether a unit already runs a given build, flash just the app with
[`ota`](firmware.md#ota) or [`factory`](firmware.md#factory).

Takes the [write options](write-options.md).

## `print-image`

Inspect a flash image without a device: which chip its bootloader is for, the
partition table inside it and, for each app partition holding a valid app, its
project name, version, IDF version, compile time, ELF SHA-256, and target chip.

```console
$ idftool print-image -f build/full-flash.img
Image: build/full-flash.img (0xc0000 bytes)

Bootloader: ESP32S3 (offset=0x0)

╭──────────┬──────┬─────────┬─────────┬──────┬───────────────╮
│ Name     │ Type │ Subtype │  Offset │ Size │ App           │
├──────────┼──────┼─────────┼─────────┼──────┼───────────────┤
│ nvs      │ data │ nvs     │  0x9000 │  24K │               │
│ otadata  │ data │ ota     │  0xf000 │   8K │               │
│ phy_init │ data │ phy     │ 0x11000 │   4K │               │
│ factory  │ app  │ factory │ 0x20000 │ 192K │ my-app 1.2.0  │
│ ota_0    │ app  │ ota_0   │ 0x50000 │ 192K │ empty         │
│ ota_1    │ app  │ ota_1   │ 0x80000 │ 192K │ empty         │
│ storage  │ data │ spiffs  │ 0xb0000 │  64K │               │
╰──────────┴──────┴─────────┴─────────┴──────┴───────────────╯

Partition 'factory' (offset=0x20000):
  Project name:     my-app
  Version:          1.2.0
  IDF version:      v6.0.2
  Secure version:   0
  Compiled:         Sep 29 2026 14:30:00
  ELF SHA256:       4fd8f31259d82483f0fc34d4422b1aefb6ead9da845395d5ea96a135ed32c78a
  Chip:             ESP32S3 (rev 0 to 99)
```
