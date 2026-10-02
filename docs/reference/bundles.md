# Bundles

A bundle is a ZIP whose filenames say what to flash. Use them to hand a build
from CI to whoever flashes it, or to archive exactly what shipped.

| File | Flashing it |
|------|-------------|
| `partition_table.csv` or `.bin` | Replaces the partition table, if it differs. Written first. |
| `bootloader.bin` | Writes the bootloader. |
| `@factory.bin` | Flashes the app to the factory partition (or `ota_0`) and clears otadata, like [`factory`](firmware.md#factory). |
| `@ota.bin` | Writes the app to the next OTA slot and boots it, like [`ota`](firmware.md#ota). |
| `<name>.bin` | Writes the partition called `name`. |
| `manifest.json` | Optional: a name, the target chip, table rules and extra ops. See [Manifest](#manifest). |

```text
release.zip
├── partition_table.csv
├── @ota.bin
└── storage.bin
```

They're written in that order. A bundle can't have both `@factory.bin` and
`@ota.bin`, or name a partition the role file may write. Partition names can't
start with `@`. Files in subdirectories are only used by manifest ops.

Before anything is written, every app and bootloader is checked against the
chip, and every partition the bundle names must exist in the table it will
meet: the bundle's own, else the device's.

## Manifest

```json
{
  "name": "MS5 v0.17.0",
  "description": "Field update: new app, reset the channel",
  "chip": "esp32s3",
  "ops": [
    {"op": "set-nvs", "partition": "nvs_cfg", "set": {"cfg:channel": "string:stable"}},
    {"op": "edit-fs", "partition": "storage", "put": {"/config.json": "files/config.json"}},
    {"op": "clear-boot"}
  ]
}
```

All fields are optional. `chip` is checked against the device. `ops` run in
order after the files:

| Op | Fields | Does |
|----|--------|------|
| `write` | `partition`, `file` | Write a file to a partition. |
| `erase` | `partition` | Erase a partition. |
| `write-fs` | `partition`, `file` | Write a filesystem image. |
| `edit-fs` | `partition`, `put`, `delete` | Put files (path → file in the bundle) in the filesystem, and delete paths. |
| `set-nvs` | `partition`, `file`, `set`, `delete` | Set the keys in `file` (an NVS CSV), set keys (`ns:key` → `type:value`, or a bare value for a key that exists) and delete them. Without `partition`, the first NVS partition. |
| `set-boot` | `partition` | Boot that OTA slot next. |
| `clear-boot` | | Clear otadata so the factory app boots. |

### Partition table rules

A table that already matches the device's isn't written. `table` says what
happens when it differs:

- `update` (default): write it.
- `ask`: show the differences and ask. `-y` answers yes.
- `require`: never write it, and refuse the device.

`tableMatch` says which differences count: `exact` (default) for any, or `used`
for only the partitions the bundle writes, erases, edits or boots. With `used`,
a device that differs elsewhere keeps its own table.

## `create-bundle`

Pack partition binaries into a bundle. `@factory` and `@ota` add a role file.
`--flash-partition-table` includes the partition table CSV so
[`write-bundle`](#write-bundle) flashes it too. `--manifest` adds a
`manifest.json` and the files its ops name, relative to it.

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

Flash a bundle. It prints the steps first, then checks everything against the
device before the first write. `--hmac-key` is the key for an
[encrypted NVS](nvs.md#encrypted-nvs) partition that a `set-nvs` op edits;
bundles never carry keys.

```bash
idftool write-bundle release.zip
```

Takes the [write options](write-options.md).

## `print-bundle`

Inspect a bundle without a device: its manifest, the steps flashing it takes,
its bootloader's chip, its partition table if it has one, and the app
descriptions.

```console
$ idftool print-bundle -f release.zip
Bundle: release.zip (0x2828d bytes)
Partition table: partition_table.csv
Steps:
  1. Write the partition table (partition_table.csv) if it differs
  2. Write ota_0.bin to partition 'ota_0'
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
