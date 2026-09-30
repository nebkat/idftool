# Partition I/O

Read, write, and erase partitions by name. All four accept the
[addressing](../guide/partition-addressing.md) syntax for slices and offsets.

## `read`

Read a partition, or a slice of one, into a file.

```bash
idftool read nvs nvs.bin
idftool read 'storage[-0x1000:]' tail.bin
```

## `write`

Write files to partitions. Arguments come in `PARTITION FILE` pairs; repeat
them to flash several partitions in one go. Each file is checked to fit its
partition before anything is written, and a `bootloader` file is checked
against the chip.

```bash
idftool write ota_0 build/app.bin storage build/spiffs.bin
```

Takes the [write options](write-options.md).

## `erase`

Erase a partition, or a slice of one.

```bash
idftool erase nvs
idftool erase 'storage[0:0x1000]'
```

## `view`

Print a partition's contents. Hex dump by default; `-s` for UTF-8 text, `-w`
to set the dump width.

```bash
idftool view nvs
idftool view nvs -w 32
idftool view 'log[0:0x2000]' -s
```
