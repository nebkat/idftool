# Bundles

A bundle is a plain ZIP with one `.bin` per partition, named after the
partition, and optionally a `bootloader.bin` and a `partition_table.csv`. Use them to hand a build
from CI to whoever flashes it, or to archive exactly what shipped.

```text
release.zip
├── partition_table.csv
├── ota_0.bin
└── storage.bin
```

## `create-bundle`

Pack partition binaries into a bundle. `--flash-partition-table` includes the
partition table CSV so [`write-bundle`](#write-bundle) flashes it too.

```bash
idftool --partition-table-file partitions.csv create-bundle \
  -o release.zip --flash-partition-table \
  ota_0 build/app.bin storage build/spiffs.bin
```

## `dump-bundle`

Read every partition off the device into a bundle, always with
`bootloader.bin` and `partition_table.csv`. Without a filename it's named
`{chip}-{mac}-{timestamp}.zip`.

```bash
idftool dump-bundle                 # auto-named
idftool dump-bundle my-backup.zip
```

## `write-bundle`

Flash every binary in a bundle. If the bundle has a `partition_table.csv`,
`idftool` uses it instead of the device's table, and rewrites the device's table
to match. Apps and the bootloader are checked against the chip before
anything is written.

```bash
idftool write-bundle release.zip
```

Takes the [write options](write-options.md).

## `print-bundle`

Inspect a bundle without a device: its bootloader's chip, its partition table
and, for each app partition present, the app description.

```console
$ idftool print-bundle -f release.zip
Bundle: release.zip (0x2828d bytes)
Partitions included: ota_0

Bootloader: none

╭──────────┬──────┬─────────┬─────────┬──────┬───────────────╮
│ Name     │ Type │ Subtype │  Offset │ Size │ App           │
├──────────┼──────┼─────────┼─────────┼──────┼───────────────┤
│ nvs      │ data │ nvs     │  0x9000 │  24K │               │
│ otadata  │ data │ ota     │  0xf000 │   8K │               │
│ phy_init │ data │ phy     │ 0x11000 │   4K │               │
│ factory  │ app  │ factory │ 0x20000 │ 192K │ empty         │
│ ota_0    │ app  │ ota_0   │ 0x50000 │ 192K │ my-app 1.2.0  │
│ ota_1    │ app  │ ota_1   │ 0x80000 │ 192K │ empty         │
│ storage  │ data │ spiffs  │ 0xb0000 │  64K │               │
╰──────────┴──────┴─────────┴─────────┴──────┴───────────────╯

Partition 'ota_0' (offset=0x50000):
  Project name:     my-app
  Version:          1.2.0
  IDF version:      v6.0.2
  Secure version:   0
  Compiled:         Sep 29 2026 14:30:00
  ELF SHA256:       4fd8f31259d82483f0fc34d4422b1aefb6ead9da845395d5ea96a135ed32c78a
  Chip:             ESP32S3 (rev 0 to 99)
```
