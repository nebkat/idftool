# idftool

A CLI tool built on top of [`esptool`](https://github.com/espressif/esptool)
that is ESP-IDF partition aware.

## Why idftool?

`esptool` is a generic tool for flashing Espressif modules that works with
arbitrary flash addresses. It does not take into the ESP-IDF partition
table or OTA mechanism, making it unduly difficult to perform simple tasks
such as flashing a new firmware binary or pulling log data from a device -
especially when dealing with multiple devices/partition tables.

`idftool` offers:

- **Partition-name addressing.** Read, write, erase, or hex-dump a
  partition by name (`nvs`, `ota_0`, `storage`, …) — and grab slices of it
  with a `name[start:stop]` syntax.
- **OTA slot management.** Inspect the active slot, switch slots, or roll
  back to factory without hand-computing otadata offsets. `idftool list`
  marks the running OTA partition right in the table.
- **Improved safety features.** Writes are checked to ensure they do not
  overflow the target partition. Firmware binaries are checked to ensure
  compatibility with the chip being flashed.
- **Reproducible multi-binary flashing.** `create-bundle` packs multiple
  partition binaries into a single ZIP; optionally flashing a partition
  table. `write-bundle` reflashes the lot in one command.
- **Filesystems, both ways.** Build a FAT, littlefs, or SPIFFS image from a
  directory and flash it (`write-fs`), or pull one back off the device and
  extract it (`read-fs`) — wear levelling and all. The filesystem is inferred
  from the partition's subtype, so you rarely have to say which.

## Installation

### pipx (recommended)

[pipx](https://pipx.pypa.io) installs idftool into an isolated environment and
puts it on your `PATH` automatically:

```bash
pipx install idftool
```

Install pipx first if you don't have it:

| Platform | Command |
|----------|---------|
| macOS | `brew install pipx` |
| Windows | `winget install python.pipx` |
| Linux / other | `pip install pipx` |

### Binary

If you don't have Python, download the pre-built binary from the
[Releases](https://github.com/nebkat/idftool/releases) page. Prefer the `-dir`
archive over the single-file download — it starts instantly rather than extracting
itself on each run.

## At a glance

```bash
$ idftool devices
/dev/cu.usbmodem1101 || USB JTAG/serial debug unit || USB VID:PID=303A:1001 SER=...

$ idftool list
| Name     | Type | Subtype  | Offset   | Size | App description |
|----------|------|----------|----------|------|-----------------|
| nvs      | data | nvs      | 0x9000   | 16K  |                 |
| otadata  | data | ota (A)  | 0xd000   | 8K   |                 |
| phy_init | data | phy      | 0xf000   | 4K   |                 |
| ota_0    | app  | ota_0    | 0x10000  | 1M   | my-app v1.0.0   |
| ota_1    | app  | ota_1    | 0x110000 | 1M   | my-app v1.1.0 * |
| storage  | data | spiffs   | 0x210000 | 1M   |                 |

# The trailing `*` marks the currently-active OTA app

$ idftool ota build/my-app.bin
Writing 'my-app v1.2.0' to partition 'ota_0'...
Setting boot partition to 'ota_0'...

$ idftool write nvs my-nvs.bin
Writing file my-nvs.bin (size=0x4000) to partition nvs (offset=0x9000, size=0x4000)

$ idftool set-boot ota_1
Setting boot partition to 'ota_1'...
```

---

## Command reference

| Command | Description |
|---------|-------------|
| **Discovery** | |
| [`devices`](#devices) | List serial ports with hardware IDs |
| [`list`](#print-table) | Print the partition table (alias of `print-table`) |
| **Partition I/O** | |
| [`read`](#read) | Read a partition (or slice) into a file |
| [`write`](#write) | Write one or more files to named partitions |
| [`erase`](#erase) | Erase a partition (or slice) |
| [`view`](#view) | Pretty-print a partition's contents |
| **Firmware** | |
| [`ota`](#ota) | Push an app to the next OTA slot and switch to it |
| [`factory`](#factory) | Flash an app to the factory partition |
| **Boot selection** | |
| [`get-boot`](#get-boot) | Show the currently-active OTA slot |
| [`set-boot`](#set-boot) | Force the next boot to a specific OTA partition |
| [`clear-boot`](#clear-boot) | Erase otadata and let the bootloader fall back |
| **Images** | |
| [`create-image`](#create-image) | Merge partition binaries into a single flash image |
| [`dump-image`](#dump-image) | Dump the entire flash to an image file |
| [`write-image`](#write-image-alias-reflash) | Write a full flash image to the device |
| [`print-image`](#print-image) | Print partition table and app info from a flash image file |
| **Bundles** | |
| [`create-bundle`](#create-bundle) | Pack partition images into a ZIP bundle |
| [`dump-bundle`](#dump-bundle) | Pack every partition from the device into a ZIP |
| [`write-bundle`](#write-bundle) | Flash every binary in a bundle ZIP |
| [`print-bundle`](#print-bundle) | Print partition table and app info from a bundle ZIP |
| **Partition table** | |
| [`create-table`](#create-table) | Convert a partition table file between CSV and binary |
| [`print-table`](#print-table) | Print a partition table from a CSV or binary file, or the device |
| [`dump-table`](#dump-table) | Read the partition table from the device into a file |
| [`write-table`](#write-table) | Flash a partition table from a CSV or binary file |
| **NVS** | |
| [`create-nvs`](#create-nvs) | Generate an NVS partition image from a CSV file |
| [`write-nvs`](#write-nvs) | Generate an NVS image from CSV and flash it |
| [`read-nvs`](#read-nvs) | Read an NVS partition from the device and extract it to CSV |
| [`extract-nvs`](#extract-nvs) | Extract an NVS image file to a CSV file |
| [`print-nvs`](#print-nvs) | List the contents of an NVS partition or image |
| [`get-nvs`](#get-nvs) | Print the value of one or more keys in an NVS partition or image |
| [`set-nvs`](#set-nvs) | Set or delete keys in an NVS partition or image |
| **Filesystems** | |
| [`create-fs`](#create-fs) | Build a filesystem image from a directory |
| [`write-fs`](#write-fs) | Build a filesystem image from a directory and flash it |
| [`read-fs`](#read-fs) | Read a filesystem partition and extract it to a directory |
| [`extract-fs`](#extract-fs) | Extract a filesystem image file to a directory |
| [`print-fs`](#print-fs) | List the contents of a filesystem partition or image |
| **Misc** | |
| [`enter-bootloader`](#enter-bootloader) | Drop the chip into ROM bootloader mode |

### Discovery

#### `devices`
List the serial ports the host can see, with their descriptions and USB
hardware IDs.
```text
idftool devices
```

### Partition I/O

#### `read`
Read a partition (or slice) into a file.
```text
idftool read nvs nvs.bin
idftool read 'storage[-0x1000:]' tail.bin
```

#### `write`
Write one or more files to named partitions. Arguments come in
`PARTITION FILENAME` pairs; you can repeat them to flash several
partitions atomically.
```text
idftool write ota_0 build/app.bin storage build/spiffs.bin
```

#### `erase`
Erase a partition (or slice of one).
```text
idftool erase nvs
```

#### `view`
Pretty-print a partition's contents. Hex dump by default; pass `-s` for
UTF-8 string mode and `-w` to tweak the dump width.
```text
idftool view nvs
idftool view nvs -w 32
idftool view nvs -s
```

### Firmware

#### `ota`
Push a new app image to the **next** OTA slot, then set it as the boot
slot. idftool figures out which slot is next from otadata, writes the
image, and bumps the OTA sequence counter — exactly what an OTA update
from the firmware would do, just over USB.
```text
idftool ota build/my-app.bin
```

#### `factory`
Write an app to the factory partition and erase otadata so the bootloader
falls back to factory on next boot. If the device has no factory
partition, the image is written to `ota_0` instead.
```text
idftool factory build/my-app.bin
```

### Boot selection

#### `get-boot`
Print which OTA slot the bootloader will run on the next reset, along
with the sequence number and OTA state.
```text
idftool get-boot
```

#### `set-boot`
Force the next boot to a specific OTA partition by name (e.g. `ota_0`,
`ota_1`).
```text
idftool set-boot ota_1
```

#### `clear-boot`
Erase the otadata partition. The bootloader's fallback then kicks in: if
a factory partition exists it boots factory, otherwise it boots `ota_0`.
The OTA app images themselves are left untouched.
```text
idftool clear-boot
```

### Images

An image is a single contiguous flash image — everything from the
primary bootloader through the partition table and partitions in one
file. Useful for archiving a known-good snapshot, recovering a bricked
device, or feeding production programmers that can't speak the esptool
protocol.

#### `create-image`
Combine partition images into a single contiguous flash image, offline,
from local files and a partition table.
```text
idftool --partition-table-file partitions.csv create-image \
  -o merged.img --flash-partition-table \
  ota_0 build/app.bin storage build/spiffs.bin
```

#### `dump-image`
Read the entire flash to an image file. If no output filename is given,
the file is named `{chip}-{mac}-{timestamp}.img` (e.g.
`esp32-s3-aabbccddeeff-20260522-143000.img`). Works even when the
on-device partition table is corrupted.
```text
idftool dump-image                 # auto-named
idftool dump-image my-backup.img
```

#### `write-image` (alias: `reflash`)
Erase the entire flash and rewrite it from a flash image. The
counterpart of `dump-image`.
```text
idftool write-image build/full-flash.img
idftool write-image --no-erase --skip-flashed build/full-flash.img
```
The erase is a **full chip erase**, not just the span the image covers,
so anything past the end of the image file goes too. `--no-erase` writes
only what the image contains, and is what `--skip-flashed` needs:
nothing can already match a chip that was just wiped, so asking for both
is refused rather than quietly doing nothing.

An image is written as one region, so `--skip-flashed` here is all or
nothing: the whole span has to match, which it stops doing the moment the
device boots and writes its own NVS. To ask "is this unit already running
this build?", flash the app on its own with
[`factory`](#factory)/[`ota`](#ota)/[`write`](#write) and
`--skip-flashed`, which compares one partition.

#### `print-image`
Inspect a flash image file without touching a device: prints the
partition table embedded in the image and, for each app partition that
contains a valid app, the project name, version, IDF version, compile
time, ELF SHA256, and target chip.
```text
idftool print-image -f build/full-flash.img
```

### Bundles

A bundle is a plain ZIP file containing one `*.bin` per partition (named
after the partition) and, optionally, a `partition_table.csv`. Bundles
are useful for hand-off between build and flash steps and for archiving a
reproducible "this is what shipped" snapshot.

#### `create-bundle`
Pack partition images into a bundle ZIP. Pass `--flash-partition-table`
to embed the partition table CSV so `write-bundle` can also reflash it.
```text
idftool --partition-table-file partitions.csv create-bundle \
  -o release.zip --flash-partition-table \
  ota_0 build/app.bin storage build/spiffs.bin
```

#### `dump-bundle`
Read every partition from the device and pack them into a bundle ZIP,
always including `partition_table.csv`. If no output filename is given,
the file is named `{chip}-{mac}-{timestamp}.zip`.
```text
idftool dump-bundle                  # auto-named
idftool dump-bundle my-backup.zip
```

#### `write-bundle`
Flash every binary in a bundle ZIP. If the bundle contains
`partition_table.csv`, idftool uses it (and rewrites the on-device table
to match) instead of reading the table from flash.
```text
idftool write-bundle release.zip
```

#### `print-bundle`
Inspect a bundle ZIP without touching a device: prints the partition
table from the embedded `partition_table.csv` and, for each app
partition whose `.bin` is present in the bundle, the project name,
version, IDF version, compile time, ELF SHA256, and target chip.
```text
idftool print-bundle -f release.zip
```

### Partition table

These commands work directly on a partition table as a file — the table
is the subject, passed as a positional argument. This is distinct from the
global `--partition-table-file` option, which overrides the layout *other*
commands use to address partitions by name. Input format (CSV or binary)
is auto-detected; output format is inferred from the file extension
(`.csv`/`.bin`) or set with `--format`.

When the input is a CSV that includes a bootloader row, pass
`--primary-bootloader-offset` (an offset or a chip name, e.g. `esp32s3`)
so the offset can be resolved.

#### `create-table`
Convert a partition table between CSV and binary, offline. Handles both
directions; the binary output includes the MD5 checksum and padding, so it
is ready to flash or embed in an image, and converts one back to CSV just as
readily — for a partition table the two directions are the same lossless
operation, which is why one command covers both. Aliased as `convert-table`.

The output format is inferred from the output file's extension; `--format`
overrides it. (There is no `-f` short form here — `-f` means `--file`
everywhere else, and this command's input and output are both positional.)
```text
idftool create-table partitions.csv partitions.bin
idftool create-table partitions.bin partitions.csv
idftool --primary-bootloader-offset esp32s3 create-table partitions.csv partitions.bin
```

#### `print-table`
Pretty-print a partition table. Given a CSV or binary file it runs offline;
with no file it reads the table from the device (or from
`--partition-table-file`). With a connected ESP, idftool also reads the
application descriptor of every app partition (project name, version) and
marks the currently-active OTA slot with a trailing `*`; the selected otadata
copy is shown next to the otadata partition's subtype (`ota (A)`, `ota (B)`,
or `ota (invalid)` when otadata is erased).

Aliased as `list`.
```text
idftool print-table                        # from the device
idftool print-table -f partitions.bin      # from a file, offline
idftool list                               # same thing
idftool --partition-table-file partitions.csv print-table
```

#### `dump-table`
Read the partition table from the connected device and save it. Defaults to
CSV with an auto-generated filename; pass an output file or `--format` to
choose otherwise.
```text
idftool dump-table                       # auto-named .csv
idftool dump-table backup.bin
idftool dump-table backup.csv --format csv
```

#### `write-table`
Flash a partition table from a CSV or binary file to the device. The file is
required, so this never reads the device's current table and writes it back
to itself — use `dump-table` to pull the current table. The table is verified
before flashing (override with `--force`).

Only the partition map (at the partition table offset) is replaced; existing
partition **data** on flash is not moved, resized, or erased, so a table that
no longer matches the flash contents can make the device unbootable.
```text
idftool write-table partitions.csv
idftool write-table partitions.bin --force
```

### NVS

idftool can generate a binary NVS (Non-Volatile Storage) partition image
from a CSV file, using the same format as ESP-IDF's
[`nvs_partition_gen.py`](https://docs.espressif.com/projects/esp-idf/en/latest/api-reference/storage/nvs_partition_gen.html).

Example CSV:
```csv
key,type,encoding,value
storage,namespace,,
device_name,data,string,My Device
device_id,data,u32,12345
api_key,data,string,abc123def456
```

#### `create-nvs`
Generate an NVS partition image from a CSV file, offline. Requires either
`--size` (explicit partition size) or `--partition` (look up the size from
the partition table — needs `--partition-table-file` or a device).
```text
idftool create-nvs nvs.csv -o nvs.bin --size 0x6000
idftool --partition-table-file partitions.csv create-nvs nvs.csv -o nvs.bin --partition nvs
```

#### `write-nvs`
Generate an NVS partition image from a CSV file and flash it to the named
partition on the device.
```text
idftool write-nvs nvs nvs.csv
```

#### `print-nvs`
List the key/value pairs in an NVS image file or in a partition on the
device. `--pages` also prints the page map — each page's state, sequence
number, and how many of its 126 entries are written or erased. Alias:
`list-nvs`.
```text
idftool print-nvs nvs                # from the device
idftool print-nvs -f nvs.bin         # from a file, offline
idftool print-nvs -f nvs.bin --pages
```

#### `extract-nvs` / `read-nvs`
Dump the contents back out as an `nvs_partition_gen` CSV, which feeds
straight back into `create-nvs`. `extract-nvs` reads an image file,
`read-nvs` reads the device.
```text
idftool extract-nvs -f nvs.bin nvs.csv
idftool read-nvs nvs nvs.csv
```

#### `get-nvs`
Print the value of one or more keys, bare and one per line, so they can be
captured in a shell. A key is `namespace:key`, or just `key` with
`--namespace`. Blobs print as hex, or as raw bytes with `--raw`.

Only the values go to stdout — progress, the partition table and the rest go to
stderr — so a capture gets the value and nothing else, even when reading from a
device.
```text
idftool get-nvs nvs storage:device_id
idftool get-nvs nvs -n storage device_name device_id
idftool get-nvs nvs storage:cert --raw > cert.der
idftool get-nvs -f nvs.bin storage:device_id

serial=$(idftool get-nvs nvs storage:device_id)
```

#### `set-nvs`
Set or delete keys in an NVS image that already exists, without rebuilding
it from a CSV. Works on an image file or, with `--partition`, on a live
device.

A spec is `namespace:key=value`. The type comes from the entry being
replaced, so editing an existing key needs no type; a key that isn't there
yet takes one explicitly as `namespace:key:type=value`. A value of `@FILE`
is read from that file. With `--namespace` set, a bare `key=value` works,
and a leading colon means the default namespace (`:key:type=value`).
```text
idftool set-nvs nvs storage:device_name="My Device"
idftool set-nvs nvs storage:serial:string=SN-0001 -d storage:old_key
idftool set-nvs nvs -n storage :cert:blob=@device.der
idftool set-nvs -f nvs.bin storage:device_id=42
idftool set-nvs nvs storage:device_id=42 --dry-run
```

Changes are **appended** the way the firmware writes them: NVS never
rewrites an entry in place, so a new entry goes into free space and the old
one is marked erased. Everything else in the partition stays byte-for-byte
identical, and a device write only re-flashes the 4 KiB pages that actually
changed — the rest of the partition is never erased.

When there is no free space left to append into, the firmware would garbage
collect; `set-nvs` instead rebuilds a compacted image from the parsed
contents and says so. `--rewrite` takes that path deliberately, which is
also how you reclaim the space that erased entries are still occupying.

Encrypted NVS partitions are not supported yet — `set-nvs` and the other
reading commands work on plaintext images only.

### Filesystems

idftool builds, flashes, lists, and extracts the three filesystems ESP-IDF
mounts from a data partition:

| Filesystem | Partition subtype | Built with |
|------------|-------------------|------------|
| `fatfs`    | `fat`             | [`pyfatfs`](https://github.com/nathanhi/pyfatfs), plus ESP-IDF's wear levelling layer |
| `littlefs` | `littlefs`        | [`littlefs-python`](https://github.com/jrast/littlefs-python), the same library [`esp_littlefs`](https://github.com/joltwallet/esp_littlefs) generates images with |
| `spiffs`   | `spiffs`          | ESP-IDF's `spiffsgen.py`, vendored — plus the reader ESP-IDF doesn't ship |

**Which filesystem?** idftool takes it from `--type` if you give one, otherwise
from the partition's subtype, otherwise from what the image looks like. So
`write-fs storage assets/` on a `spiffs` partition needs no `--type`, and
`print-fs -f storage.bin` identifies the image on its own.

**Wear levelling.** ESP-IDF mounts a `fat` partition in SPI flash through the
wear levelling layer, which reserves several sectors of the partition and
shifts the filesystem by a "dummy" sector that migrates as writes accumulate.
idftool wraps FAT images in that container by default and unwraps them
transparently on read — including images the device has written to, where the
filesystem has been shifted and rotated. Pass `--no-fat-wear-levelling` for a
bare image (e.g. for a read-only partition mounted with
`esp_vfs_fat_spiflash_mount_ro`).

**Matching the device's sdkconfig.** The per-filesystem options default to
ESP-IDF's own Kconfig defaults, so they only need setting when the device
differs — e.g. `--littlefs-name-max` for `CONFIG_LITTLEFS_OBJ_NAME_LEN`,
`--spiffs-page-size` for `CONFIG_SPIFFS_PAGE_SIZE`, `--fat-sector-size` for
`CONFIG_WL_SECTOR_SIZE`. Run `idftool create-fs --help` for the full list.

Note that SPIFFS is flat: it has no directories, and a file's whole path
counts against `CONFIG_SPIFFS_OBJ_NAME_LEN` (32 characters by default).

#### `create-fs`
Build a filesystem image from a directory, offline. Requires either `--size`
(explicit image size) or `--partition` (take the size — and the filesystem —
from the partition table).
```text
idftool create-fs assets/ -o storage.bin --size 0x100000 --type littlefs
idftool --partition-table-file partitions.csv create-fs assets/ -o storage.bin --partition storage
```

#### `write-fs`
Build a filesystem image from a directory and flash it to the named partition,
sized to fill it. If the source is a file that already looks like a filesystem
image it's flashed as-is (padded out to the partition) rather than rebuilt.
```text
idftool write-fs storage assets/
idftool write-fs storage prebuilt-storage.bin
idftool write-fs storage assets/ --type littlefs
```

#### `read-fs`
Read a filesystem partition off the device and extract it into a directory.
```text
idftool read-fs storage ./storage-backup
```

#### `extract-fs`
Extract a local filesystem image file into a directory, offline — the
counterpart of `read-fs`.
```text
idftool extract-fs -f storage.bin ./storage-backup
```

#### `print-fs`
List the contents of a filesystem image file, or of a partition on the device.
Aliased as `list-fs`.
```text
idftool print-fs storage             # from the device
idftool print-fs -f storage.bin      # from a file, offline
```

### Misc

#### `enter-bootloader`
Wait for a serial port to appear, then run the BOOT0+RESET dance to drop
the chip into the ROM bootloader (a.k.a. firmware download mode) — and
exit immediately, leaving the device parked for whatever tool you want to
hand it off to. The port path is polled at 50 ms intervals, and transient
errors (e.g. `termios.error: Device not configured` from a tty node that
isn't fully settled) are retried silently.
```text
idftool -p /dev/cu.usbmodem1101 enter-bootloader
```
Requires `-p`/`--port`.

--

## Global options

These flags apply to every subcommand and go **before** the command name:

| Flag | Purpose |
|------|---------|
| `-p`, `--port PATH` | Serial port device. If omitted, idftool auto-picks one. |
| `-b`, `--baud N` | Serial baud rate (defaults to esptool's ROM baud, 115200). |
| `--no-reset` | Skip the hard reset that normally happens after a command. |
| `--partition-table-file PATH` | Use a CSV or binary partition table from disk instead of reading it off the device. |
| `--partition-table-offset OFFSET` | Where to expect the partition table in flash (default `0x8000`). |
| `--partition-table-size SIZE` | Size of the partition table region (default `0x1000`). |
| `--primary-bootloader-offset OFFSET` | Primary bootloader offset, or a chip name like `esp32s3` to pick a default. Only needed when addressing the `bootloader` partition by name in **offline** mode (`--partition-table-file` with no device); auto-detected from a connected chip otherwise. |
| `--recovery-bootloader-offset OFFSET` | Recovery bootloader offset; same scope as `--primary-bootloader-offset`. |

The commands `list`, `create-image`, `create-bundle`, and `create-nvs` will
work **without** a device when you supply `--partition-table-file`; everything
else needs a connected ESP.

## Write options

Every command that writes to flash — `write`, `write-image`, `write-nvs`,
`write-fs`, `write-bundle`, `factory`, `ota` — takes the same set of flags,
which are passed through to esptool's `write_flash`. They go **after** the
command name, and each one left alone keeps esptool's own default.

| Flag | Purpose |
|------|---------|
| `--skip-flashed` | Compare the MD5 of what is already in flash with the data about to be written, and skip the writes that would change nothing. The check is per file (or partition), not per sector, so it is all-or-nothing for each one. |
| `--compress` / `--no-compress` | Compress on the way to the device. On by default, unless the flasher stub is disabled. |
| `--encrypt` | Encrypt the data as it is written. |
| `--force` | Ignore safety and content checks: chip/revision mismatch, secure boot, flash size. |
| `--ignore-flash-enc-efuse` | Ignore the flash encryption eFuse settings. |
| `--no-progress` | Do not print progress while writing. |

`write-image` adds `--erase`/`--no-erase` (on by default), and refuses
`--skip-flashed` unless the erase is off — see
[`write-image`](#write-image-alias-reflash).

`write-table` is the one write command that does not take them: its `--force`
already means "flash a table that failed verification", and a 3 KiB partition
table has nothing to gain from the rest.

The same names work when idftool is driven as a library, along with the
esptool options that take images rather than a yes/no (`diff_with`,
`no_diff_verify`, `encrypt_files`, `erase_all`):

```python
from idftool import write_image, write_nvs

# `state` is an idftool.State holding the connection and the global options
write_image(state, 'flash.img', erase=False, skip_flashed=True)
write_nvs(state, 'nvs', 'provision.csv', no_progress=True)
```

An option esptool does not know is rejected rather than ignored: it reads its
keyword arguments with `kwargs.get`, so a misspelled one would otherwise be a
write that quietly did something else.

## Partitions or files

Most commands can work against a device or against a local file, and which one
they use follows a single rule:

> A command's **subject** — the thing it inspects or modifies — is a
> `PARTITION` positional, or `-f FILE`.
> **Payloads** (files pushed at the device) and **outputs** (`-o`) stay
> positional.

So the subject always comes first, and `-f` is how you point it at a file
instead of the chip:

```text
idftool print-nvs nvs                  # the nvs partition on the device
idftool print-nvs -f nvs.bin           # a local image file
idftool set-nvs nvs storage:id=42      # edit the device
idftool set-nvs -f nvs.bin storage:id=42
```

Commands whose subject can only ever be a file take `-f` too, so there is
nothing per-command to remember: `print-image -f`, `print-bundle -f`,
`print-table -f`, `extract-fs -f`, `extract-nvs -f`.

Files that are *not* the subject keep their positional slot, because there is
no device alternative for them to displace — the payload in
`write-table partitions.csv`, `factory app.bin`, `ota app.bin`, and the inputs
to `create-*` and `create-table`.

Passing a file where a partition belongs is caught **before** connecting, so
you get a usage error instead of a serial timeout:

```text
$ idftool print-nvs nvs.bin
Error: 'nvs.bin' is a file, not a partition name — use `idftool print-nvs -f nvs.bin`,
or name the partition to read from the device
```

## Partition addressing

Wherever a command takes a `partition` argument you can pass:

- A **name** from the partition table (`nvs`, `ota_0`, `storage`, …).
- A **numeric address** that matches an existing partition's start
  offset exactly — equivalent to looking the partition up by name, just
  keyed on its address.
- An **offset into a partition**: `name[offset]`. Negative values count
  from the end. Sets the starting point for the operation. Accepted by
  `write` and `create-image`.
- A **slice of a partition**: `name[start:stop]`. Negative values count
  from the end, and a `+N` stop is a length relative to `start`.
  Accepted by `read`, `erase`, and `view`. Examples:
  - `nvs[0:0x100]` — first 256 bytes of `nvs`
  - `storage[-0x1000:]` — last 4 KiB of `storage`
  - `ota_0[0x1000:+0x800]` — 2 KiB starting 4 KiB into `ota_0`

All numeric values in addresses, offsets, and sizes accept either
decimal (`4096`) or hex (`0x1000`).
