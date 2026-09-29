# Bundles

A bundle is a plain ZIP with one `.bin` per partition, named after the
partition, and optionally a `partition_table.csv`. Use them to hand a build
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
`partition_table.csv`. Without a filename it's named
`{chip}-{mac}-{timestamp}.zip`.

```bash
idftool dump-bundle                 # auto-named
idftool dump-bundle my-backup.zip
```

## `write-bundle`

Flash every binary in a bundle. If the bundle has a `partition_table.csv`,
idftool uses it instead of the device's table, and rewrites the device's table
to match.

```bash
idftool write-bundle release.zip
```

Takes the [write options](../guide/write-options.md).

## `print-bundle`

Inspect a bundle without a device: its partition table and, for each app
partition present, the app description.

```bash
idftool print-bundle -f release.zip
```
