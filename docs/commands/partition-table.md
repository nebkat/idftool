# Partition table

These work on the partition table itself. That's different from the global
`--partition-table-file` option, which sets the layout *other* commands use to
find partitions by name.

The input format (CSV or binary) is detected automatically. The output format
comes from the file extension (`.csv` or `.bin`), or `--format`.

When a CSV has a `bootloader` row, pass `--primary-bootloader-offset` (an
offset, or a chip name like `esp32s3`) so its offset can be resolved.

## `print-table`

Print a partition table. With `-f` it reads a file, offline; otherwise it reads
the device (or `--partition-table-file`). Alias: `list`.

```bash
idftool list                               # from the device
idftool print-table -f partitions.bin      # from a file
idftool --partition-table-file partitions.csv print-table
```

From a device, idftool also reads each app partition's description and marks
the running OTA slot. Next to otadata it shows which copy is in use: `ota (A)`,
`ota (B)`, or `ota (invalid)` when otadata is erased.

On a terminal the table is boxed and coloured, with the active app marked
`● active`:

```text
╭──────────┬──────┬─────────┬──────────┬──────┬────────────────────────╮
│ Name     │ Type │ Subtype │   Offset │ Size │ App                    │
├──────────┼──────┼─────────┼──────────┼──────┼────────────────────────┤
│ nvs      │ data │ nvs     │   0x9000 │  16K │                        │
│ otadata  │ data │ ota (A) │   0xd000 │   8K │                        │
│ ota_0    │ app  │ ota_0   │  0x10000 │   1M │ my-app v1.0.0          │
│ ota_1    │ app  │ ota_1   │ 0x110000 │   1M │ my-app v1.1.0 ● active │
╰──────────┴──────┴─────────┴──────────┴──────┴────────────────────────╯
```

Piped or redirected, it's a plain Markdown table with the active app marked
`*`, so scripts see a stable format:

```text
| Name    | Type | Subtype | Offset   | Size | App description |
|---------|------|---------|----------|------|-----------------|
| nvs     | data | nvs     | 0x9000   | 16K  |                 |
| otadata | data | ota (A) | 0xd000   | 8K   |                 |
| ota_0   | app  | ota_0   | 0x10000  | 1M   | my-app v1.0.0   |
| ota_1   | app  | ota_1   | 0x110000 | 1M   | my-app v1.1.0 * |
```

## `create-table`

Convert a partition table between CSV and binary, offline, in either
direction. The binary includes the MD5 checksum and padding, ready to flash or
embed. Alias: `convert-table`.

```bash
idftool create-table partitions.csv partitions.bin
idftool create-table partitions.bin partitions.csv
idftool --primary-bootloader-offset esp32s3 create-table partitions.csv partitions.bin
```

There's no `-f` short form for `--format` here: `-f` means `--file` everywhere
else, and both of this command's files are positional.

## `dump-table`

Read the device's partition table into a file. CSV with an automatic name by
default.

```bash
idftool dump-table                       # auto-named .csv
idftool dump-table backup.bin
```

## `write-table`

Flash a partition table from a CSV or binary file. The table is verified first;
`--force` flashes it anyway.

```bash
idftool write-table partitions.csv
idftool write-table partitions.bin --force
```

!!! warning
    Only the table is replaced. Partition data isn't moved, resized, or
    erased, so a table that no longer matches what's in flash can leave the
    device unbootable.
