# Filesystems

`idftool` builds, flashes, lists, and extracts the three filesystems ESP-IDF
mounts from a data partition.

| Filesystem | Partition subtype | Built with |
|------------|-------------------|------------|
| `fatfs` | `fat` | [pyfatfs](https://github.com/nathanhi/pyfatfs), plus ESP-IDF's wear levelling layer |
| `littlefs` | `littlefs` | [littlefs-python](https://github.com/jrast/littlefs-python), the library [esp_littlefs](https://github.com/joltwallet/esp_littlefs) itself uses |
| `spiffs` | `spiffs` | ESP-IDF's `spiffsgen.py`, vendored, plus a reader ESP-IDF doesn't ship |

**Which filesystem?** From `--type` if given, otherwise from the partition's
subtype, otherwise from what the image looks like. So
`write-fs storage assets/` on a `spiffs` partition needs no `--type`, and
`print-fs -f storage.bin` works out the format itself.

**Wear levelling.** ESP-IDF mounts a `fat` partition through its wear
levelling layer, which reserves sectors and shifts the filesystem as writes
accumulate. `idftool` wraps FAT images in that layer by default and unwraps them
on read, including images the device has written to. `--no-fat-wear-levelling`
gives a bare image, e.g. for a partition mounted with
`esp_vfs_fat_spiflash_mount_ro`.

**Matching the device's sdkconfig.** The per-filesystem options default to
ESP-IDF's Kconfig defaults, so they only need setting when the device differs:
`--littlefs-name-max` for `CONFIG_LITTLEFS_OBJ_NAME_LEN`, `--spiffs-page-size`
for `CONFIG_SPIFFS_PAGE_SIZE`, `--fat-sector-size` for `CONFIG_WL_SECTOR_SIZE`.
`idftool create-fs --help` lists them all.

!!! note "SPIFFS is flat"
    SPIFFS has no directories. A file's whole path counts against
    `CONFIG_SPIFFS_OBJ_NAME_LEN`, 32 characters by default.

## `create-fs`

Build a filesystem image from a directory, offline. Needs `--size`, or
`--partition` to take the size (and filesystem) from the partition table.

```bash
idftool create-fs assets/ -o storage.bin --size 0x100000 --type littlefs
idftool --partition-table-file partitions.csv create-fs assets/ -o storage.bin --partition storage
```

## `write-fs`

Build a filesystem image from a directory and flash it, sized to fill the
partition. The filesystem comes from the partition's subtype unless `--type`
says otherwise; see [which filesystem](#filesystems). A file that's already a
filesystem image is flashed as-is, padded to the partition.

```bash
idftool write-fs storage assets/
idftool write-fs storage prebuilt-storage.bin
idftool write-fs storage assets/ --type littlefs
```

Takes the [write options](write-options.md).

## `read-fs`

Read a filesystem partition off the device and extract it into a directory.

```bash
idftool read-fs storage ./storage-backup
```

## `extract-fs`

Extract a filesystem image file into a directory, offline.

```bash
idftool extract-fs -f storage.bin ./storage-backup
```

## `print-fs`

List the contents of a filesystem partition or image. Alias: `list-fs`.

```bash
idftool print-fs storage              # from the device
idftool print-fs -f storage.bin       # from a file
```

```console
$ idftool print-fs -f storage.bin
'storage.bin': littlefs, 0x10000 bytes
╭────────────────┬───────╮
│ Path           │  Size │
├────────────────┼───────┤
│ config.json    │     8 │
│ www            │ <dir> │
│ www/index.html │    12 │
╰────────────────┴───────╯
2 files, 20 bytes, 1 directory
```
