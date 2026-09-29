# NVS

`idftool` builds NVS (Non-Volatile Storage) images from the same CSV format as
ESP-IDF's
[`nvs_partition_gen.py`](https://docs.espressif.com/projects/esp-idf/en/latest/api-reference/storage/nvs_partition_gen.html),
and reads, queries, and edits them on the device or as files.

The CSV has one row per namespace or key, under a
`key,type,encoding,value` header:

- A `namespace` row starts a namespace. The keys below it belong to it.
- A `data` row is a key with an encoding (`u8` to `u64`, `i8` to `i64`,
  `string`, `hex2bin`, `base64`, or `binary`) and its value.
- A `file` row is the same, but the value is read from the file named.

This example makes a namespace `storage` holding three keys:

```csv title="example.csv"
key,type,encoding,value
storage,namespace,,
device_name,data,string,My Device
device_id,data,u32,12345
api_key,data,string,abc123def456
```

Don't pad the columns with spaces: `nvs_partition_gen` doesn't accept them in
the header.

!!! note
    Encrypted NVS isn't supported yet. Every command here works on plaintext
    NVS only.

## `create-nvs`

Build an NVS image from a CSV, offline. Needs a size: `--size`, or
`--partition` to take it from the partition table (which needs
`--partition-table-file` or a device).

```bash
idftool create-nvs example.csv -o nvs.bin --size 0x6000
idftool --partition-table-file partitions.csv create-nvs example.csv -o nvs.bin --partition nvs
```

## `write-nvs`

Build an NVS image from a CSV and flash it to a partition.

```bash
idftool write-nvs nvs example.csv
```

Takes the [write options](write-options.md).

## `print-nvs`

List the keys and values in an NVS partition or image. `--pages` adds the page
map: each page's state, sequence number, and how many of its 126 entries are
written or erased. Alias: `list-nvs`.

```bash
idftool print-nvs nvs                 # from the device
idftool print-nvs -f nvs.bin          # from a file
idftool print-nvs -f nvs.bin --pages
```

```console
$ idftool print-nvs -f nvs.bin --pages
'nvs.bin': NVS version 2, 0x6000 bytes
╭──────┬─────┬───────────────┬───────┬─────────────────────╮
│ Page │ Seq │ State         │ Used  │ Entries             │
├──────┼─────┼───────────────┼───────┼─────────────────────┤
│ 0    │ 0   │ active        │ 6/126 │ 6 written, 0 erased │
│ 1    │ -   │ uninitialised │       │                     │
│ 2    │ -   │ uninitialised │       │                     │
│ 3    │ -   │ uninitialised │       │                     │
│ 4    │ -   │ uninitialised │       │                     │
│ 5    │ -   │ uninitialised │       │                     │
╰──────┴─────┴───────────────┴───────┴─────────────────────╯

╭───────────┬─────────────┬────────┬──────────╮
│ Namespace │ Key         │ Type   │ Value    │
├───────────┼─────────────┼────────┼──────────┤
│ config    │ sample_rate │ u32    │ 100      │
│ config    │ serial      │ string │ SN-0042  │
│ config    │ wifi_ssid   │ string │ Workshop │
╰───────────┴─────────────┴────────┴──────────╯
3 entries in 1 namespace, 21 bytes of data
```

## `extract-nvs` / `read-nvs`

Dump NVS back out as a CSV that feeds straight into `create-nvs`.
`extract-nvs` reads an image file, `read-nvs` reads the device.

```bash
idftool extract-nvs -f nvs.bin nvs.csv
idftool read-nvs nvs nvs.csv
```

## `get-nvs`

Print the values of one or more keys, one per line and nothing else, so they
can be captured in a shell. A key is `namespace:key`, or just `key` with
`--namespace`. Blobs print as hex, or raw bytes with `--raw`.

```bash
idftool get-nvs nvs storage:device_id
idftool get-nvs nvs -n storage device_name device_id
idftool get-nvs nvs storage:cert --raw > cert.der
idftool get-nvs -f nvs.bin storage:device_id

serial=$(idftool get-nvs nvs storage:device_id)
```

Only values go to stdout. Progress, the partition table, and everything else go
to stderr, even when reading from a device.

## `set-nvs`

Set or delete keys in an existing NVS partition or image, without rebuilding it
from a CSV.

```bash
idftool set-nvs nvs storage:device_name="My Device"
idftool set-nvs nvs storage:serial:string=SN-0001 -d storage:old_key
idftool set-nvs nvs -n storage :cert:blob=@device.der
idftool set-nvs -f nvs.bin storage:device_id=42
idftool set-nvs nvs storage:device_id=42 --dry-run
```

`namespace:key=value`
:   Change an existing key. Its type is kept.

`namespace:key:type=value`
:   Add a key that isn't there yet, with its type.

`key=value`
:   With `--namespace` set.

`:key:type=value`
:   A leading colon means the default namespace.

`=@FILE`
:   Read the value from a file.

`-d namespace:key`
:   Delete a key.

Changes are **appended** the way firmware writes them: a new entry goes into
free space and the old one is marked erased. Everything else stays
byte-for-byte identical, and on a device only the 4 KiB pages that changed are
rewritten.

When there's no free space left, firmware would garbage-collect; `set-nvs`
instead rebuilds a compacted image and says so. `--rewrite` does that on
purpose, which also reclaims the space erased entries still take up.
