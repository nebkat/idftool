# Partitions or files

Most commands work on either a device or a local file. Which one follows a
single rule:

!!! abstract "The rule"
    A command's **subject** (the thing it inspects or modifies) is a
    `PARTITION` positional, or `-f FILE`.
    **Payloads** (files pushed to the device) and **outputs** (`-o`) stay
    positional.

So the subject always comes first, and `-f` points it at a file instead of the
chip:

```bash
idftool print-nvs nvs                   # the nvs partition on the device
idftool print-nvs -f nvs.bin            # a local image file
idftool set-nvs nvs storage:id=42       # edit the device
idftool set-nvs -f nvs.bin storage:id=42
```

Commands whose subject can only be a file take `-f` too, so there's nothing to
remember per command: `print-image -f`, `print-bundle -f`, `print-table -f`,
`extract-fs -f`, `extract-nvs -f`, `app-info -f`.

Files that are *not* the subject keep their positional slot, because they have
no device alternative: the payload in `write-table partitions.csv`,
`factory app.bin`, `ota app.bin`, and the inputs to `create-*` and
`create-table`.

Passing a file where a partition belongs is caught before connecting, so you
get a usage error instead of a serial timeout:

```console
$ idftool print-nvs nvs.bin
Error: 'nvs.bin' is a file, not a partition name — use `idftool print-nvs -f nvs.bin`,
or name the partition to read from the device
```

## Offline use

With `--partition-table-file`, commands that only need the partition layout run
without a device: `list`, `create-image`, `create-bundle`, `create-nvs`,
`create-fs`.

```bash
idftool --partition-table-file partitions.csv create-bundle \
  -o release.zip ota_0 build/app.bin
```
