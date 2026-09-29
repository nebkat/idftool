# Images

An image is one contiguous copy of flash: bootloader, partition table, and
partitions in a single file. Useful for archiving a known-good unit, recovering
a bricked one, or feeding production programmers that don't speak the esptool
protocol.

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

Takes the [write options](../guide/write-options.md).

## `print-image`

Inspect a flash image without a device: the partition table inside it and, for
each app partition holding a valid app, its project name, version, IDF version,
compile time, ELF SHA-256, and target chip.

```bash
idftool print-image -f build/full-flash.img
```
